#!/usr/bin/env python3
r"""
Real-game regression for the panel hook.

Runs the relay's Hub in-process against a COPY of the save data (-datapath),
starts the game, and drives it only through hook commands - the same path
the 3DS uses. Each scenario starts a fresh game process, because the game's
own menu screens (save/load/settings) can only be left with the mouse.

    python regress.py                  all scenarios
    python regress.py story choice     just these
    python regress.py --list
    python regress.py --game D:\Games\SomeKagExGame --adapter kagex --setup "kag.allskip=1"
                                       another game (see --help)

Where the built-in test games are: dev/testgames.json (copy
testgames.example.json; it stays out of the repository).

The game window appears while this runs; leave it alone.

The scenarios at the end (kag3_*) are for the fallback adapter and run
against a hardlinked mirror of their own game, made on first use; --game
moves them only when they are named on the command line. The choice
scenario needs the played-through save copy in dev/testdata: skipping in
these games stops at text the player has not read yet, so a fresh copy
never reaches a choice in the time the scenario allows.
"""
import argparse
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "pc"))
from kiripanel import relay  # noqa: E402



def test_games():
    """Where the test games are on this machine: dev/testgames.json (not in
    the repository; see testgames.example.json), or the environment
    variables KIRIPANEL_TEST_EXE and KIRIPANEL_KAG3_GAME."""
    conf = {}
    try:
        import json
        with open(os.path.join(HERE, "testgames.json"), encoding="utf-8") as f:
            conf = json.load(f)
    except (OSError, ValueError):
        pass
    return (os.environ.get("KIRIPANEL_TEST_EXE") or conf.get("koihazi_exe", ""),
            os.environ.get("KIRIPANEL_KAG3_GAME") or conf.get("kag3_dir", ""))


# the game the koihazi scenarios (and the default run) use
GAME_EXE, KAG3_SRC = test_games()
DATA_DIR = os.path.join(HERE, "testdata")


class Fail(Exception):
    pass


class Game:
    def __init__(self, exe, datadir, log):
        self.exe = exe
        self.datadir = datadir
        self.log = log
        self.proc = None
        self.acks = {}
        # out of the way of --setup, which numbers its evals from the clock
        self.evalseq = 1000000
        self.hub = relay.Hub(cmd_path=os.path.join(datadir, relay.CMD_NAME))
        self.hub.log = self._hublog
        self.watcher = None

    def _hublog(self, msg):
        # "  cmd #3 qsave -> saved"
        s = msg.strip()
        if s.startswith("cmd #"):
            head, _, res = s.partition(" -> ")
            seq = int(head.split()[1][1:])
            self.acks[seq] = res
        self.log("    relay: " + s)

    def start(self):
        # a devprobe left in the folder would replay its old expression in
        # the game (the probe's own seq starts at 0), and a stale command
        # file would be replayed by the hook
        for n in (relay.STATE_NAME, relay.CMD_NAME, "_kpprobe_in.ksd",
                  "_kpprobe_out.ksd"):
            p = os.path.join(self.datadir, n)
            if os.path.exists(p):
                os.remove(p)
        self.hub.reset_command_file()
        self.watcher = relay.Watcher(os.path.join(self.datadir, relay.STATE_NAME),
                                     self.hub)
        self.watcher.start()
        dp = self.datadir.rstrip("\\/") + "\\"
        self.proc = subprocess.Popen([self.exe, "-datapath=" + dp],
                                     cwd=os.path.dirname(self.exe))

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(10)
        if self.watcher:
            self.watcher.stopped.set()
            self.watcher.join(2)

    @property
    def st(self):
        with self.hub.lock:
            return dict(self.hub.hook or {})

    def wait(self, what, pred, timeout):
        end = time.time() + timeout
        while time.time() < end:
            if self.proc.poll() is not None:
                raise Fail("game exited (code %s) while waiting for %s"
                           % (self.proc.returncode, what))
            try:
                if self.hub.hook_alive() and pred(self.st):
                    return self.st
            except (KeyError, TypeError):
                pass
            time.sleep(0.1)
        raise Fail("timed out waiting for %s; state: loc=%r btn=%r sel=%r "
                   "auto=%r skip=%r tb=%r err=%r"
                   % (what, *(self.st.get(k) for k in
                              ("loc", "btn", "sel", "auto", "skip", "tb", "err"))))

    def cmd(self, name, arg=0, sel=-1, expect=None, timeout=8):
        ok, seq = self.hub.enqueue(name, arg, sel)
        if not ok:
            raise Fail("enqueue %s failed: %s" % (name, seq))
        end = time.time() + timeout
        while time.time() < end:
            if seq in self.acks:
                res = self.acks[seq]
                if expect is not None and res not in (
                        expect if isinstance(expect, tuple) else (expect,)):
                    raise Fail("%s -> %s, expected %s" % (name, res, expect))
                return res
            time.sleep(0.05)
        raise Fail("no ack for %s" % name)

    def eval(self, expr, timeout=10):
        """Run TJS in the game (dev probe; this is how a screen only a mouse
        can leave is left in a test)."""
        import probe
        self.evalseq += 1
        r = probe.probe_eval(self.datadir, self.evalseq, expr, timeout=timeout)
        if r is None or "error" in r:
            raise Fail("eval %r: %s" % (expr, r and (r.get("error") or r.get("value"))))
        return r.get("value")

    def history(self):
        with self.hub.lock:
            return [dict(h) for h in self.hub.history]

    def last_line_id(self):
        h = [x for x in self.history() if x["kind"] == "L"]
        return h[-1]["id"] if h else 0


def btn(bit):
    return lambda st: int(st.get("btn") or 0) & bit


def has_sel(st):
    sel = st.get("sel")
    return sel is not None and sel >= 0


def playing(st):
    return st.get("loc") == "play" and not st.get("dlg")


def at_title(s):
    return s.get("loc") == "title" and int(s.get("btn") or 0) & relay_btn["newgame"]


def to_title(g):
    """Wait for the title menu, tapping through logos and caution screens
    on the way, as a player on the console would."""
    end = time.time() + 90
    while time.time() < end:
        try:
            return g.wait("title menu", at_title, 2.5)
        except Fail:
            if g.proc.poll() is not None:
                raise
            st = g.st
            if st.get("loc") in ("menu", "play") and not st.get("dlg"):
                g.cmd("click")
    return g.wait("title menu", at_title, 1)


relay_btn = {"auto": 1, "skip": 2, "voice": 4, "qsave": 8, "qload": 16,
             "config": 32, "save": 64, "load": 128, "title": 256,
             "continue": 512, "newgame": 1024}


def advance(g, n):
    """n clicks, each must produce a new history line (or a choice)."""
    for _ in range(n):
        before = g.last_line_id()
        g.wait("stable", lambda s: playing(s), 10)
        if has_sel(g.st):
            return
        g.cmd("click", expect="ok")
        # a click either finishes the line being typed or starts the next
        end = time.time() + 6
        while time.time() < end and g.last_line_id() == before:
            if has_sel(g.st):
                return
            time.sleep(0.1)
        if g.last_line_id() == before:
            # first click only completed the typing; one more advances
            g.cmd("click", expect="ok")
            g.wait("a new line", lambda s: g.last_line_id() > before
                   or has_sel(s), 8)


# ---------------------------------------------------------------- scenarios --

def sc_story(g, log):
    """new game, advance, auto on/off, quick save/load, voice, textbox."""
    to_title(g)
    g.hub.textbox = "always"
    g.wait("textbox hidden on title", lambda s: s.get("tb") == 1, 6)
    g.cmd("newgame", expect="ok")
    g.wait("story", lambda s: playing(s) and s.get("lines"), 60)
    advance(g, 4)
    n = len(g.history())
    log("    %d history lines after advancing" % n)
    if n < 3:
        raise Fail("history did not grow")

    g.wait("auto usable", btn(relay_btn["auto"]), 10)
    g.cmd("auto", expect="ok")
    g.wait("auto on", lambda s: s.get("auto") == 1, 5)
    g.cmd("auto", expect="ok")
    g.wait("auto off", lambda s: s.get("auto") == 0, 8)

    g.wait("quick save usable", btn(relay_btn["qsave"]), 10)
    g.cmd("qsave", expect="saved")
    mark = g.last_line_id()
    gen0 = g.st.get("gen")
    advance(g, 3)
    g.wait("quick load usable", btn(relay_btn["qload"]), 10)
    g.cmd("qload", expect="loaded")
    g.wait("backlog replaced after load", lambda s: s.get("gen") != gen0, 15)
    g.wait("textbox still hidden after load", lambda s: s.get("tb") == 1, 6)
    log("    quick save at line %d, load changed gen %s -> %s"
        % (mark, gen0, g.st.get("gen")))

    res = g.cmd("voice", expect=("ok", "novoice"))
    log("    voice -> %s" % res)

    g.hub.textbox = "never"
    g.wait("textbox shown again", lambda s: s.get("tb") == 0, 6)
    if g.st.get("tbHealed"):
        raise Fail("tbHealed=%s: a hidden opacity leaked into a save"
                   % g.st.get("tbHealed"))


def sc_choice(g, log):
    """skip to the first choice and pick an option."""
    to_title(g)
    g.cmd("newgame", expect="ok")
    g.wait("story", playing, 60)
    g.wait("skip usable", btn(relay_btn["skip"]), 15)
    g.cmd("skip", expect="ok")
    # Skipping stops at things a player taps through (an opening movie,
    # unread text in some games); tap and skip again when it stalls.
    end = time.time() + 300
    last, since = g.last_line_id(), time.time()
    while not has_sel(g.st):
        if time.time() > end:
            raise Fail("no choice within 300 s of skipping")
        time.sleep(0.3)
        if g.last_line_id() != last:
            last, since = g.last_line_id(), time.time()
        elif time.time() - since > 6 and not g.st.get("dlg"):
            g.cmd("click")
            time.sleep(1)
            if not g.st.get("skip") and not has_sel(g.st):
                g.cmd("skip")
            since = time.time()
    st = g.st
    opts = st.get("opts") or []
    log("    choice %s with %d options: %s"
        % (st["sel"], len(opts), [o[1] or o[2] for o in opts]))
    if not opts:
        raise Fail("choice without options")
    # a tap meant for another prompt must be refused
    g.cmd("choose", 0, st["sel"] + 1000, expect="stale")
    g.cmd("choose", 0, st["sel"], expect="chosen")
    g.wait("choice closed", lambda s: not has_sel(s), 15)
    before = g.last_line_id()
    g.wait("story after the choice",
           lambda s: playing(s) and (g.last_line_id() > before or s.get("skip")), 20)


def sc_title(g, log):
    """back to title from the story, then continue, then the load screen."""
    to_title(g)
    g.cmd("newgame", expect="ok")
    g.wait("story", playing, 60)
    advance(g, 2)
    g.wait("title usable", btn(relay_btn["title"]), 10)
    g.cmd("title", expect="totitle")
    to_title(g)
    g.wait("continue offered", btn(relay_btn["continue"]), 20)
    g.cmd("continue", expect="ok")
    g.wait("story again", playing, 30)
    g.wait("load usable", btn(relay_btn["load"]), 10)
    g.cmd("load", expect="ok")
    g.wait("load screen", lambda s: s.get("loc") == "load", 10)


def sc_screens(g, log):
    """settings from the title menu, and the save screen from the story."""
    to_title(g)
    g.cmd("config", expect="ok")
    g.wait("settings screen", lambda s: s.get("loc") == "config", 10)
    res = g.cmd("click")
    if res != "menu":
        raise Fail("click on a menu screen -> %s, expected menu" % res)


def sc_save(g, log):
    """the save screen from the story, and back to the story."""
    to_title(g)
    g.cmd("newgame", expect="ok")
    g.wait("story", playing, 60)
    g.wait("save usable", btn(relay_btn["save"]), 10)
    g.cmd("save", expect="ok")
    g.wait("save screen", lambda s: s.get("loc") == "save", 10)


# ------------------------------------------- KAG3 (the fallback adapter) --
#
# The fallback adapter is what every KAG3 game gets. It is tested on a game
# that uses the KAG3 sample scripts (systembutton.ks, yesnodialog.tjs) and
# draws its name plate into a message layer of its own. The scenarios run
# against a hardlinked mirror of it, made on the first run.

KAG3_MIRROR = os.path.join(HERE, "probe", "games", "maiden")


def kag3_game():
    """The mirror of the KAG3 test game, made if it is not there yet."""
    if not os.path.exists(KAG3_MIRROR):
        if not KAG3_SRC:
            raise SystemExit("set kag3_dir in dev/testgames.json (a KAG3 game folder)")
        import mirror
        print("making %s" % KAG3_MIRROR, flush=True)
        mirror.make(KAG3_SRC, KAG3_MIRROR, skip=["savedata"])
    return KAG3_MIRROR


def click_window(pid, max_width, rx, ry):
    """Click inside the newest small top-level window of `pid`: a question
    box of the game's own, which only the mouse can answer."""
    import ctypes
    import ctypes.wintypes as W
    u = ctypes.windll.user32
    found = []

    def cb(h, _):
        q = W.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(q))
        if q.value == pid and u.IsWindowVisible(h):
            r = W.RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            if 0 < r.right - r.left <= max_width:
                found.append(r)
        return True
    u.EnumWindows(ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)(cb), 0)
    if not found:
        raise Fail("no question box window to click")
    r = found[-1]
    x = int(r.left + (r.right - r.left) * rx)
    y = int(r.top + (r.bottom - r.top) * ry)
    # the game only sees a click while its own window is in front
    u.SetForegroundWindow(u.WindowFromPoint(W.POINT(x, y)))
    time.sleep(0.3)
    for _ in range(2):
        u.SetCursorPos(x, y)
        time.sleep(0.15)
        u.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.08)
        u.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.15)


def ksc_title(g, log):
    """the title menu: its own tiles, a new game, back to the title."""
    to_title(g)
    g.wait("title offers a new game", btn(relay_btn["newgame"]), 5)
    if g.st.get("opts"):
        raise Fail("the title menu came through as %d options" % len(g.st["opts"]))
    g.cmd("newgame", expect="ok")
    g.wait("story", playing, 40)
    advance(g, 3)
    g.wait("title usable", btn(relay_btn["title"]), 10)
    g.cmd("title", expect="totitle")
    to_title(g)
    log("    new game, back to the title, menu offered again")


def ksc_story(g, log):
    """new game, advance, speaker names, auto, skip, the textbox."""
    to_title(g)
    g.hub.textbox = "always"
    g.wait("textbox hidden on the title", lambda s: s.get("tb") == 1, 6)
    g.cmd("newgame", expect="ok")
    g.wait("story", lambda s: playing(s) and s.get("lines"), 40)
    advance(g, 12)
    h = [x for x in g.history() if x["kind"] == "L"]
    named = [x for x in h if x["name"]]
    if len(h) < 8:
        raise Fail("only %d lines after advancing" % len(h))
    if not named:
        raise Fail("none of %d lines got a speaker name" % len(h))
    for x in named:
        if x["text"].startswith(x["name"]):
            raise Fail("the name %r was left in the text %r"
                       % (x["name"], x["text"][:24]))
    log("    %d lines, %d named, last %r: %r"
        % (len(h), len(named), named[-1]["name"], named[-1]["text"][:20]))

    g.wait("auto usable", btn(relay_btn["auto"]), 10)
    g.cmd("auto", expect="ok")
    g.wait("auto on", lambda s: s.get("auto") == 1, 6)
    g.cmd("auto", expect="ok")
    g.wait("auto off", lambda s: s.get("auto") == 0, 10)

    before = g.last_line_id()
    g.wait("skip usable", btn(relay_btn["skip"]), 10)
    g.cmd("skip", expect="ok")
    g.wait("skip running",
           lambda s: s.get("skip") or g.last_line_id() > before, 8)
    if g.st.get("skip"):
        g.cmd("skip", expect="ok")
        g.wait("skip off", lambda s: not s.get("skip"), 10)
    log("    auto and skip ran (the game's own enterAutoMode/skipToStop)")

    g.hub.textbox = "never"
    g.wait("textbox shown again", lambda s: s.get("tb") == 0, 8)
    if g.st.get("tbHealed"):
        raise Fail("tbHealed=%s: a hidden opacity leaked into a save"
                   % g.st.get("tbHealed"))


def ksc_screens(g, log):
    """the game's own save, load and settings screens and what they report."""
    to_title(g)
    g.cmd("newgame", expect="ok")
    g.wait("story", playing, 40)
    for cmd, loc in (("save", "save"), ("load", "load"), ("config", "config")):
        g.wait("%s usable" % cmd, btn(relay_btn[cmd]), 10)
        g.cmd(cmd, expect="ok")
        g.wait("%s screen" % loc, lambda s: s.get("loc") == loc, 15)
        if g.st.get("opts"):
            raise Fail("the %s screen came through as %d options"
                       % (loc, len(g.st["opts"])))
        if g.st.get("btn"):
            raise Fail("the %s screen still offers buttons" % loc)
        g.eval("kag.onPrimaryRightClick()")     # the screen's own "back"
        g.wait("back in the story", playing, 15)
    log("    save, load and settings each open, report themselves and close")


def ksc_dialog(g, log):
    """the game's own question box: a dialog, answered on the computer."""
    to_title(g)
    g.cmd("newgame", expect="ok")
    g.wait("story", playing, 40)
    g.eval("systembutton_object.onTitleButtonClick()")   # asks its own question
    g.wait("question box reported",
           lambda s: s.get("loc") == "dialog" and s.get("dlg"), 15)
    if g.st.get("btn") or g.st.get("opts"):
        raise Fail("a question box still offers buttons or options")
    click_window(g.proc.pid, 400, 0.72, 0.74)            # its "no" button
    g.wait("back in the story", playing, 15)
    log("    a box was a dialog, and answering it on the PC gave the story back")


# The third field is the game a scenario needs: None is the built-in one
# (or whatever --game names), "kag3" the mirror of the KAG3 test game.
SCENARIOS = [("story", sc_story, None), ("choice", sc_choice, None),
             ("title", sc_title, None), ("screens", sc_screens, None),
             ("save", sc_save, None),
             ("kag3_story", ksc_story, "kag3"),
             ("kag3_screens", ksc_screens, "kag3"),
             ("kag3_title", ksc_title, "kag3"),
             ("kag3_dialog", ksc_dialog, "kag3")]


def game_for(args, need, named):
    """(exe, save folder, files added) for one scenario. --game wins over
    what the scenario asks for, but only when that scenario was asked for by
    name: running everything against one game would take the KAG3 scenarios
    to a game that is not the one they are for."""
    if need is None or (args.game and named):
        return args.exe, args.data, []
    import probe
    game = kag3_game()
    exe = os.path.join(game, probe.pick_exe(game))
    return exe, probe.dev_data(game), probe.dev_install(game)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("names", nargs="*")
    ap.add_argument("--exe", default=GAME_EXE)
    ap.add_argument("--data", default=DATA_DIR,
                    help="save-data COPY to run against (never the real one)")
    ap.add_argument("--fresh-from",
                    help="first replace --data with a copy of this folder")
    ap.add_argument("--loader", choices=("tpm", "version.dll", "mpr.dll", "script"),
                    help="fail unless the hook was installed this way")
    ap.add_argument("--adapter",
                    help="fail unless the hook picked this adapter "
                         "(default: koihazi for the built-in game)")
    ap.add_argument("--game",
                    help="another game folder: the hook (and dev probe) are "
                         "installed for the run, saves go to dev/probe/data/<game>")
    ap.add_argument("--setup",
                    help="TJS run once the hook is up, e.g. 'kag.allskip=1' (with --game)")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    if args.list:
        for n, f, need in SCENARIOS:
            print("%-13s %s%s" % (n, f.__doc__ or "",
                                  "  [%s]" % need if need else ""))
        return 0
    added = []
    if args.game:
        import probe
        game = os.path.abspath(args.game)
        if args.exe == GAME_EXE:
            args.exe = os.path.join(game, probe.pick_exe(game))
        if args.data == DATA_DIR:
            args.data = probe.dev_data(game)
        added = probe.dev_install(game)
    if not args.exe:
        print("no test game: pass --game/--exe or set koihazi_exe in dev/testgames.json")
        return 2
    # the game is started with its own working directory, so a relative
    # --datapath would land inside the game folder
    args.data = os.path.abspath(args.data)
    if args.fresh_from:
        args.fresh_from = os.path.abspath(args.fresh_from)
    real = [os.path.normcase(d) for d in relay.profile_savedirs()]
    if os.path.normcase(os.path.abspath(args.data)) in real:
        print("refusing to run against the real save folder")
        return 2
    if args.fresh_from:
        shutil.rmtree(args.data, ignore_errors=True)
        shutil.copytree(args.fresh_from, args.data)
    try:
        return run(args)
    finally:
        if added:
            probe.dev_uninstall(game, added)


def run(args):
    pick = [s for s in SCENARIOS if not args.names or s[0] in args.names]
    results = []
    seq = int(time.time())
    for name, fn, need in pick:
        t0 = time.time()
        print("== %s" % name, flush=True)
        exe, data, added = game_for(args, need, bool(args.names))
        # an explicit --adapter wins; otherwise the scenario's game decides
        # (the built-in game is the one the koihazi adapter is for)
        want = args.adapter or need or "koihazi"
        g = Game(exe, data, lambda m: print(m, flush=True))
        try:
            g.start()
            st = g.wait("an adapter", lambda s: s.get("adapter") not in (None, "none")
                        or s.get("degraded"), 60)
            if args.setup:
                import probe
                seq += 1
                r = probe.probe_eval(data, seq, args.setup)
                if r is None or "error" in r:
                    raise Fail("setup failed: %s" % (r and r.get("error")))
            if st.get("degraded"):
                raise Fail("adapter %s was dropped: %s" % (st["degraded"], st.get("err")))
            if want and st.get("adapter") != want:
                raise Fail("adapter %s, expected %s" % (st.get("adapter"), want))
            if args.loader and st.get("loader") != args.loader:
                raise Fail("hook loaded by %s, expected %s"
                           % (st.get("loader"), args.loader))
            fn(g, lambda m: print(m, flush=True))
            results.append((name, "PASS", ""))
        except Fail as ex:
            results.append((name, "FAIL", str(ex)))
        except Exception as ex:   # a bug in this script, not in the hook
            results.append((name, "ERROR", "%s: %s" % (type(ex).__name__, ex)))
        finally:
            err = g.st.get("err")
            if g.st.get("dbg"):
                print("   dbg:", g.st.get("dbg"), flush=True)
            g.stop()
            if added:
                import probe
                probe.dev_uninstall(os.path.dirname(exe), added)
            if err:
                results[-1] = (results[-1][0], results[-1][1],
                               (results[-1][2] + "  hook err: " + err).strip())
        print("   %s (%.0fs) %s" % (results[-1][1], time.time() - t0,
                                    results[-1][2]), flush=True)
        time.sleep(1.5)
    print()
    for name, res, why in results:
        print("%-13s %-5s %s" % (name, res, why))
    return 0 if all(r[1] == "PASS" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
