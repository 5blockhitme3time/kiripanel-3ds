#!/usr/bin/env python3
"""
Build the font the 3DS panel carries: Noto Sans SC cut down to the
characters galgames use.

    python makesubset.py            -> ../moonlight-n3ds/3ds/data/hkfont.ttf

The set is game-independent: all of GB2312 (simplified Chinese), Big5
level 1 (common traditional), JIS X 0208 (Japanese kanji and kana), the
full-width forms and the punctuation and symbols scripts use - about 11 000
characters, 3.5 MB. Text outside it is drawn from a full font on the SD
card if the player put one there (see panel_font.hpp), else as a box.

charset.txt (the first game's script) and the panel's own strings
(ui_strings.py) are added on top, so nothing that worked before goes
missing.
"""
import os

from fontTools import subset
from fontTools.ttLib import TTFont

HERE = os.path.dirname(os.path.abspath(__file__))
CHARSET = os.path.join(HERE, "charset.txt")
OUT = os.path.join(HERE, "..", "moonlight-n3ds", "3ds", "data", "hkfont.ttf")

# Noto Sans SC: modern screen-designed CJK with the widest coverage of the
# fonts Windows ships (1 of the 11 200 target characters missing; YaHei 57,
# SimHei 125, the Japanese fonts ~2 300). It is a variable font, so the
# variation tables are dropped to freeze it at the default (regular) weight.
SOURCE = r"C:\Windows\Fonts\NotoSansSC-VF.ttf"

EXTRA = (
    "".join(chr(c) for c in range(0x20, 0x7F))          # ASCII printable
    + "".join(chr(c) for c in range(0xA0, 0x180))       # Latin-1 + Latin Extended-A
    + "".join(chr(c) for c in range(0x2010, 0x2070))    # general punctuation
    + "".join(chr(c) for c in range(0x2190, 0x2200))    # arrows
    + "".join(chr(c) for c in range(0x2460, 0x2500))    # circled numbers
    + "".join(chr(c) for c in range(0x2500, 0x2580))    # box drawing
    + "".join(chr(c) for c in range(0x25A0, 0x2700))    # shapes, symbols (♪ ★ ♥ ...)
    + "".join(chr(c) for c in range(0x3000, 0x3100))    # CJK punctuation, kana
    + "".join(chr(c) for c in range(0x3100, 0x3130))    # bopomofo
    + "".join(chr(c) for c in range(0x31F0, 0x3200))    # small katakana
    + "".join(chr(c) for c in range(0xFF00, 0xFFF0))    # full-width and half-width forms
    + "〜～♪♡♥☆★※…‥―"
)


def encodable(codec, lead_ranges):
    """Every single character of a double-byte codec with lead bytes in
    `lead_ranges`."""
    out = set()
    for lo, hi in lead_ranges:
        for b1 in range(lo, hi + 1):
            for b2 in range(0x40, 0xFF):
                try:
                    s = bytes([b1, b2]).decode(codec)
                except UnicodeDecodeError:
                    continue
                if len(s) == 1:
                    out.add(s)
    return out


def wanted():
    chars = set(EXTRA)
    chars |= encodable("gb2312", [(0xA1, 0xF7)])
    chars |= encodable("big5", [(0xA1, 0xC6)])          # symbols + level 1
    chars |= encodable("cp932", [(0x81, 0x9F), (0xE0, 0xEA)])
    if os.path.exists(CHARSET):
        chars |= set(open(CHARSET, encoding="utf-8").read())
    try:
        from ui_strings import ui_chars
        chars |= ui_chars()
    except ImportError:
        pass
    return {c for c in chars if c.isprintable() or c == " "}


def main():
    need = wanted()
    font = TTFont(SOURCE)
    have = font.getBestCmap()
    text = "".join(sorted(c for c in need if ord(c) in have))
    missing = sorted(c for c in need if ord(c) not in have)
    opts = subset.Options()
    opts.layout_features = ["*"]
    opts.name_IDs = ["*"]
    opts.notdef_outline = True
    opts.recalc_bounds = True
    opts.drop_tables += ["DSIG", "gvar", "HVAR", "VVAR", "MVAR", "STAT", "avar", "fvar",
                         "MERG", "meta"]
    s = subset.Subsetter(options=opts)
    s.populate(text=text)
    s.subset(font)
    font.flavor = None
    out = os.path.normpath(OUT)
    font.save(out)
    print("%d characters wanted, %d in the font -> %s (%.2f MB)"
          % (len(need), len(text), out, os.path.getsize(out) / 1048576))
    if missing:
        print("not in Noto Sans SC (drawn from the SD card font, or as a box): %s"
              % "".join(missing[:40]))


if __name__ == "__main__":
    main()
