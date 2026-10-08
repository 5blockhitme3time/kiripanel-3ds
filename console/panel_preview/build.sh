#!/usr/bin/env bash
# Build the panel harness natively in WSL and run it.
#
#   bash build.sh            tests (under AddressSanitizer) + preview sheet
#                            (wsl bash console/panel_preview/build.sh)
#
# Uses the console's own sources. There is no FreeType -dev package on this
# host and no network to install one, so the headers come from devkitPro's
# portlibs (FreeType's public API is the same on both) and the library is the
# system's libfreetype.so.6.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/../moonlight-n3ds/src"
FONT="$HERE/../moonlight-n3ds/3ds/data/hkfont.ttf"
FT_INC="$HOME/devkitpro/portlibs/3ds/include/freetype2"
OUT=/tmp/panel_preview
mkdir -p "$OUT"

FT_LIB=$(ls /usr/lib/x86_64-linux-gnu/libfreetype.so.6 2>/dev/null || true)
[ -n "$FT_LIB" ] || { echo "no libfreetype.so.6 on this host"; exit 1; }

SOURCES="$HERE/main.cpp $HERE/png.cpp $HERE/host_assets.cpp
         $SRC/panel/panel_ui.cpp $SRC/panel/panel_draw.cpp
         $SRC/panel/panel_font.cpp $SRC/relay/relay_protocol.cpp"
CXXFLAGS="-std=gnu++17 -O1 -g -Wall -Wextra -Wno-unused-parameter
          -I$SRC/panel -I$SRC/relay -I$FT_INC"

g++ $CXXFLAGS -fsanitize=address,undefined -fno-omit-frame-pointer \
    $SOURCES "$FT_LIB" -o "$OUT/panel_test"
echo "=== tests"
# a full system font stands in for the one a player puts on the SD card
EXTRA=$(ls /mnt/c/Windows/Fonts/malgun.ttf 2>/dev/null || true)
"$OUT/panel_test" "$FONT" --test $EXTRA

g++ $CXXFLAGS $SOURCES "$FT_LIB" -o "$OUT/panel_preview"
"$OUT/panel_preview" "$FONT" "$HERE/_panel_preview.png"
