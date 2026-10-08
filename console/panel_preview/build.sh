#!/usr/bin/env bash
# Build the panel harness natively on the PC and run it.
#
#   bash build.sh            tests (under AddressSanitizer) + preview sheet
#                            (wsl bash console/panel_preview/build.sh)
#
# Uses the console's own sources. The headers are looked for where devkitPro's
# portlibs keep them first (this is usually run in WSL next to the toolchain),
# then wherever this host has FreeType; the library comes from pkg-config or
# the usual lib directory, so a plain Linux box with libfreetype-dev works too.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/../moonlight-n3ds/src"
FONT="$HERE/../moonlight-n3ds/3ds/data/hkfont.ttf"
FT_INC="$HOME/devkitpro/portlibs/3ds/include/freetype2"
if [ ! -f "$FT_INC/ft2build.h" ]; then
    for probe in "$(pkg-config --cflags-only-I freetype2 2>/dev/null | tr -d '-I')" \
                 /usr/include/freetype2 \
                 "$(ls -d /usr/include/freetype2* 2>/dev/null | head -1)"; do
        if [ -n "$probe" ] && [ -f "$probe/ft2build.h" ]; then
            FT_INC="$probe"
            break
        fi
    done
fi
[ -f "$FT_INC/ft2build.h" ] || { echo "no FreeType headers (install libfreetype-dev)"; exit 1; }

FT_LIB="$(pkg-config --libs freetype2 2>/dev/null || true)"
if [ -z "$FT_LIB" ]; then
    FT_LIB="$(ls /usr/lib/*/libfreetype.so* /usr/lib/libfreetype.so* 2>/dev/null | head -1 || true)"
fi
[ -n "$FT_LIB" ] || { echo "no FreeType library (install libfreetype-dev)"; exit 1; }

OUT=/tmp/panel_preview
mkdir -p "$OUT"

SOURCES="$HERE/main.cpp $HERE/png.cpp $HERE/host_assets.cpp
         $SRC/panel/panel_ui.cpp $SRC/panel/panel_draw.cpp
         $SRC/panel/panel_font.cpp $SRC/relay/relay_protocol.cpp"
CXXFLAGS="-std=gnu++17 -O1 -g -Wall -Wextra -Wno-unused-parameter
          -I$SRC/panel -I$SRC/relay -I$FT_INC"
g++ $CXXFLAGS -fsanitize=address,undefined -fno-omit-frame-pointer \
    $SOURCES $FT_LIB -o "$OUT/panel_test"
echo "=== tests"
# A full system font stands in for the one a player puts on the SD card; the
# test only needs any font with the coverage the subset lacks. Noto CJK comes
# first: it is the one CI installs for this (the package name varies between
# NotoSansCJK-Regular.ttc and the variable NotoSansCJK-VF.ttc), and it is the
# one with real Hangul.
EXTRA=""
for f in /usr/share/fonts/opentype/noto/NotoSansCJK*.tt[cf] \
         /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf \
         /mnt/c/Windows/Fonts/malgun.ttf; do
    [ -f "$f" ] && EXTRA="$f" && break
done
"$OUT/panel_test" "$FONT" --test $EXTRA

g++ $CXXFLAGS $SOURCES $FT_LIB -o "$OUT/panel_preview"
"$OUT/panel_preview" "$FONT" "$HERE/_panel_preview.png"
