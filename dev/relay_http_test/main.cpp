/*
 * Host-side check for src/relay/http_client.hpp: starts the real relay (the
 * same relay.py the PC runs), drives it with the console's own request shapes,
 * and reports what came back. Built and run by run.sh.
 *
 * Not part of the 3DS build.
 */
#include "http_client.hpp"

#include <cstdio>
#include <string>

static int failures = 0;

static void check(bool ok, const char *what) {
    printf("  [%s] %s\n", ok ? "ok" : "FAIL", what);
    if (!ok) {
        failures++;
    }
}

int main(int argc, char **argv) {
    int port = argc > 1 ? atoi(argv[1]) : 8791;
    http::Client c;
    c.set_target("127.0.0.1", port);

    std::string body;

    // 1. first poll: no epoch yet, so the console omits it
    int code = c.request("/panel.txt?hide=1&wait=900", body, 2100);
    check(code == 200, "first poll answers");
    check(body.find("v\t") != std::string::npos &&
              body.find("end") != std::string::npos,
          "response is a complete panel document");
    check(c.connected(), "connection is kept alive");

    // 2. the same poll again, with the epoch and revision just learned
    long long epoch = -1, rev = -1;
    size_t p = body.find("epoch\t");
    if (p != std::string::npos) {
        epoch = strtoll(body.c_str() + p + 6, NULL, 10);
    }
    p = body.find("rev\t");
    if (p != std::string::npos) {
        rev = strtoll(body.c_str() + p + 4, NULL, 10);
    }
    check(epoch > 0 && rev > 0, "epoch and revision parsed");

    char path[160];
    snprintf(path, sizeof(path), "/panel.txt?epoch=%lld&rev=%lld&hist=0&hide=1&wait=900",
             epoch, rev);
    long long t0 = http::mono_ms();
    code = c.request(path, body, 2100);
    long long held = http::mono_ms() - t0;
    check(code == 200, "long poll answers");
    check(body.find("same") != std::string::npos, "unchanged state says so");
    check(held >= 800 && held < 2000,
          "the relay held the poll open instead of answering at once");
    printf("       (held %lld ms)\n", held);

    // 3. a second request on the same connection
    code = c.request("/state.json", body, 2100);
    check(code == 200 && body.size() > 10 && body[0] == '{',
          "keep-alive reuse works");

    // 4. no relay. On Linux a closed port refuses at once, so this only shows
    // the request fails cleanly; the connect budget that matters (the PC drops
    // packets to a closed port instead) is the deadline in connect_socket().
    http::Client dead;
    dead.set_target("127.0.0.1", 8799);   // nothing listening here
    t0 = http::mono_ms();
    code = dead.request("/panel.txt", body, 2100);
    long long took = http::mono_ms() - t0;
    check(code == 0, "a dead relay reports no response");
    printf("       (failed after %lld ms)\n", took);
    check(took < 1500, "a failed request returns without stalling the caller");

    printf("%s\n", failures ? "FAILURES" : "all good");
    return failures ? 1 : 0;
}
