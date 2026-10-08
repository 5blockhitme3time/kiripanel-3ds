/*
 * Asks the relay whether it is there - implementation.
 */
#include "probe.hpp"

#include <cstdio>
#include <cstring>

#include <arpa/inet.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <unistd.h>

#include "../diag.hpp"

namespace {

// Must match relay.py.
const char kMagic[] = "kiripanel ";
// Ask twice a second while nothing answers: a relay that starts is noticed
// within about half a second, and a datagram that nobody reads costs nothing.
const u64 kProbeMs = 500;
// Three unanswered probes mean the relay is gone: stop dialling it.
const u64 kStaleMs = 2000;
const u64 kTickMs = 50;

}  // namespace

bool RelayProbe::start(const std::string &host, int port) {
    fd = ::socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) {
        return false;
    }
    memset(&peer, 0, sizeof(peer));
    peer.sin_family = AF_INET;
    peer.sin_port = htons((unsigned short)port);
    if (inet_pton(AF_INET, host.c_str(), &peer.sin_addr) != 1) {
        // not a literal address; nothing to probe without resolving it
        ::close(fd);
        fd = -1;
        return false;
    }
    int flags = fcntl(fd, F_GETFL, 0);
    fcntl(fd, F_SETFL, flags | O_NONBLOCK);
    // libctru assigns a local port on the first send

    running = true;
    worker = threadCreate(thread_main, this, 0x4000, 0x31, -1, false);
    if (!worker) {
        ::close(fd);
        fd = -1;
        running = false;
        return false;
    }
    return true;
}

void RelayProbe::stop() {
    if (worker) {
        running = false;
        threadJoin(worker, U64_MAX);
        threadFree(worker);
        worker = nullptr;
    }
    if (fd >= 0) {
        ::close(fd);
        fd = -1;
    }
}

void RelayProbe::thread_main(void *self) { ((RelayProbe *)self)->run(); }

void RelayProbe::send_probe(u64 now_ms) {
    // The payload is the same shape as the relay's announcement; the relay
    // only checks the prefix.
    const char msg[] = "kiripanel ?";
    ::sendto(fd, msg, sizeof(msg) - 1, 0, (struct sockaddr *)&peer,
             sizeof(peer));
    // A failure here means no route or an ICMP refusal from the last probe;
    // either way it is an answer of "no", and the next probe tries again.
    last_probe_ms = now_ms;
}

void RelayProbe::run() {
    while (running) {
        u64 now_ms = svcGetSystemTick() / (SYSCLOCK_ARM11 / 1000);
        if (!heard && now_ms - last_probe_ms >= kProbeMs) {
            send_probe(now_ms);
        }
        // Drain whatever has arrived. A closed socket or a stale ICMP error
        // reports as a failed read, which is the same as nothing arriving.
        for (int i = 0; i < 8; i++) {
            char buf[64];
            struct sockaddr_in from;
            socklen_t from_len = sizeof(from);
            ssize_t n = ::recvfrom(fd, buf, sizeof(buf), MSG_DONTWAIT,
                                   (struct sockaddr *)&from, &from_len);
            if (n <= 0) {
                break;
            }
            if ((size_t)n > sizeof(kMagic) - 1 &&
                memcmp(buf, kMagic, sizeof(kMagic) - 1) == 0) {
                LightLock_Lock(&lock);
                heard = true;
                answered = true;
                last_ms = now_ms;
                LightLock_Unlock(&lock);
                // one sample per answer: a count in the log means the relay is
                // answering, a dash across the column means it is not
                diag::add(diag::PROBE, 0);
                diag::flush_if_due();
            }
        }
        svcSleepThread(kTickMs * 1000000LL);
    }
}

void RelayProbe::poll() {
    if (!heard) {
        return;
    }
    u64 now_ms = svcGetSystemTick() / (SYSCLOCK_ARM11 / 1000);
    LightLock_Lock(&lock);
    if (heard && now_ms - last_ms > kStaleMs) {
        heard = false;   // the relay stopped answering: stop dialling it
    }
    LightLock_Unlock(&lock);
}

void RelayProbe::forget() {
    LightLock_Lock(&lock);
    heard = false;
    LightLock_Unlock(&lock);
}

bool RelayProbe::available() {
    LightLock_Lock(&lock);
    bool ok = heard;
    LightLock_Unlock(&lock);
    return ok;
}

bool RelayProbe::ever_answered() { return answered; }
