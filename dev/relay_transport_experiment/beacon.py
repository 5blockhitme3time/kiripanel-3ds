"""
The relay's UDP beacon from the 2026-10-04 transport experiment (not used).

It ran inside relay.py next to the HTTP server, for the console side in
probe.hpp/.cpp: the console asked "is a relay here?" with a datagram and only
dialled once it had an answer, because on the 3DS a connect() to a host that
answers nothing freezes the whole stream for the length of the timeout.
The console side never worked on hardware (see README.txt), so the relay no
longer runs this; it is kept with the rest of the experiment.

The datagram is "kiripanel <port>" in both directions.
"""
import socket
import threading
import time

BEACON_PORT = 8788
BEACON_MAGIC = b"kiripanel "
BEACON_EVERY_S = 1.0


class Beacon(threading.Thread):
    """Answers the console's probe, and announces the relay on the LAN.

    Unicast answers go wherever the stream goes; the broadcast is only an
    announcement, because access points are free to filter broadcast frames
    between wireless clients."""

    daemon = True

    def __init__(self, port, interval=BEACON_EVERY_S):
        super().__init__()
        self.port = port
        self.interval = interval
        self.stopped = threading.Event()
        self.log = print

    def _broadcasts(self):
        """Private IPv4 addresses of this machine, and their /24 broadcasts."""
        addrs = set()
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None,
                                           socket.AF_INET):
                addrs.add(info[4][0])
        except OSError:
            pass
        out = []
        for addr in addrs:
            octets = addr.split(".")
            if len(octets) != 4 or addr.startswith("127."):
                continue
            if addr.startswith(("10.", "192.168.", "172.")):
                out.append(".".join(octets[:3]) + ".255")
        return out

    def run(self):
        payload = BEACON_MAGIC + str(self.port).encode()
        try:
            sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sk.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sk.bind(("", BEACON_PORT))
            sk.setblocking(False)
        except OSError as ex:
            self.log("! beacon: %s" % ex)
            return
        last_announce = 0.0
        try:
            while not self.stopped.is_set():
                # answer a console that is asking whether we are here
                for _ in range(8):
                    try:
                        data, addr = sk.recvfrom(64)
                    except (BlockingIOError, OSError):
                        break
                    if data.startswith(BEACON_MAGIC):
                        try:
                            sk.sendto(payload, addr)
                        except OSError:
                            pass
                        last_announce = time.time()   # it knows where we are now
                now = time.time()
                if now - last_announce >= self.interval:
                    last_announce = now
                    for bcast in self._broadcasts():
                        try:
                            sk.sendto(payload, (bcast, BEACON_PORT))
                        except OSError:
                            pass
                self.stopped.wait(0.05)
        finally:
            sk.close()
