#!/usr/bin/env python3
"""
Read an XP3 archive whose index is zlib-compressed at the offset the header
points to (a layout some localized KAG3 releases use; the plain `File` chunks
at the tail that dev/koihazi/xp3tool.py handles are a different one).

    python xp3zlib.py ARCHIVE names [PATTERN]     one line per file
    python xp3zlib.py ARCHIVE extract PATTERN DIR

Needed to find the scenario and system script names of a game the hook is
being written for, before asking the running game for them with
`dev/probe.py src FILE` (which reads them the way the engine does).
"""
import argparse
import os
import struct
import sys
import zlib

XP3_MAGIC = b"XP3\r\n \n\x1a\x8b\x67\x01"


class Entry:
    __slots__ = ("name", "orig", "comp", "segs")

    def __init__(self, name, orig, comp):
        self.name, self.orig, self.comp = name, orig, comp
        self.segs = []


def read_index(path):
    """The decompressed index of the archive (its File chunks)."""
    fh = open(path, "rb")
    head = fh.read(19)
    if head[:11] != XP3_MAGIC:
        raise ValueError("not an XP3 archive: %r" % head[:11])
    fh.seek(struct.unpack_from("<q", head, 11)[0])
    raw = fh.read(1 << 22)
    # a few bytes of something else sit in front of the zlib stream
    for off in range(64):
        try:
            blob = zlib.decompressobj().decompress(raw[off:])
        except zlib.error:
            continue
        if blob[:4] == b"File":
            return blob
    raise ValueError("no index at the offset the header names")


def parse(blob):
    entries = []
    p = 0
    while p + 12 <= len(blob):
        tag = blob[p:p + 4]
        size = struct.unpack_from("<q", blob, p + 4)[0]
        p += 12
        if tag != b"File":
            break
        end = p + size
        info = segm = None
        while p < end:
            t = blob[p:p + 4]
            n = struct.unpack_from("<q", blob, p + 4)[0]
            p += 12
            if t == b"info":
                info = blob[p:p + n]
            elif t == b"segm":
                segm = blob[p:p + n]
            p += n
        if info is None:
            continue
        here = []
        q = 4                       # info starts with its flags
        while q + 18 <= len(info):
            orig, comp = struct.unpack_from("<qq", info, q)
            q += 16
            nl = struct.unpack_from("<H", info, q)[0]
            q += 2
            e = Entry(info[q:q + nl * 2].decode("utf-16-le", "replace"), orig, comp)
            q += nl * 2
            here.append(e)
            entries.append(e)
        if segm is None:
            continue
        q = 0
        for e in here:              # segments follow their entries in order
            done = 0
            while done < e.comp and q + 28 <= len(segm):
                f, o, osz, csz = struct.unpack_from("<Iqqq", segm, q)
                q += 28
                e.segs.append((o, osz, csz))
                done += csz
    return entries


def read_file(fh, e):
    out = bytearray()
    for o, osz, csz in e.segs:
        fh.seek(o)
        data = fh.read(csz)
        if csz != osz:
            data = zlib.decompress(data)
        out += data
    return bytes(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("archive")
    ap.add_argument("cmd", choices=("names", "extract"))
    ap.add_argument("pattern", nargs="?", default="")
    ap.add_argument("outdir", nargs="?")
    a = ap.parse_args()
    path = a.archive
    entries = [e for e in parse(read_index(path))
               if a.pattern.lower() in e.name.lower()]
    if a.cmd == "names":
        for e in entries:
            print("%-64s %9d %9d" % (e.name, e.orig, e.comp))
        print("%d file(s)" % len(entries))
        return 0
    if not a.outdir:
        raise SystemExit("extract needs an output directory")
    fh = open(path, "rb")
    for e in entries:
        dst = os.path.join(a.outdir, e.name.replace("\\", "/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(read_file(fh, e))
    print("%d file(s) -> %s" % (len(entries), a.outdir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
