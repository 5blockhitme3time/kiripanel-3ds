#!/usr/bin/env python3
"""
Make a hardlinked copy of a game folder without any kiripanel files, to test
an install layout without touching the real folder.

    python mirror.py GAME_DIR DEST [--skip NAME ...]

Every game file is a hard link (same volume, no extra space). Files ours by
install.ours() and the given --skip names (relative paths) are left out.
Deleting DEST later only removes the links, never the game's files.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "pc"))
from kiripanel import install  # noqa: E402

# Small files only: install.ours() reads the whole file.
OURS_MAX = 4 * 1024 * 1024


def make(game, dest, skip=(), log=print):
    """Link `game` into `dest` (which must not exist). Returns (linked, left)."""
    game, dest = os.path.abspath(game), os.path.abspath(dest)
    if os.path.exists(dest):
        raise SystemExit("%s exists; remove it first" % dest)
    skip = {os.path.normcase(os.path.normpath(s)) for s in skip}
    n = left = 0
    for dp, dns, fns in os.walk(game):
        rel_dir = os.path.relpath(dp, game)
        dns[:] = [d for d in dns
                  if os.path.normcase(os.path.normpath(os.path.join(rel_dir, d))) not in skip
                  and d.lower() != "kiripanel"]
        os.makedirs(os.path.join(dest, rel_dir), exist_ok=True)
        for fn in fns:
            rel = os.path.normpath(os.path.join(rel_dir, fn))
            src = os.path.join(game, rel)
            if os.path.normcase(rel) in skip or (
                    os.path.getsize(src) < OURS_MAX and install.ours(src)):
                log("left out " + rel)
                left += 1
                continue
            os.link(src, os.path.join(dest, rel))
            n += 1
    log("%d files linked, %d left out -> %s" % (n, left, dest))
    return n, left


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("game")
    ap.add_argument("dest")
    ap.add_argument("--skip", nargs="*", default=[])
    a = ap.parse_args()
    make(a.game, a.dest, a.skip)


if __name__ == "__main__":
    main()
