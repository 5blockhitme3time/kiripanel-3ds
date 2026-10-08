#!/usr/bin/env bash
# Build and run the host-side check for http_client.hpp against the real relay.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
PORT=8791

python3 - "$PORT" > /tmp/relay_http_test_server.log 2>&1 <<'PY' &
import sys, threading, time, http.server
sys.path.insert(0, "/mnt/d/kiri/pc")
from kiripanel import relay

port = int(sys.argv[1])
hub = relay.Hub(cmd_path=None)
relay.Handler.hub = hub
srv = relay.Server(("127.0.0.1", port), relay.Handler)
print("serving", flush=True)
srv.serve_forever()
PY
SERVER=$!
trap 'kill $SERVER 2>/dev/null' EXIT
sleep 1.5

g++ -std=gnu++17 -Wall -O1 -o /tmp/relay_http_test \
    -I "$HERE/../../console/moonlight-n3ds/src/relay" "$HERE/main.cpp" || exit 1
/tmp/relay_http_test "$PORT"
