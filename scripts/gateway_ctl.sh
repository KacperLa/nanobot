#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE_DIR="${NANOBOT_WORKSPACE:-$HOME/.nanobot}"
LOG_DIR="$WORKSPACE_DIR/logs"
PID_FILE="$WORKSPACE_DIR/gateway.pid"
SOCKET_PATH="${NANOBOT_API_SOCKET:-$WORKSPACE_DIR/api.sock}"
TMUX_SESSION="${NANOBOT_GATEWAY_SESSION:-nanobot-gateway}"

if [[ -x "$ROOT_DIR/venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/venv/bin/python"
elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
else
  echo "No nanobot virtualenv Python found under $ROOT_DIR/{venv,.venv}" >&2
  exit 1
fi

ACTION="${1:-status}"
shift || true

mkdir -p "$LOG_DIR"

read_pid_file() {
  if [[ -f "$PID_FILE" ]]; then
    tr -d '[:space:]' < "$PID_FILE"
  fi
}

running_pid() {
  local pid
  pid="$(read_pid_file)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "$pid"
    return 0
  fi
  return 1
}

discover_pid() {
  pgrep -f "$PYTHON_BIN -m nanobot gateway" | head -n 1 || true
}

cleanup_stale_state() {
  local pid
  pid="$(read_pid_file)"
  if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$PID_FILE"
  fi
  if [[ -S "$SOCKET_PATH" ]]; then
    local live_pid
    live_pid="$(discover_pid)"
    if [[ -z "$live_pid" ]]; then
      rm -f "$SOCKET_PATH"
    fi
  fi
}

tmux_session_running() {
  tmux has-session -t "$TMUX_SESSION" 2>/dev/null
}

build_gateway_command() {
  local log_path="$1"
  shift

  local cmd
  printf -v cmd 'cd %q && exec %q -m nanobot gateway' "$ROOT_DIR" "$PYTHON_BIN"
  for arg in "$@"; do
    printf -v cmd '%s %q' "$cmd" "$arg"
  done
  printf -v cmd '%s >> %q 2>&1' "$cmd" "$log_path"
  printf '%s' "$cmd"
}

start_gateway() {
  cleanup_stale_state
  local pid
  pid="$(running_pid || true)"
  if [[ -z "$pid" ]]; then
    pid="$(discover_pid)"
  fi
  if [[ -z "$pid" ]] && tmux_session_running; then
    pid="$(discover_pid)"
  fi
  if [[ -n "$pid" ]]; then
    echo "gateway already running (pid $pid)"
    echo "$pid" > "$PID_FILE"
    return 0
  fi

  rm -f "$SOCKET_PATH"
  local stamp log_path
  stamp="$(date +%Y%m%d-%H%M%S)"
  log_path="$LOG_DIR/gateway-$stamp.log"
  tmux kill-session -t "$TMUX_SESSION" 2>/dev/null || true
  tmux new-session -d -s "$TMUX_SESSION" "$(build_gateway_command "$log_path" "$@")"

  sleep 2
  pid="$(discover_pid)"
  if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
    echo "gateway failed to start; recent log:" >&2
    tail -n 40 "$log_path" >&2 || true
    exit 1
  fi
  echo "$pid" > "$PID_FILE"

  echo "gateway started (pid $pid)"
  echo "tmux session: $TMUX_SESSION"
  echo "log: $log_path"
}

stop_gateway() {
  cleanup_stale_state
  local pid
  pid="$(running_pid || true)"
  if [[ -z "$pid" ]]; then
    pid="$(discover_pid)"
  fi
  if tmux_session_running; then
    tmux kill-session -t "$TMUX_SESSION" || true
  fi
  if [[ -n "$pid" ]]; then
    kill "$pid" 2>/dev/null || true
    for _ in {1..20}; do
      if ! kill -0 "$pid" 2>/dev/null; then
        break
      fi
      sleep 0.25
    done
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  fi
  rm -f "$PID_FILE"
  if [[ -S "$SOCKET_PATH" ]]; then
    rm -f "$SOCKET_PATH"
  fi
  echo "gateway stopped"
}

status_gateway() {
  cleanup_stale_state
  local pid
  pid="$(running_pid || true)"
  if [[ -z "$pid" ]]; then
    pid="$(discover_pid)"
  fi
  if [[ -n "$pid" ]] || tmux_session_running; then
    echo "running pid=${pid:-unknown} socket=$SOCKET_PATH session=$TMUX_SESSION"
  else
    echo "stopped socket=$SOCKET_PATH"
  fi
}

case "$ACTION" in
  start)
    start_gateway "$@"
    ;;
  stop)
    stop_gateway
    ;;
  restart)
    stop_gateway
    start_gateway "$@"
    ;;
  status)
    status_gateway
    ;;
  *)
    echo "usage: $0 {start|stop|restart|status} [extra nanobot gateway args...]" >&2
    exit 1
    ;;
esac
