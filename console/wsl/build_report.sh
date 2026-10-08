#!/usr/bin/env bash
# Sync + build, then show only diagnostics from the files this project owns.
bash "$(dirname "$0")/wsl_sync_build.sh" > /tmp/sync.out 2>&1
grep -E "^(exit=|3dsx:)" /tmp/sync.out
echo "--- errors"
grep -E "error" /tmp/build.log | head -30
echo "--- warnings in project files"
grep -E "(panel|relay|Dialogue|N3dsTouchscreen|N3dsRenderer|n3ds_input|config\.cpp|n3ds_main)[^:]*:[0-9]+:[0-9]+: warning" /tmp/build.log | sort -u | head -30
