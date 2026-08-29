#!/bin/bash
set -euo pipefail

PROJECT_DIR="/Users/zhoulin/Documents/alphaLab/etf_strategy"
PYTHON_BIN="/opt/miniconda3/bin/python"
DB_PATH="$PROJECT_DIR/data/processed/etf_strategy.duckdb"
HOST="127.0.0.1"
PORT="8765"
URL="http://$HOST:$PORT/"
LOG_DIR="$HOME/Library/Logs/ETFStrategy"
LOG_FILE="$LOG_DIR/dashboard.log"
LAUNCHER_LOG="$LOG_DIR/launcher.log"
PID_FILE="$LOG_DIR/dashboard.pid"

mkdir -p "$LOG_DIR"
touch "$LAUNCHER_LOG"
exec >>"$LAUNCHER_LOG" 2>&1
echo "[$(/bin/date '+%Y-%m-%d %H:%M:%S')] launcher start"

notify() {
  /usr/bin/osascript -e "display notification \"$1\" with title \"ETFStrategy\"" >/dev/null 2>&1 || true
}

open_dashboard() {
  echo "opening browser: $URL"
  /usr/bin/open "$URL" || true
  if [[ -d "/Applications/Google Chrome.app" ]]; then
    /usr/bin/open -a "Google Chrome" "$URL" || true
    /usr/bin/osascript <<APPLESCRIPT || true
tell application "Google Chrome"
  activate
  if (count of windows) = 0 then make new window
  set URL of active tab of front window to "$URL"
  delay 1
  get URL of active tab of front window
end tell
APPLESCRIPT
  elif [[ -d "/Applications/Safari.app" ]]; then
    /usr/bin/open -a "Safari" "$URL" || true
    /usr/bin/osascript <<APPLESCRIPT || true
tell application "Safari"
  activate
  if (count of documents) = 0 then make new document
  set URL of front document to "$URL"
  delay 1
  get URL of front document
end tell
APPLESCRIPT
  fi
  echo "browser open requested"
}

is_dashboard_ready() {
  /usr/bin/curl -fsS --max-time 2 "$URL" >/dev/null 2>&1
}

if is_dashboard_ready; then
  open_dashboard
  notify "Dashboard 已打开"
  echo "dashboard already ready"
  exit 0
fi

if [[ -f "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${OLD_PID:-}" ]] && /bin/kill -0 "$OLD_PID" >/dev/null 2>&1; then
    echo "stale pid file points to running pid=$OLD_PID, but dashboard is not ready"
    /bin/kill "$OLD_PID" >/dev/null 2>&1 || true
    /bin/sleep 1
  fi
fi

cd "$PROJECT_DIR"
echo "starting dashboard as detached daemon..."
: > "$LOG_FILE"
DAEMONIZER="$PROJECT_DIR/launchers/start_dashboard_daemon.py"
SERVICE_PID="$(/usr/bin/python3 "$DAEMONIZER" \
  --python-bin "$PYTHON_BIN" \
  --script "$PROJECT_DIR/run_dashboard.py" \
  --db "$DB_PATH" \
  --host "$HOST" \
  --port "$PORT" \
  --workdir "$PROJECT_DIR" \
  --log-file "$LOG_FILE" \
  --pid-file "$PID_FILE")"
echo "$SERVICE_PID" >"$PID_FILE"
echo "service pid=$SERVICE_PID"

for _ in {1..80}; do
  if is_dashboard_ready; then
    LISTENER_PID="$(/usr/sbin/lsof -tiTCP:$PORT -sTCP:LISTEN 2>/dev/null | /usr/bin/head -1 || true)"
    if [[ -n "${LISTENER_PID:-}" ]]; then
      echo "$LISTENER_PID" >"$PID_FILE"
    fi
    open_dashboard
    notify "后台服务已启动"
    echo "dashboard ready pid=${LISTENER_PID:-$SERVICE_PID}"
    exit 0
  fi
  if ! /bin/kill -0 "$SERVICE_PID" >/dev/null 2>&1; then
    echo "service exited before ready"
    echo "--- dashboard.log ---"
    tail -80 "$LOG_FILE" 2>/dev/null || true
    exit 1
  fi
  /bin/sleep 0.5
done

/usr/bin/open "$LOG_FILE"
notify "启动失败，请查看日志"
echo "dashboard failed to become ready"
exit 1
