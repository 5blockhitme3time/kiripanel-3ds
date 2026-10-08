#!/usr/bin/env python3
"""
Does the plugin come up when a game is started through its own launcher?
For games whose launcher ignores -datapath, so they can only be tested
with their real save folder: this only WATCHES (no command is sent to the
game), and the save folder is compared with a backup afterwards.

    python launchtest.py GAME_DIR LAUNCHER.exe SAVE_BACKUP_DIR [--wait 35]

The plugin is installed for the run and removed again (unless it was
there before). Message boxes the game shows are listed, not answered.
"""
import argparse
import filecmp
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "pc"))
from kiripanel import compat, install, relay, winproc  # noqa: E402

import dlgclick  # noqa: E402


def diff(a, b):
    c = filecmp.dircmp(a, b)
    out = ["only in backup: " + n for n in c.left_only] + \
          ["new: " + n for n in c.right_only] + ["changed: " + n for n in c.diff_files]
    for sub in c.common_dirs:
        out += [sub + "/" + x for x in diff(os.path.join(a, sub), os.path.join(b, sub))]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("game")
    ap.add_argument("launcher")
    ap.add_argument("backup")
    ap.add_argument("--wait", type=int, default=35)
    ap.add_argument("--answer", help="press this button in message boxes (e.g. 确定)")
    a = ap.parse_args()
    game = os.path.abspath(a.game)
    had = install.ours(os.path.join(game, "version.dll")) or install.ours(os.path.join(game, "mpr.dll"))
    if not had:
        install.apply(game, log=lambda m: None)
    t0 = time.time()
    proc = subprocess.Popen([os.path.join(game, a.launcher)], cwd=game)
    box = None
    seen = set()
    st = None
    try:
        end = t0 + a.wait
        while time.time() < end:
            # a launcher may exit after starting the game, which is then an
            # orphan: keep every pid ever seen
            seen |= winproc.tree(proc.pid)
            pids = seen & set(winproc.processes())
            boxes = winproc.dialogs(pids)
            if boxes and a.answer:
                for pid in pids:
                    if dlgclick.click(pid, a.answer):
                        print("(answered %r)" % a.answer)
                        break
            if boxes and boxes[0] != box:
                box = boxes[0]
                print("message box: %s | %s | %s" % (boxes[0][0], boxes[0][1].replace("\n", " ")[:100], boxes[0][2]))
            ses = relay.read_session()
            if ses and float(ses.get("boot", 0) or 0) > 0 and int(ses.get("pid", 0)) in pids:
                path = os.path.join(ses["datapath"], relay.STATE_NAME)
                try:
                    with open(path, "rb") as f:
                        st = relay.parse_struct(relay.decode_struct_bytes(f.read()))
                except (OSError, relay.StructError):
                    pass
                if st and st.get("adapter") not in (None, "none") and st.get("loc") == "title":
                    break
            time.sleep(0.5)
        pids = seen
        print("processes:", sorted("%d %s" % (p, winproc.processes().get(p, (0, "?"))[1]) for p in pids))
        for ln in compat.log_lines(pids | {proc.pid}, t0):
            print("  log:", ln)
        if st:
            print("hook: loader=%s adapter=%s loc=%s raw=%s err=%s"
                  % (st.get("loader"), st.get("adapter"), st.get("loc"), st.get("rawLoc"), st.get("err")))
            ses = relay.read_session()
            print("save folder the game used:", ses and ses.get("datapath"))
        else:
            print("hook: did not come up")
    finally:
        for pid in seen | {proc.pid}:
            winproc.kill_tree(pid)
        time.sleep(1.5)
        if not had:
            install.apply(game, uninstall=True, log=lambda m: None)
    # what changed in the save folder, then put it back as it was: the hook
    # writes _hk*.ksd there, the game rewrites its system variables
    save = os.path.join(game, "savedata")
    changes = diff(a.backup, save)
    print("save folder changes:", changes or "none")
    for n in (relay.STATE_NAME, relay.CMD_NAME, "_hkscout.ksd"):
        if os.path.exists(os.path.join(save, n)):
            os.remove(os.path.join(save, n))
    shutil.copytree(a.backup, save, dirs_exist_ok=True)
    left = diff(a.backup, save)
    print("after restoring from the backup:", left or "identical")


if __name__ == "__main__":
    main()
