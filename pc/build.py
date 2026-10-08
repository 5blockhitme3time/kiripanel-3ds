#!/usr/bin/env python3
r"""
Build the player's download: dist\KiriPanel\

    python pc\build.py            (run plugin\native\build.bat first)

    KiriPanel.exe        the manager (relay + window), one file
    3DS\                 the console app, if dist\3ds holds a build
    使用说明.txt          the player's guide (docs\player_guide.txt)

The plugin itself (version.dll / mpr.dll / kiripanel.tpm, both bitnesses,
and the hook scripts) travels inside KiriPanel.exe and is copied into a
game only when the player installs it there.

    python pc\build.py --zip      also dist\KiriPanel-<version>.zip, ready to
                                 attach to a GitHub release. Works on any
                                 platform: --add-data takes os.pathsep.
"""
import argparse
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(REPO, "dist", "KiriPanel")
WORK = os.path.join(REPO, "dist", "_build")
NATIVE = os.path.join(REPO, "plugin", "native", "out")
HOOK = os.path.join(REPO, "plugin", "hook")


def payload_args():
    args = []
    for arch in ("x86", "x64"):
        for n in ("version.dll", "mpr.dll", "kiripanel.tpm"):
            p = os.path.join(NATIVE, arch, n)
            if not os.path.isfile(p):
                raise SystemExit("missing %s - run plugin\\native\\build.bat" % p)
            args += ["--add-data", "%s%spayload/native/%s" % (p, os.pathsep, arch)]
    from kiripanel import install
    for n in install.hook_files() + ["AfterInit2.tjs"]:
        args += ["--add-data", "%s%spayload/hook" % (os.path.join(HOOK, n), os.pathsep)]
    return args


def version():
    """VERSION from pc/kiripanel/app.py - one place to bump."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    from kiripanel import app
    return app.VERSION


def main():
    ap = argparse.ArgumentParser(description="build dist/KiriPanel")
    ap.add_argument("--zip", action="store_true",
                    help="also dist/KiriPanel-<version>.zip, for a release")
    a = ap.parse_args()
    sys.path.insert(0, HERE)
    from kiripanel import install
    install.check_ascii()
    pkg = os.path.join(HERE, "kiripanel")
    entry = os.path.join(WORK, "KiriPanel.py")
    os.makedirs(WORK, exist_ok=True)
    with open(entry, "w", encoding="utf-8") as f:
        f.write("import sys\nfrom kiripanel import app\nsys.exit(app.main())\n")
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
           "--windowed", "--name", "KiriPanel",
           "--distpath", OUT, "--workpath", WORK, "--specpath", WORK,
           "--paths", HERE,
           "--add-data", "%s%skiripanel" % (os.path.join(pkg, "ui.html"), os.pathsep),
           "--add-data", "%s%skiripanel" % (os.path.join(pkg, "icon.png"), os.pathsep),
           "--add-data", "%s%skiripanel/profiles"
           % (os.path.join(pkg, "profiles", "*.json"), os.pathsep),
           ] + payload_args() + [entry]
    icon = os.path.join(HERE, "kiripanel.ico")
    if os.path.isfile(icon):
        cmd[cmd.index("--name"):cmd.index("--name")] = ["--icon", icon]
    print("building KiriPanel.exe ...", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        print(r.stdout[-3000:], r.stderr[-6000:])
        raise SystemExit("PyInstaller failed")
    guide = os.path.join(REPO, "docs", "player_guide.txt")
    if os.path.isfile(guide):
        # Notepad-friendly: BOM and CRLF
        with open(guide, encoding="utf-8") as f:
            text = f.read()
        with open(os.path.join(OUT, "使用说明.txt"), "w", encoding="utf-8-sig",
                  newline="\r\n") as f:
            f.write(text)
    console = os.path.join(REPO, "dist", "3ds")
    dst = os.path.join(OUT, "3DS")
    if os.path.isdir(console):
        os.makedirs(dst, exist_ok=True)
        for n in ("moonlight.3dsx", "moonlight.cia"):
            if os.path.isfile(os.path.join(console, n)):
                shutil.copyfile(os.path.join(console, n), os.path.join(dst, n))
    size = os.path.getsize(os.path.join(OUT, "KiriPanel.exe")) / 1048576
    print("done: %s (KiriPanel.exe %.1f MB)" % (OUT, size))
    if a.zip:
        zpath = os.path.join(REPO, "dist", "KiriPanel-%s.zip" % version())
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _, files in os.walk(OUT):
                for n in files:
                    p = os.path.join(root, n)
                    z.write(p, os.path.relpath(p, OUT))
        print("zip : %s (%.1f MB)" % (zpath,
                                      os.path.getsize(zpath) / 1048576))


if __name__ == "__main__":
    main()
