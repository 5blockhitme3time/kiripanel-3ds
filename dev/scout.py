#!/usr/bin/env python3
"""
Scout an unknown KiriKiri game for writing its adapter.

    python scout.py GAME_DIR [--exe NAME] [--at 15,45] [--keep]

Installs the panel plugin into GAME_DIR for the duration (only files that
are not there already; removed again afterwards unless --keep), starts the
game with -datapath pointing at an EMPTY temporary folder - never the
player's saves - and asks the hook for a scout report at each of the given
seconds after the hook comes up. Reports go to dev/scout/<game>-<n>.txt.

The game window appears while this runs. Nothing is clicked: the reports
show the title screen (and whatever the game does on its own before it).
"""
import argparse
import os
import shutil
import sys
import tempfile
import time

import regress

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "pc"))
from kiripanel import install, relay, report  # noqa: E402

OUT_DIR = os.path.join(HERE, "scout")


def pick_exe(game):
    """The engine the player would run: a patched/cracked build if there is
    one (the original often needs a DRM launcher), else the plainest name."""
    engines = [n for n, _ in install.find_engines(game)]
    if not engines:
        raise SystemExit("no KiriKiri executable in %s" % game)
    for key in ("_chs", "chs", "crack"):
        for n in engines:
            if key in n.lower():
                return n
    return min(engines, key=len)


def render(st, path):
    """_hkscout.ksd -> readable text file."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(report.render(st))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("game")
    ap.add_argument("--exe")
    ap.add_argument("--at", default="12,40",
                    help="seconds after the hook comes up to take reports")
    ap.add_argument("--keep", action="store_true",
                    help="leave the plugin installed afterwards")
    ap.add_argument("--wait", type=float, default=90,
                    help="how long to wait for the hook")
    a = ap.parse_args()
    game = os.path.abspath(a.game)
    exe = os.path.join(game, a.exe or pick_exe(game))
    tag = os.path.basename(game.rstrip("\\/"))
    os.makedirs(OUT_DIR, exist_ok=True)

    engines, steps = install.plan(game, False, False)
    added = []
    for src, dst in steps:
        if src is None:
            continue
        if os.path.exists(dst):
            if not install.ours(dst):
                print("! %s exists and is the game's own; not touching it" % dst)
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        added.append(dst)
    data = tempfile.mkdtemp(prefix="kiriscout_")
    print("game   %s\nexe    %s\ndata   %s (temporary)" % (game, os.path.basename(exe), data))

    g = regress.Game(exe, data, lambda m: print(m, flush=True))
    reports = []
    try:
        g.start()
        try:
            st = g.wait("the hook", lambda s: True, a.wait)
        except regress.Fail as ex:
            print("! %s" % ex)
            print("  kiripanel.log:")
            try:
                with open(os.path.expandvars(r"%LOCALAPPDATA%\kiripanel\kiripanel.log"),
                          encoding="utf-8") as f:
                    for line in f.readlines()[-6:]:
                        print("    " + line.rstrip())
            except OSError:
                pass
            return 1
        print("hook up: loader=%s adapter=%s loc=%s"
              % (st.get("loader"), st.get("adapter"), st.get("loc")))
        t0 = time.time()
        for i, at in enumerate(float(x) for x in a.at.split(",")):
            while time.time() - t0 < at:
                if g.proc.poll() is not None:
                    raise regress.Fail("game exited")
                time.sleep(0.2)
            res = g.cmd("scout", timeout=30)
            src = os.path.join(data, "_hkscout.ksd")
            if res != "scouted" or not os.path.exists(src):
                print("! scout -> %s" % res)
                continue
            out = os.path.join(OUT_DIR, "%s-%d.txt" % (tag, i + 1))
            # the raw report too, for when it does not parse
            shutil.copyfile(src, out[:-4] + ".ksd")
            with open(src, "rb") as f:
                text = relay.decode_struct_bytes(f.read())
            try:
                rep = relay.parse_struct(text)
            except relay.StructError as ex:
                at = int(str(ex).rsplit(" ", 1)[-1]) if str(ex)[-1].isdigit() else 0
                print("! report does not parse: %s; near: %r"
                      % (ex, text[max(0, at - 120):at + 60]))
                continue
            render(rep, out)
            reports.append(out)
            print("report %s  (adapter=%s loc=%s raw=%s)"
                  % (out, g.st.get("adapter"), g.st.get("loc"), g.st.get("rawLoc")))
    except regress.Fail as ex:
        print("! %s" % ex)
    finally:
        g.stop()
        time.sleep(1)
        if not a.keep:
            for p in reversed(added):
                try:
                    os.remove(p)
                except OSError as ex:
                    print("! could not remove %s: %s" % (p, ex))
            install.remove_empty_dirs(game)
        shutil.rmtree(data, ignore_errors=True)
    return 0 if reports else 1


if __name__ == "__main__":
    sys.exit(main())
