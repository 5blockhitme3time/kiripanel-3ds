#!/usr/bin/env python3
r"""
Install the 3DS panel hook into a KiriKiri game folder.

    python install.py GAME_DIR              version.dll + hook (the normal install;
                                            mpr.dll if the game has its own
                                            version.dll)
    python install.py GAME_DIR --tpm        also plugin\kiripanel.tpm, for engines
                                            that load *.tpm by themselves
    python install.py GAME_DIR --fallback   also AfterInit2.tjs, for games whose
                                            scripts run it
    python install.py GAME_DIR --uninstall  removes all of the above, and the
                                            layout of version 0.2
    python install.py GAME_DIR --dry-run    show what would happen

Writes only these files, and never replaces a file that is not ours:
    version.dll or mpr.dll              next to the exe (32- or 64-bit, as the exe)
    kiripanel/*.tjs                     next to the exe
    plugin/kiripanel.tpm                (--tpm; plugin64\ for 64-bit KiriKiri Z)
    AfterInit2.tjs                      (--fallback)
"""
import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, "frozen", False):
    # the packaged manager carries the payload (see pc/build.py)
    PAYLOAD = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), "payload")
    OUT = os.path.join(PAYLOAD, "native")
    HOOK_DIR = os.path.join(PAYLOAD, "hook")
else:
    REPO = os.path.dirname(os.path.dirname(HERE))
    OUT = os.path.join(REPO, "plugin", "native", "out")
    HOOK_DIR = os.path.join(REPO, "plugin", "hook")
# Text that marks a file as one of ours (current or earlier versions).
OUR_MARKS = (b"kiripanel", "kiripanel".encode("utf-16-le"), b"HKPanelBridge")
# DLL names the proxy can take, in order of preference. Every KiriKiri
# engine seen so far imports both.
PROXY_NAMES = ("version.dll", "mpr.dll")
# hook/ files that are not part of the installed hook.
NOT_INSTALLED = ("AfterInit2.tjs", "kiripanel_startup.tjs")


def pe_machine(path):
    """'x86', 'x64' or None."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return None
    if head[:2] != b"MZ" or len(head) < 0x40:
        return None
    off = int.from_bytes(head[0x3c:0x40], "little")
    if off + 6 > len(head) or head[off:off + 4] != b"PE\0\0":
        return None
    m = int.from_bytes(head[off + 4:off + 6], "little")
    return {0x14c: "x86", 0x8664: "x64"}.get(m)


def is_kirikiri(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return False
    return any(s in data for s in (b"TVP(KIRIKIRI)", "TVP(KIRIKIRI)".encode("utf-16-le"),
                                   "kirikiriz".encode("utf-16-le")))


def find_engines(game):
    out = []
    for n in sorted(os.listdir(game)):
        p = os.path.join(game, n)
        if n.lower().endswith(".exe") and os.path.isfile(p):
            arch = pe_machine(p)
            if arch and is_kirikiri(p):
                out.append((n, arch))
    return out


def ours(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return False
    low = data.lower()
    return any(m.lower() in low for m in OUR_MARKS)


def hook_files():
    return sorted(n for n in os.listdir(HOOK_DIR)
                  if n.endswith(".tjs") and n not in NOT_INSTALLED)


def proxy_name(game):
    """The first proxy name the game does not use for a DLL of its own
    (patches often come as a version.dll), or None."""
    for n in PROXY_NAMES:
        p = os.path.join(game, n)
        if not os.path.exists(p) or ours(p):
            return n
    return None


def plan(game, tpm=False, fallback=False, uninstall=False, arch=None):
    """(engines, [(src, dst)]); src None means "remove dst". An uninstall
    plan lists every file we may have put there, in any version."""
    engines = find_engines(game)
    if not engines:
        raise SystemExit("no KiriKiri executable found in %s" % game)
    archs = sorted({a for _, a in engines})
    if arch is None:
        # One folder holds one proxy. Mixed folders are rare; the 32-bit
        # build is the one the player most likely runs.
        arch = "x86" if "x86" in archs else archs[0]
    if uninstall:
        steps = [(None, os.path.join(game, n)) for n in PROXY_NAMES]
    else:
        name = proxy_name(game)
        if name is None:
            raise SystemExit("the game already has its own %s; no proxy name left"
                             % " and ".join(PROXY_NAMES))
        steps = [(os.path.join(OUT, arch, name), os.path.join(game, name))]
    for n in hook_files():
        steps.append((os.path.join(HOOK_DIR, n), os.path.join(game, "kiripanel", n)))
    for a in archs if (tpm or uninstall) else []:
        pdir = os.path.join(game, "plugin" if a == "x86" else "plugin64")
        steps.append((os.path.join(OUT, a, "kiripanel.tpm"),
                      os.path.join(pdir, "kiripanel.tpm")))
    if fallback or uninstall:
        steps.append((os.path.join(HOOK_DIR, "AfterInit2.tjs"),
                      os.path.join(game, "AfterInit2.tjs")))
    # version 0.2 kept the hook under plugin\; the plugin finds it next to
    # the exe now, so the old copy only goes stale
    for sub in ("plugin", "plugin64"):
        d = os.path.join(game, sub, "kiripanel")
        if os.path.isdir(d):
            for n in sorted(os.listdir(d)):
                if n.endswith(".tjs"):
                    steps.append((None, os.path.join(d, n)))
    return engines, steps


def remove_empty_dirs(game):
    for d in ("kiripanel", "plugin/kiripanel", "plugin64/kiripanel"):
        p = os.path.join(game, d)
        if os.path.isdir(p) and not os.listdir(p):
            os.rmdir(p)


def check_ascii():
    """The engine may read the hook as Shift-JIS, UTF-8 or the system code
    page; ASCII reads the same under all of them."""
    for n in hook_files() + ["AfterInit2.tjs"]:
        with open(os.path.join(HOOK_DIR, n), "rb") as f:
            data = f.read()
        if any(b > 127 for b in data):   # also catches a UTF-8 BOM
            raise SystemExit("hook/%s is not plain ASCII" % n)


def apply(game, uninstall=False, tpm=False, fallback=False, arch=None,
          dry_run=False, log=print):
    """Install into (or remove from) `game`. Returns the number of files
    that could not be handled (the game's own, or a missing build)."""
    check_ascii()
    game = os.path.abspath(game)
    engines, steps = plan(game, tpm, fallback, uninstall, arch)
    for n, a in engines:
        log("engine  %s (%s)" % (n, a))
    if len({a for _, a in engines}) > 1 and not uninstall:
        log("note    32- and 64-bit engines here; %s is the %s build"
            " (use --arch to change)" % (os.path.basename(steps[0][1]),
                                        os.path.basename(os.path.dirname(steps[0][0]))))
    problems = 0
    for src, dst in steps:
        rel = os.path.relpath(dst, game)
        exists = os.path.exists(dst)
        if exists and not ours(dst):
            if uninstall:
                log("keep    %s: the game's own" % rel)
            else:
                log("SKIP    %s: belongs to the game, not replacing it" % rel)
                problems += 1
            continue
        if uninstall or src is None:
            if exists:
                log("remove  %s" % rel)
                if not dry_run:
                    os.remove(dst)
            continue
        if not os.path.exists(src):
            log("MISSING %s (build it first)" % src)
            problems += 1
            continue
        log("%s %s" % ("update " if exists else "install", rel))
        if not dry_run:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
    if not dry_run:
        remove_empty_dirs(game)
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("game")
    ap.add_argument("--tpm", action="store_true")
    ap.add_argument("--fallback", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--arch", choices=("x86", "x64"),
                    help="version.dll for this engine (default: from the exe)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    problems = apply(a.game, a.uninstall, a.tpm, a.fallback, a.arch, a.dry_run)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
