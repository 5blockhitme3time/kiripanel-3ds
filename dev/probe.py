#!/usr/bin/env python3
r"""
Explore a running game while writing its adapter (development only).

    python probe.py start GAME_DIR [--exe NAME]   install, start with this tool's
                    [--fresh]                     own save folder for the game
                                                  (dev/probe/data/<game>)
    python probe.py eval "EXPR" [--depth N]       evaluate TJS in the game
    python probe.py eval -f "STATEMENTS; return X"
    python probe.py state                         the hook's state, short
    python probe.py exec CMD [ARG] [SEL]          run a panel command through
                                                  the current adapter
    python probe.py shot [FILE]                   screenshot of the game window
    python probe.py stop                          end the game, remove what
                                                  start added

One game at a time; the session lives in dev/probe/session.json. The game
never sees the player's saves, and nothing here is reachable from the
network (the files are in the dev save folder only).
"""
import argparse
import ctypes
import ctypes.wintypes as W
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "pc"))
from kiripanel import install, relay  # noqa: E402

PROBE_DIR = os.path.join(HERE, "probe")
SESSION = os.path.join(PROBE_DIR, "session.json")
DEVPROBE = os.path.join(PROBE_DIR, "devprobe.tjs")


def load_session():
    try:
        with open(SESSION, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        raise SystemExit("no probe session; run: probe.py start GAME_DIR")


def pick_exe(game):
    engines = [n for n, _ in install.find_engines(game)]
    if not engines:
        raise SystemExit("no KiriKiri executable in %s" % game)
    for key in ("_chs", "chs", "crack"):
        for n in engines:
            if key in n.lower():
                return n
    return min(engines, key=len)


def dev_install(game):
    """Install the hook and the dev probe into `game` (only files that are
    not there yet). Returns the paths added, for dev_uninstall()."""
    _, steps = install.plan(game)
    steps.append((DEVPROBE, os.path.join(game, "kiripanel", "devprobe.tjs")))
    added = []
    for src, dst in steps:
        if src is None:          # a stale copy the real install would remove
            continue
        if os.path.exists(dst):
            if not install.ours(dst):
                raise SystemExit("%s is the game's own; not touching it" % dst)
            # ours but maybe older: refresh it, it stays in place afterwards
            shutil.copyfile(src, dst)
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        added.append(dst)
    return added


def dev_uninstall(game, added):
    for p in reversed(added):
        try:
            os.remove(p)
        except OSError as ex:
            print("! could not remove %s: %s" % (p, ex))
    install.remove_empty_dirs(game)


def dev_data(game, fresh=False):
    """This tool's own save folder for `game`, kept between runs so the
    game's first-start questions are answered only once."""
    data = os.path.join(PROBE_DIR, "data", os.path.basename(game.rstrip("\\/")))
    if fresh:
        shutil.rmtree(data, ignore_errors=True)
    os.makedirs(data, exist_ok=True)
    # _kpprobe_boot.tjs pins an adapter (--adapter): a stale one would keep
    # the next run (or dev/regress.py) on that adapter without saying so
    for n in (relay.STATE_NAME, relay.CMD_NAME, "_kpprobe_in.ksd",
              "_kpprobe_out.ksd", "_kpprobe_boot.tjs"):
        if os.path.exists(os.path.join(data, n)):
            os.remove(os.path.join(data, n))
    return data


def cmd_start(a):
    if os.path.exists(SESSION):
        raise SystemExit("a probe session is running; probe.py stop first")
    game = os.path.abspath(a.game)
    exe = os.path.join(game, a.exe or pick_exe(game))
    added = dev_install(game)
    data = dev_data(game, a.fresh)
    boot = os.path.join(data, "_kpprobe_boot.tjs")
    if a.adapter:
        with open(boot, "w", encoding="ascii") as f:
            f.write('global.kiripanel_only = "%s";\n' % a.adapter)
    elif os.path.exists(boot):
        os.remove(boot)
    proc = subprocess.Popen([exe, "-datapath=" + data.rstrip("\\/") + "\\"],
                            cwd=game)
    with open(SESSION, "w", encoding="utf-8") as f:
        json.dump({"game": game, "exe": exe, "pid": proc.pid, "data": data,
                   "added": added, "seq": 0}, f, ensure_ascii=False, indent=1)
    print("started %s (pid %d), data %s" % (os.path.basename(exe), proc.pid, data))
    end = time.time() + a.wait
    while time.time() < end:
        if os.path.exists(os.path.join(data, relay.STATE_NAME)):
            print("hook up: %s" % short_state(read_state(data)))
            return
        time.sleep(0.3)
    print("hook not up after %d s (see %%LOCALAPPDATA%%\\kiripanel\\kiripanel.log)" % a.wait)


def read_state(data):
    p = os.path.join(data, relay.STATE_NAME)
    for _ in range(10):
        try:
            with open(p, "rb") as f:
                return relay.parse_struct(relay.decode_struct_bytes(f.read()))
        except (OSError, relay.StructError):
            time.sleep(0.05)
    return {}


def short_state(st):
    keys = ("adapter", "degraded", "loc", "rawLoc", "dlg", "auto", "skip", "btn",
            "sel", "tb", "err")
    s = " ".join("%s=%s" % (k, st.get(k)) for k in keys if st.get(k) not in (None, ""))
    lines = st.get("lines") or []
    if lines:
        s += "\n  last lines: " + " | ".join("%s:%s" % (l[2], l[3][:40]) for l in lines[-3:])
    if st.get("cur"):
        s += "\n  typing: %s: %s" % (st.get("curName"), st.get("cur")[:60])
    if st.get("opts"):
        s += "\n  options: %s" % st.get("opts")
    acks = st.get("acks") or []
    if acks:
        s += "\n  acks: %s" % acks[-3:]
    return s


def probe_eval(data, seq, expr, depth=3, timeout=10):
    """Evaluate `expr` in the game whose save folder is `data` (devprobe
    must be installed). seq must grow per call. Returns the answer dict
    (value or error), or None on timeout."""
    relay.write_struct_file(os.path.join(data, "_kpprobe_in.ksd"),
                            {"seq": seq, "expr": expr, "depth": depth})
    out = os.path.join(data, "_kpprobe_out.ksd")
    end = time.time() + timeout
    while time.time() < end:
        try:
            with open(out, "rb") as f:
                r = relay.parse_struct(relay.decode_struct_bytes(f.read()))
            if r.get("seq") == seq:
                return r
        except (OSError, relay.StructError):
            pass
        time.sleep(0.05)
    return None


def ask(s, expr, depth, timeout=10):
    s["seq"] += 1
    with open(SESSION, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    r = probe_eval(s["data"], s["seq"], expr, depth, timeout)
    if r is None:
        raise SystemExit("no answer in %d s (game busy, or a modal dialog open?)" % timeout)
    return r


def show(v, indent=0, out=None):
    pad = "  " * indent
    if isinstance(v, dict) and set(v) >= {"class"} and "members" in v:
        print(pad + "{%s}" % v["class"])
        m = v["members"]
        for i in range(0, len(m), 8):
            print(pad + "  " + " ".join(m[i:i + 8]))
    elif isinstance(v, dict):
        for k in sorted(v):
            x = v[k]
            if isinstance(x, (dict, list)) and x:
                print(pad + "%s:" % k)
                show(x, indent + 1)
            else:
                print(pad + "%s = %r" % (k, x))
    elif isinstance(v, list):
        if all(not isinstance(x, (dict, list)) for x in v):
            print(pad + repr(v))
        else:
            for i, x in enumerate(v):
                print(pad + "[%d]" % i)
                show(x, indent + 1)
    else:
        print(pad + repr(v))


def cmd_eval(a):
    s = load_session()
    expr = a.expr
    if a.body:
        # statements: run as a function body (use `return`)
        expr = "(function() { %s } incontextof global)()" % expr
    r = ask(s, expr, a.depth, a.timeout)
    if "error" in r:
        print("ERROR:", r["error"])
        if r.get("trace"):
            print("  at", r["trace"])
        return 1
    show(r.get("value"))


def cmd_src(a):
    """Copy game scripts (as the engine reads them) to dev/probe/src/<game>/."""
    s = load_session()
    tag = os.path.basename(s["game"].rstrip("\\/"))
    out_dir = os.path.join(PROBE_DIR, "src", tag)
    os.makedirs(out_dir, exist_ok=True)
    for name in a.names:
        r = ask(s, 'kp_source("%s")' % name.replace('"', '\\"'), 1, 20)
        if "error" in r:
            print("%-28s %s" % (name, r["error"]))
            continue
        text = r.get("value") or ""
        dst = os.path.join(out_dir, name.replace("/", "_"))
        with open(dst, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print("%-28s %6d lines -> %s" % (name, text.count("\n"), dst))


def cmd_state(a):
    print(short_state(read_state(load_session()["data"])))


def cmd_exec(a):
    s = load_session()
    expr = ('global.hk_panel_bridge.adapter.exec(%%["cmd" => "%s", "arg" => %d, "sel" => %d])'
            % (a.cmd, a.arg, a.sel))
    r = ask(s, expr, 1)
    print(r.get("error") or r.get("value"))


def cmd_hide(a):
    """Ask the hook to hide the textbox, as the relay does for a console in
    dialogue mode. The request lapses 4 s after the last one (the relay
    keeps renewing it), so take the screenshot right after."""
    s = load_session()
    s["beat"] = s.get("beat", 0) + 1
    with open(SESSION, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    relay.write_struct_file(os.path.join(s["data"], relay.CMD_NAME),
                            {"epoch": 1, "cmds": [], "hide": 1 if a.on == "on" else 0,
                             "beat": s["beat"]})
    time.sleep(0.5)
    print(short_state(read_state(s["data"])))


def cmd_shot(a):
    s = load_session()
    path = a.file or os.path.join(PROBE_DIR, "shot.png")
    grab(s["pid"], path, a.scale)
    print(path)


def grab(pid, path, scale):
    from PIL import Image
    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    u.SetProcessDPIAware()
    found = []

    @ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    def each(h, _):
        p = W.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid and u.IsWindowVisible(h):
            r = W.RECT()
            u.GetClientRect(h, ctypes.byref(r))
            found.append((r.right * r.bottom, h, r.right, r.bottom))
        return True
    u.EnumWindows(each, 0)
    if not found:
        raise SystemExit("no visible window for pid %d" % pid)
    _, h, w, hh = max(found)
    dc = u.GetDC(h)
    mdc = g.CreateCompatibleDC(dc)
    bmp = g.CreateCompatibleBitmap(dc, w, hh)
    g.SelectObject(mdc, bmp)
    u.PrintWindow(h, mdc, 3)   # PW_CLIENTONLY | PW_RENDERFULLCONTENT

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", W.DWORD), ("biWidth", W.LONG), ("biHeight", W.LONG),
                    ("biPlanes", W.WORD), ("biBitCount", W.WORD), ("biCompression", W.DWORD),
                    ("biSizeImage", W.DWORD), ("a", W.LONG), ("b", W.LONG),
                    ("c", W.DWORD), ("d", W.DWORD)]
    bi = BIH(ctypes.sizeof(BIH), w, -hh, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * hh * 4)
    g.GetDIBits(mdc, bmp, 0, hh, buf, ctypes.byref(bi), 0)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(h, dc)
    img = Image.frombuffer("RGBA", (w, hh), buf, "raw", "BGRA", 0, 1).convert("RGB")
    if scale != 1:
        img = img.resize((int(w * scale), int(hh * scale)))
    img.save(path)


def cmd_stop(a):
    s = load_session()
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(s["pid"])],
                   capture_output=True)
    time.sleep(1)
    dev_uninstall(s["game"], s["added"])
    os.remove(SESSION)
    print("stopped; removed %d file(s) from the game folder" % len(s["added"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("start")
    p.add_argument("game")
    p.add_argument("--exe")
    p.add_argument("--wait", type=int, default=40)
    p.add_argument("--fresh", action="store_true", help="empty the dev save folder first")
    p.add_argument("--adapter", help="use only this adapter (to test kag3 on any game)")
    p.set_defaults(fn=cmd_start)
    p = sub.add_parser("eval")
    p.add_argument("expr")
    p.add_argument("--depth", type=int, default=3)
    p.add_argument("--timeout", type=int, default=10)
    p.add_argument("-f", "--body", action="store_true",
                   help="EXPR is a function body (statements, ends with return)")
    p.set_defaults(fn=cmd_eval)
    p = sub.add_parser("state")
    p.set_defaults(fn=cmd_state)
    p = sub.add_parser("src")
    p.add_argument("names", nargs="+")
    p.set_defaults(fn=cmd_src)
    p = sub.add_parser("exec")
    p.add_argument("cmd")
    p.add_argument("arg", type=int, nargs="?", default=0)
    p.add_argument("sel", type=int, nargs="?", default=-1)
    p.set_defaults(fn=cmd_exec)
    p = sub.add_parser("hide")
    p.add_argument("on", choices=("on", "off"))
    p.set_defaults(fn=cmd_hide)
    p = sub.add_parser("shot")
    p.add_argument("file", nargs="?")
    p.add_argument("--scale", type=float, default=0.5)
    p.set_defaults(fn=cmd_shot)
    p = sub.add_parser("stop")
    p.set_defaults(fn=cmd_stop)
    a = ap.parse_args()
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
