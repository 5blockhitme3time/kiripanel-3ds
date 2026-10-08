#!/usr/bin/env python3
"""
Characters the panel shows that do not come from the game script: the
console UI's own strings (src/panel, src/relay) and everything relay.py sends
(status labels, notes, choice captions). makesubset.py adds these to the
font, and `python ui_strings.py --check FONT` lists any the font lacks.
"""
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONSOLE = os.path.dirname(HERE)
REPO = os.path.dirname(CONSOLE)
SOURCES = (
    glob.glob(os.path.join(CONSOLE, "moonlight-n3ds", "src", "panel", "*.cpp"))
    + glob.glob(os.path.join(CONSOLE, "moonlight-n3ds", "src", "relay", "*.cpp"))
    + [os.path.join(REPO, "pc", "kiripanel", "relay.py")]
    + glob.glob(os.path.join(REPO, "pc", "kiripanel", "profiles", "*.json"))
)
STRING = re.compile(r'"((?:[^"\\\n]|\\.)*)"')


def ui_chars():
    chars = set()
    for path in SOURCES:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for m in STRING.finditer(text):
            chars.update(c for c in m.group(1) if ord(c) > 0x7F)
    return chars


def main():
    chars = ui_chars()
    if len(sys.argv) == 3 and sys.argv[1] == "--check":
        from fontTools.ttLib import TTFont
        font = TTFont(sys.argv[2], lazy=True)
        cmap = set(font.getBestCmap())
        missing = sorted(c for c in chars if ord(c) not in cmap)
        print("%d UI characters, %d missing: %s"
              % (len(chars), len(missing), "".join(missing)))
        sys.exit(1 if missing else 0)
    print("".join(sorted(chars)))


if __name__ == "__main__":
    main()
