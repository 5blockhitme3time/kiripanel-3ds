#!/usr/bin/env python3
"""
Run the manager's compatibility check on games from the command line.

    python compatrun.py GAME_DIR... [--exe NAME] [--answer 否]

--answer: press the button starting with this text in any message box the
game shows (first-start questions); the manager leaves those to the player.
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "pc"))
from kiripanel import compat, games  # noqa: E402

import dlgclick  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("games", nargs="+")
    ap.add_argument("--exe")
    ap.add_argument("--answer")
    a = ap.parse_args()
    rc = 0
    for path in a.games:
        g = games.describe(path)
        if not g:
            print("## %s: no KiriKiri exe" % path)
            continue
        print("## %s  (%s %s %s, exe %s)" % (g["name"], g["engine"], g["version"], g["arch"],
                                            a.exe or g["exe"]))
        c = compat.Check(g, exe=a.exe)
        c.start()
        shown = 0
        while c.is_alive():
            time.sleep(0.5)
            s = c.snapshot()
            for t in s["steps"][shown:]:
                print("   ", t)
            shown = len(s["steps"])
            if a.answer and s["dialog"]:
                for pid in list(c.pids):
                    if dlgclick.click(pid, a.answer):
                        print("    (answered %r)" % a.answer)
                        break
        s = c.snapshot()
        for t in s["steps"][shown:]:
            print("   ", t)
        r = s["result"] or {}
        print("  => %s: %s  [adapter=%s loader=%s title=%s]"
              % (r.get("tier"), r.get("why"), r.get("adapter"), r.get("loader"),
                 r.get("title_reached")))
        if r.get("tier") not in ("ready", "basic"):
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
