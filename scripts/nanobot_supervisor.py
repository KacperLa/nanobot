#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import TextIO


ROOT_DIR = Path(__file__).resolve().parents[1]
WORKSPACE_DIR = Path(os.getenv("NANOBOT_WORKSPACE", str(Path.home() / ".nanobot"))).expanduser()
LOG_DIR = WORKSPACE_DIR / "logs"
SUPERVISOR_PID_PATH = WORKSPACE_DIR / "nanobot-supervisor.pid"
GATEWAY_PID_PATH = WORKSPACE_DIR / "gateway.pid"
API_SOCKET_PATH = Path(
    os.getenv("NANOBOT_API_SOCKET", str(WORKSPACE_DIR / "api.sock"))
).expanduser()
SUPERVISOR_LOG_PATH = LOG_DIR / "nanobot-supervisor.log"
DEFAULT_RESTART_DELAY_SECONDS = 3.0
DEFAULT_HEALTH_CHECK_INTERVAL_SECONDS = 10.0
DEFAULT_STARTUP_GRACE_SECONDS = 12.0
CHILD_SHUTDOWN_TIMEOUT_SECONDS = 8.0


def resolve_python_bin() -> Path:
    for candidate in (ROOT_DIR / "venv" / "bin" / "python", ROOT_DIR / ".venv" / "bin" / "python"):
        if candidate.exists():
            return candidate
    raise RuntimeError(f"no virtualenv Python found under {ROOT_DIR}/venv or {ROOT_DIR}/.venv")


def timestamp() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def is_pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_pid(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def write_pid(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n", encoding="utf-8")


def remove_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def log_line(handle: TextIO, message: str) -> None:
    line = f"{timestamp()} {message}"
    print(line, file=handle, flush=True)
    if handle is not sys.stdout and sys.stdout.isatty():
        print(line, flush=True)


def open_supervisor_log() -> TextIO:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return SUPERVISOR_LOG_PATH.open("a", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Supervise the local nanobot gateway.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Start the supervisor in the background.")
    start.add_argument("--restart-delay", type=float, default=DEFAULT_RESTART_DELAY_SECONDS)
    start.add_argument("--health-check-interval", type=float, default=DEFAULT_HEALTH_CHECK_INTERVAL_SECONDS)
    start.add_argument("--startup-grace", type=float, default=DEFAULT_STARTUP_GRACE_SECONDS)

    run = subparsers.add_parser("run", help="Run the supervisor in the foreground.")
    run.add_argument("--restart-delay", type=float, default=DEFAULT_RESTART_DELAY_SECONDS)
    run.add_argument("--health-check-interval", type=float, default=DEFAULT_HEALTH_CHECK_INTERVAL_SECONDS)
    run.add_argument("--startup-grace", type=float, default=DEFAULT_STARTUP_GRACE_SECONDS)

    subparsers.add_parser("stop", help="Stop the running supervisor.")
    subparsers.add_parser("status", help="Show supervisor status.")
    subparsers.add_parser("restart", help="Restart the supervisor.")
    return parser


def launch_background(args: argparse.Namespace) -> int:
    existing_pid = read_pid(SUPERVISOR_PID_PATH)
    if existing_pid and is_pid_running(existing_pid):
        print(f"nanobot supervisor already running (pid {existing_pid})")
        return 0

    remove_file(SUPERVISOR_PID_PATH)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "run",
        "--restart-delay",
        str(args.restart_delay),
        "--health-check-interval",
        str(args.health_check_interval),
        "--startup-grace",
        str(args.startup_grace),
    ]
    with SUPERVISOR_LOG_PATH.open("a", encoding="utf-8") as log_handle:
        proc = subprocess.Popen(
            command,
            cwd=str(ROOT_DIR),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    time.sleep(1.0)
    if proc.poll() is not None:
        print("nanobot supervisor failed to start")
        return 1
    print(f"nanobot supervisor started (pid {proc.pid})")
    print(f"log: {SUPERVISOR_LOG_PATH}")
    return 0


def stop_supervisor() -> int:
    pid = read_pid(SUPERVISOR_PID_PATH)
    if not pid or not is_pid_running(pid):
        remove_file(SUPERVISOR_PID_PATH)
        print("nanobot supervisor not running")
        return 0

    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + CHILD_SHUTDOWN_TIMEOUT_SECONDS
    while time.time() < deadline:
        if not is_pid_running(pid):
            break
        time.sleep(0.25)
    if is_pid_running(pid):
        os.kill(pid, signal.SIGKILL)
    remove_file(SUPERVISOR_PID_PATH)
    print("nanobot supervisor stopped")
    return 0


def supervisor_status() -> int:
    pid = read_pid(SUPERVISOR_PID_PATH)
    gateway_pid = read_pid(GATEWAY_PID_PATH)
    supervisor_running = bool(pid and is_pid_running(pid))
    gateway_running = bool(gateway_pid and is_pid_running(gateway_pid))
    socket_exists = API_SOCKET_PATH.exists()
    if supervisor_running:
        print(
            f"running supervisor_pid={pid} gateway_pid={gateway_pid or 'unknown'} "
            f"socket={API_SOCKET_PATH} socket_exists={socket_exists}"
        )
    else:
        print(
            f"stopped supervisor_pid={pid or 'none'} gateway_pid={gateway_pid or 'none'} "
            f"socket={API_SOCKET_PATH} socket_exists={socket_exists}"
        )
    return 0


def probe_api_socket(socket_path: Path) -> bool:
    if not socket_path.exists():
        return False
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(2.0)
    try:
        sock.connect(str(socket_path))
        sock.sendall(
            (
                json.dumps({"jsonrpc": "2.0", "id": "supervisor", "method": "ping", "params": {}})
                + "\n"
            ).encode("utf-8")
        )
        data = sock.recv(8192).decode("utf-8", errors="replace").strip()
    except OSError:
        return False
    finally:
        sock.close()
    return '"pong": true' in data


def remove_stale_socket() -> None:
    if not API_SOCKET_PATH.exists():
        return
    if probe_api_socket(API_SOCKET_PATH):
        return
    try:
        API_SOCKET_PATH.unlink()
    except OSError:
        return


def terminate_child(proc: subprocess.Popen[bytes], handle: TextIO, *, reason: str) -> None:
    if proc.poll() is not None:
        return
    log_line(handle, f"terminating gateway pid={proc.pid} reason={reason}")
    proc.terminate()
    try:
        proc.wait(timeout=CHILD_SHUTDOWN_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        log_line(handle, f"killing unresponsive gateway pid={proc.pid}")
        proc.kill()
        proc.wait(timeout=5)


def supervise(args: argparse.Namespace) -> int:
    python_bin = resolve_python_bin()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stop_requested = False
    current_child: subprocess.Popen[bytes] | None = None

    def request_stop(signum: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    with open_supervisor_log() as supervisor_log:
        write_pid(SUPERVISOR_PID_PATH, os.getpid())
        log_line(supervisor_log, "supervisor started")
        try:
            while not stop_requested:
                remove_stale_socket()
                run_stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
                run_log_path = LOG_DIR / f"gateway-run-{run_stamp}.log"
                run_log = run_log_path.open("ab")
                try:
                    current_child = subprocess.Popen(
                        [str(python_bin), "-m", "nanobot", "gateway"],
                        cwd=str(ROOT_DIR),
                        stdin=subprocess.DEVNULL,
                        stdout=run_log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                finally:
                    run_log.close()
                write_pid(GATEWAY_PID_PATH, current_child.pid)
                log_line(
                    supervisor_log,
                    f"gateway started pid={current_child.pid} log={run_log_path}",
                )

                started_at = time.time()
                next_health_check = started_at + args.health_check_interval
                unhealthy_reason = ""
                while not stop_requested:
                    exit_code = current_child.poll()
                    if exit_code is not None:
                        log_line(
                            supervisor_log,
                            f"gateway exited pid={current_child.pid} code={exit_code}",
                        )
                        break
                    now = time.time()
                    if (
                        now >= next_health_check
                        and now - started_at >= args.startup_grace
                    ):
                        next_health_check = now + args.health_check_interval
                        if not probe_api_socket(API_SOCKET_PATH):
                            unhealthy_reason = "api socket health check failed"
                            terminate_child(current_child, supervisor_log, reason=unhealthy_reason)
                            break
                    time.sleep(0.5)

                remove_file(GATEWAY_PID_PATH)
                if stop_requested:
                    if current_child and current_child.poll() is None:
                        terminate_child(current_child, supervisor_log, reason="supervisor stopping")
                    break

                delay = max(0.5, args.restart_delay)
                if unhealthy_reason:
                    log_line(
                        supervisor_log,
                        f"restarting gateway after unhealthy state in {delay:.1f}s",
                    )
                else:
                    log_line(
                        supervisor_log,
                        f"restarting gateway after exit in {delay:.1f}s",
                    )
                time.sleep(delay)
        finally:
            if current_child and current_child.poll() is None:
                terminate_child(current_child, supervisor_log, reason="supervisor shutdown")
            remove_file(GATEWAY_PID_PATH)
            remove_file(SUPERVISOR_PID_PATH)
            if API_SOCKET_PATH.exists() and not probe_api_socket(API_SOCKET_PATH):
                remove_stale_socket()
            log_line(supervisor_log, "supervisor stopped")
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "start":
        return launch_background(args)
    if args.command == "run":
        return supervise(args)
    if args.command == "stop":
        return stop_supervisor()
    if args.command == "status":
        return supervisor_status()
    if args.command == "restart":
        stop_supervisor()
        start_args = argparse.Namespace(
            restart_delay=DEFAULT_RESTART_DELAY_SECONDS,
            health_check_interval=DEFAULT_HEALTH_CHECK_INTERVAL_SECONDS,
            startup_grace=DEFAULT_STARTUP_GRACE_SECONDS,
        )
        return launch_background(start_args)
    parser.error(f"unsupported command: {args.command}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
