/*
 * Asks the relay whether it is there, so the console never has to dial a host
 * that answers nothing.
 *
 * connect() on this console is a synchronous request to the SOC service, which
 * serves one request at a time: a dial to a host with no relay behind it
 * freezes the video and audio streams for the whole connect timeout, every
 * time it is retried. A UDP datagram costs nothing whether or not anyone
 * listens, so the link asks first and dials only on an answer.
 *
 * The probe is unicast to the relay's address, which is the address the stream
 * already comes from - broadcast would be cheaper still, but access points are
 * free to filter broadcast frames between wireless clients, and one did.
 * Both sockets are non-blocking, so this thread never holds the SOC service
 * either.
 */
#pragma once

#include <3ds.h>

#include <netinet/in.h>

#include <string>

class RelayProbe {
  public:
    RelayProbe() { LightLock_Init(&lock); }
    ~RelayProbe() { stop(); }
    RelayProbe(const RelayProbe &) = delete;
    RelayProbe &operator=(const RelayProbe &) = delete;

    // host is the relay's address, port its UDP probe port. Returns false when
    // the socket cannot be opened or the address is not a literal address; the
    // caller then dials directly and lives with the stutter.
    bool start(const std::string &host, int port);
    void stop();

    // Call from the relay thread. Cheap: the probe thread does the work.
    void poll();

    // The link just failed, so the last answer is no longer evidence that the
    // relay is there. Asking again before dialling keeps a dead relay from
    // costing one more connect attempt.
    void forget();

    // True while the relay has answered recently.
    bool available();

    // True once an answer has ever arrived. A relay that is there but cannot
    // answer - a firewall dropping the probe - would otherwise leave the panel
    // permanently connecting, so the caller gives up waiting on this.
    bool ever_answered();

  private:
    static void thread_main(void *self);
    void run();
    void send_probe(u64 now_ms);

    int fd = -1;
    Thread worker = nullptr;
    volatile bool running = false;
    volatile bool heard = false;
    volatile bool answered = false;   // an answer arrived at least once

    LightLock lock;
    u64 last_ms = 0;        // when the last answer arrived
    u64 last_probe_ms = 0;  // when we last asked
    struct sockaddr_in peer;
};
