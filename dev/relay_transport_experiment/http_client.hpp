/*
 * A tiny HTTP/1.1 client for the relay link - header only.
 *
 * The console's SOC service serves one request at a time, so a socket call
 * that blocks does not just stall the caller: every other socket operation on
 * the console queues behind it. A failed connect is the bad case - this PC
 * answers a connection to a closed port after about two seconds rather than
 * refusing it outright - and curl's blocking connect then froze the video and
 * audio streams for as long as its timeout. That was the stutter.
 *
 * So every call here is non-blocking, and waiting happens in select() with a
 * deadline. The phase called from the relay thread therefore never holds the
 * SOC service for more than the few microseconds a call takes, and the stream
 * threads are never behind it.
 *
 * HTTP is enough for this link: plain text, a handful of responses per second,
 * one connection that is kept alive. It is deliberately not a general client -
 * no chunked responses, no redirects, no cookies.
 */
#pragma once

#include <sys/socket.h>

#include <cerrno>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <time.h>

#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/select.h>
#include <unistd.h>

namespace http {

// The relay is a plain HTTP server on the LAN; no write should ever be this
// slow. Reads get their own, longer budget.
const int kWriteTimeoutMs = 1000;
const int kConnectTimeoutMs = 900;

inline long long mono_ms() {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

class Client {
  public:
    Client() = default;
    ~Client() { close(); }
    Client(const Client &) = delete;
    Client &operator=(const Client &) = delete;

    void set_target(const std::string &host, int port) {
        close();
        this->host = host;
        this->port = port;
    }

    bool connected() const { return fd >= 0 && !must_close; }

    void close() {
        if (fd >= 0) {
            ::close(fd);
            fd = -1;
        }
        must_close = false;
    }

    // GET path. Returns the status code, or 0 when no complete response could
    // be read; body gets the response body either way. A 4xx is a real answer
    // from the relay and is returned as such, so the caller can tell "the
    // relay said no" from "there is no relay". Any failure drops the
    // connection, so the next call reconnects from scratch.
    int request(const std::string &path, std::string &body, int timeout_ms) {
        body.clear();
        if (!ensure_connected()) {
            return 0;
        }
        if (!write_request(path, timeout_ms)) {
            close();
            return 0;
        }
        int code = read_response(body, timeout_ms);
        if (code == 0) {
            close();
            return 0;
        }
        if (must_close) {
            close();
        }
        return code;
    }

    // Convenience for the common case: a body only when the relay said 200.
    bool get(const std::string &path, std::string &body, int timeout_ms) {
        int code = request(path, body, timeout_ms);
        if (code != 200) {
            body.clear();
            return false;
        }
        return true;
    }

  private:
    // A non-blocking connect: EINPROGRESS is the normal outcome, and the wait
    // until the socket is writable happens in select(), not in the SOC call.
    bool connect_socket(long long deadline) {
        struct sockaddr_in sa;
        memset(&sa, 0, sizeof(sa));
        sa.sin_family = AF_INET;
        sa.sin_port = htons((unsigned short)port);
        if (inet_pton(AF_INET, host.c_str(), &sa.sin_addr) != 1) {
            return false;   // the address is resolved by the caller
        }

        int s = ::socket(AF_INET, SOCK_STREAM, 0);
        if (s < 0) {
            return false;
        }
        int flags = fcntl(s, F_GETFL, 0);
        fcntl(s, F_SETFL, flags | O_NONBLOCK);
        int one = 1;
        setsockopt(s, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));

        int rc = ::connect(s, (struct sockaddr *)&sa, sizeof(sa));
        if (rc < 0 && errno != EINPROGRESS && errno != EALREADY) {
            ::close(s);
            return false;
        }
        if (rc < 0) {
            // waits for writability: immediately for a live relay, the whole
            // budget for a dead one, and never inside a socket call
            if (!wait_writable(s, deadline)) {
                ::close(s);
                return false;
            }
            int err = 0;
            socklen_t len = sizeof(err);
            if (getsockopt(s, SOL_SOCKET, SO_ERROR, &err, &len) < 0 || err) {
                ::close(s);
                return false;
            }
        }
        fd = s;
        return true;
    }

    bool ensure_connected() {
        if (fd >= 0 && !must_close) {
            return true;
        }
        close();
        return connect_socket(mono_ms() + kConnectTimeoutMs);
    }

    bool wait_writable(int s, long long deadline) {
        struct timeval tv;
        long long left = deadline - mono_ms();
        if (left < 0) {
            left = 0;
        }
        tv.tv_sec = left / 1000;
        tv.tv_usec = (left % 1000) * 1000;
        fd_set wfds;
        FD_ZERO(&wfds);
        FD_SET(s, &wfds);
        return select(s + 1, NULL, &wfds, NULL, &tv) > 0;
    }

    bool wait_readable(int s, long long deadline) {
        struct timeval tv;
        long long left = deadline - mono_ms();
        if (left < 0) {
            left = 0;
        }
        tv.tv_sec = left / 1000;
        tv.tv_usec = (left % 1000) * 1000;
        fd_set rfds;
        FD_ZERO(&rfds);
        FD_SET(s, &rfds);
        return select(s + 1, &rfds, NULL, NULL, &tv) > 0;
    }

    bool write_request(const std::string &path, int timeout_ms) {
        char hdr[512];
        int n = snprintf(hdr, sizeof(hdr),
                         "GET %s HTTP/1.1\r\n"
                         "Host: %s:%d\r\n"
                         "User-Agent: moonlight-n3ds\r\n"
                         "Accept: */*\r\n"
                         "Connection: keep-alive\r\n"
                         "\r\n",
                         path.c_str(), host.c_str(), port);
        if (n <= 0 || (size_t)n >= sizeof(hdr)) {
            return false;
        }
        size_t sent = 0;
        while (sent < (size_t)n) {
            ssize_t rc = ::send(fd, hdr + sent, (size_t)n - sent, MSG_NOSIGNAL);
            if (rc > 0) {
                sent += (size_t)rc;
                continue;
            }
            if (rc < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                if (!wait_writable(fd, mono_ms() + kWriteTimeoutMs)) {
                    return false;
                }
                continue;
            }
            return false;
        }
        return true;
    }

    // One byte at a time until the headers are complete: the relay's responses
    // are small, and this keeps the state machine trivial.
    bool read_line(std::string &line, long long deadline) {
        line.clear();
        for (;;) {
            char c;
            ssize_t rc = ::recv(fd, &c, 1, 0);
            if (rc == 1) {
                line += c;
                if (c == '\n') {
                    if (line.size() >= 2 && line[line.size() - 2] == '\r') {
                        line.erase(line.size() - 2);
                    } else {
                        line.erase(line.size() - 1);
                    }
                    return true;
                }
                continue;
            }
            if (rc < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                if (!wait_readable(fd, deadline)) {
                    return false;
                }
                continue;
            }
            return false;   // EOF mid-line, or a hard error
        }
    }

    // Returns the status code, or 0 if the response could not be read whole.
    int read_response(std::string &body, int timeout_ms) {
        long long deadline = mono_ms() + timeout_ms;
        std::string line;
        if (!read_line(line, deadline)) {
            return 0;
        }
        // "HTTP/1.1 200 OK"
        int code = 0;
        if (sscanf(line.c_str(), "HTTP/%*d.%*d %d", &code) != 1) {
            return 0;
        }
        long content_length = -1;
        for (;;) {
            if (!read_line(line, deadline)) {
                return 0;
            }
            if (line.empty()) {
                break;   // end of headers
            }
            std::string lower = line;
            for (size_t i = 0; i < lower.size(); i++) {
                lower[i] = (char)tolower((unsigned char)lower[i]);
            }
            if (lower.compare(0, 15, "content-length:") == 0) {
                content_length = strtol(line.c_str() + 15, NULL, 10);
            } else if (lower.compare(0, 10, "connection") == 0 &&
                       lower.find("close") != std::string::npos) {
                must_close = true;
            }
        }
        // The relay always sends a length, including on its error responses.
        if (content_length < 0) {
            return 0;
        }
        body.reserve((size_t)content_length);
        char buf[1024];
        while ((long)body.size() < content_length) {
            size_t want = (size_t)content_length - body.size();
            if (want > sizeof(buf)) {
                want = sizeof(buf);
            }
            ssize_t rc = ::recv(fd, buf, want, 0);
            if (rc > 0) {
                body.append(buf, (size_t)rc);
                continue;
            }
            if (rc < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                if (!wait_readable(fd, deadline)) {
                    return 0;
                }
                continue;
            }
            return 0;
        }
        return code;
    }

    std::string host;
    int port = 0;
    int fd = -1;
    bool must_close = false;   // the response asked for the connection to end
};

}  // namespace http
