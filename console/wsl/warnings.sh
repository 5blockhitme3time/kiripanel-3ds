#!/usr/bin/env bash
# Rebuild the given console sources and show only their warnings/errors.
#   bash warnings.sh panel/panel_font.cpp panel/panel_controller.cpp
set -u
export DEVKITPRO="$HOME/devkitpro"
export DEVKITARM="$DEVKITPRO/devkitARM"
export PATH="$DEVKITARM/bin:$DEVKITPRO/tools/bin:$PATH"
cd "$HOME/mn3ds" || exit 1
for f in "$@"; do touch "src/$f"; done
make > $HOME/warn.log 2>&1
echo "exit=$?"
for f in "$@"; do
    grep -E "$(basename "$f"):[0-9]+:[0-9]+: (warning|error)" $HOME/warn.log | sort -u
done
echo "(end)"
