#!/usr/bin/env bash
# Copy the built artifacts out of the WSL build tree.
set -u

OUT="$(cd "$(dirname "$0")/../.." && pwd)/dist/3ds"

mkdir -p "$OUT"
for f in moonlight.3dsx moonlight.cia moonlight.elf; do
    src="$HOME/mn3ds/$f"
    if [ -f "$src" ]; then
        cp "$src" "$OUT/"
        printf '  %-18s %8.2f MB\n' "$f" "$(stat -c %s "$src" | awk '{print $1/1048576}')"
    else
        printf '  %-18s MISSING\n' "$f"
    fi
done
