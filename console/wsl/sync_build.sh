#!/usr/bin/env bash
# Mirror the edited sources into the WSL build tree and build.
# Editing happens on the Windows side; the toolchain lives here.
set -u

SRC_WIN="$(cd "$(dirname "$0")/../moonlight-n3ds" && pwd)"
WORK="$HOME/mn3ds"

export DEVKITPRO="$HOME/devkitpro"
export DEVKITARM="$DEVKITPRO/devkitARM"
export PATH="$DEVKITARM/bin:$DEVKITPRO/tools/bin:$PATH"

echo "=== sync ==="
# copy the whole source tree: this round touched files in several directories
rm -rf "$WORK/src"
cp -r "$SRC_WIN/src" "$WORK/src"
# 3ds/ holds the bottom-screen art (menu.png was relettered for the panel
# entry) and the embedded font in 3ds/data
rm -rf "$WORK/3ds"
cp -r "$SRC_WIN/3ds" "$WORK/3ds"
cp "$SRC_WIN/Makefile" "$WORK/Makefile"
echo "  src files: $(find "$WORK/src" -type f | wc -l)"
echo "  gfx      : $(ls "$WORK/3ds/gfx" | tr '\n' ' ')"

cd "$WORK" || exit 1

echo
echo "=== build ==="
# Clean first: the copied tree can leave a dependency file behind for a source
# that has since been renamed, and make then fails on a target that no longer
# exists.
make clean > /dev/null 2>&1
make -j"$(nproc)" > /tmp/build.log 2>&1
rc=$?

echo "exit=$rc"
grep -E "error:" /tmp/build.log | head -20
echo "--- tail ---"
tail -12 /tmp/build.log
if [ -f moonlight.3dsx ]; then
    echo
    echo "3dsx: $(ls -la moonlight.3dsx | awk '{print $5}') bytes"
fi
