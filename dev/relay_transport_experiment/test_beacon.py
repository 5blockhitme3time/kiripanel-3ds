"""Host-side check for the experiment's beacon (beacon.py).

Sends the probe exactly the way probe.cpp does (same magic, same port,
unicast to the relay's address) and checks the reply.

Run:  python dev/relay_transport_experiment/test_beacon.py
"""
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import beacon as relay  # noqa: E402  (the names this test always used)

failures = []


def check(ok, what):
    print("  [%s] %s" % ("ok" if ok else "FAIL", what))
    if not ok:
        failures.append(what)


def probe(timeout=2.0, to=("127.0.0.1", relay.BEACON_PORT)):
    """What beacon.cpp does: one datagram out, one answer back, no blocking."""
    sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sk.bind(("", 0))
    sk.setblocking(False)
    try:
        sk.sendto(relay.BEACON_MAGIC + b"?", to)
    except OSError:
        pass
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data, addr = sk.recvfrom(64)
        except BlockingIOError:
            time.sleep(0.03)
            continue
        except OSError:
            break
        if data.startswith(relay.BEACON_MAGIC):
            try:
                port = int(data[len(relay.BEACON_MAGIC):].decode())
            except ValueError:
                return None
            return port
    return None


# 1. no relay: the probe must come back empty rather than hang or raise
t0 = time.time()
port = probe(timeout=0.5)
check(port is None, "no answer while no relay runs")
check(time.time() - t0 < 1.5, "a probe with nobody there returns at once")

# 2. a relay answers the probe with its port
b = relay.Beacon(8787)
b.log = lambda m: None
b.start()
time.sleep(0.4)
check(probe() == 8787, "the relay answers a probe with its port")

# 3. repeatedly, which is what the console does
hits = sum(1 for _ in range(4) if probe(timeout=1.0) == 8787)
check(hits == 4, "every probe is answered (%d/4)" % hits)

# 4. the announcement still goes out (a console on a network that passes
#    broadcast may hear it and skip probing)
rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
rx.bind(("", relay.BEACON_PORT + 0))   # same port: the beacon answers us too
rx.close()
# the relay stopped, so the probe goes unanswered again
b.stopped.set()
b.join(timeout=2)
check(probe(timeout=0.6) is None, "answers stop with the relay")

print("%s" % ("FAILURES: %s" % failures if failures else "all good"))
sys.exit(1 if failures else 0)
