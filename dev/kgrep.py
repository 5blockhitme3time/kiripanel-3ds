#!/usr/bin/env python3
"""
grep for the game's scripts, which mix UTF-16LE (with BOM), UTF-8 and
Shift-JIS - ordinary search tools misread most of them.

    python kgrep.py PATTERN [-C N] [--root DIR] [--files]

PATTERN is a regular expression. Default root is out/ (the extracted data and
the Chinese patch).
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def decode(raw):
    if raw[:2] == b"\xff\xfe":
        return raw[2:].decode("utf-16-le", "replace")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8", "replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp932", "replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pattern")
    ap.add_argument("-C", type=int, default=0, help="context lines")
    ap.add_argument("--root", default=os.path.join(HERE, "out"))
    ap.add_argument("--files", action="store_true", help="list files only")
    ap.add_argument("--max", type=int, default=200, help="max matches")
    args = ap.parse_args()
    rx = re.compile(args.pattern)
    shown = 0
    for dirpath, _, names in os.walk(args.root):
        for n in sorted(names):
            if not n.endswith((".ks", ".tjs")):
                continue   # the .utf8.txt copies would double every hit
            path = os.path.join(dirpath, n)
            with open(path, "rb") as f:
                lines = decode(f.read()).splitlines()
            hits = [i for i, l in enumerate(lines) if rx.search(l)]
            if not hits:
                continue
            rel = os.path.relpath(path, args.root)
            if args.files:
                print("%s (%d)" % (rel, len(hits)))
                continue
            last = -1
            for i in hits:
                lo, hi = max(0, i - args.C), min(len(lines), i + args.C + 1)
                if lo > last + 1 and args.C:
                    print("--")
                for j in range(max(lo, last + 1), hi):
                    print("%s:%d%s %s" % (rel, j + 1, ":" if j == i else "-",
                                          lines[j].rstrip()))
                last = hi - 1
                shown += 1
                if shown >= args.max:
                    return


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
