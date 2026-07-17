#!/usr/bin/env bash
# Rebuild any missing price-history sidecars while the current webapp keeps
# serving, then swap in a fresh server process. Since the sidecars already
# exist by the time the new process starts, its own startup pass
# (_ensure_price_histories in webapp/app.py) is a no-op and it comes up
# immediately instead of blocking for however long the build takes.
#
# Usage: scripts/restart_webapp.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_PATH="$REPO_ROOT/webapp/app.py"
WEBAPP_LOG="$REPO_ROOT/webapp/webapp.log"
PORT=5000

echo "[1/3] Building missing price-history sidecars..."
python3 "$REPO_ROOT/scripts/build_price_history.py"

port_pids() {
    ss -ltnp "sport = :$PORT" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u || true
}

echo "[2/3] Restarting webapp..."
pids="$(port_pids)"
if [ -n "$pids" ]; then
    echo "  stopping existing process(es): $pids"
    kill $pids 2>/dev/null || true
    for _ in $(seq 1 10); do
        [ -z "$(port_pids)" ] && break
        sleep 0.5
    done
    pids="$(port_pids)"
    if [ -n "$pids" ]; then
        echo "  force-killing: $pids"
        kill -9 $pids 2>/dev/null || true
        sleep 1
    fi
fi

cd "$REPO_ROOT/webapp"
nohup python3 "$APP_PATH" > "$WEBAPP_LOG" 2>&1 &
disown

echo "[3/3] Waiting for webapp to come up..."
for _ in $(seq 1 20); do
    if curl -s -o /dev/null "http://127.0.0.1:$PORT/"; then
        echo "Webapp is up at http://127.0.0.1:$PORT/"
        exit 0
    fi
    sleep 1
done

echo "Webapp did not come up within 20s -- check $WEBAPP_LOG" >&2
exit 1
