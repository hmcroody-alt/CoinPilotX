#!/usr/bin/env python3
"""Run the local PulseSoc web process outside transient terminal sessions."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 5069
MAX_RESTARTS_PER_WINDOW = 5
RESTART_WINDOW_SECONDS = 60
CIRCUIT_BREAKER_SECONDS = 60


def runtime_paths(port: int) -> dict[str, Path]:
    prefix = Path(f"/tmp/pulsesoc-local-{port}")
    return {
        "lock": prefix.with_suffix(".lock"),
        "guard_pid": prefix.with_suffix(".guard.pid"),
        "child_pid": prefix.with_suffix(".pid"),
        "log": prefix.with_suffix(".log"),
    }


def read_pid(path: Path) -> int:
    try:
        return int(path.read_text().strip())
    except (OSError, TypeError, ValueError):
        return 0


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def health_ok(port: int, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def append_log(path: Path, message: str) -> None:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} LOCAL_GUARD {message}\n")


def daemonize() -> None:
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    os.chdir(ROOT)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, sys.stdin.fileno())


def server_environment(port: int) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "PORT": str(port),
            "BOT_TOKEN": env.get("BOT_TOKEN") or "invalid",
            "TELEGRAM_BOT_TOKEN": env.get("TELEGRAM_BOT_TOKEN") or "invalid",
            "EMAIL_OPPORTUNISTIC_PROCESSOR_ENABLED": "0",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return env


def run_guard(port: int) -> int:
    paths = runtime_paths(port)
    lock_handle = paths["lock"].open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"PulseSoc local guard is already running on port {port}.", file=sys.stderr)
        return 1

    paths["guard_pid"].write_text(str(os.getpid()), encoding="utf-8")
    stopping = False
    child: subprocess.Popen[bytes] | None = None

    def stop_handler(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
        if child and child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    restarts: deque[float] = deque()

    try:
        while not stopping:
            now = time.monotonic()
            while restarts and now - restarts[0] > RESTART_WINDOW_SECONDS:
                restarts.popleft()
            if len(restarts) >= MAX_RESTARTS_PER_WINDOW:
                append_log(paths["log"], f"circuit_open restarts={len(restarts)} sleep={CIRCUIT_BREAKER_SECONDS}s")
                time.sleep(CIRCUIT_BREAKER_SECONDS)
                restarts.clear()
                continue

            with paths["log"].open("ab", buffering=0) as log_handle:
                child = subprocess.Popen(
                    [str(ROOT / "venv/bin/python"), str(ROOT / "bot.py")],
                    cwd=ROOT,
                    env=server_environment(port),
                    stdin=subprocess.DEVNULL,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                paths["child_pid"].write_text(str(child.pid), encoding="utf-8")
                append_log(paths["log"], f"server_started pid={child.pid} port={port}")
                exit_code = child.wait()

            paths["child_pid"].unlink(missing_ok=True)
            child = None
            if stopping:
                break
            restarts.append(time.monotonic())
            delay = min(15, 2 ** max(0, len(restarts) - 1))
            append_log(paths["log"], f"server_exited code={exit_code} restart_in={delay}s")
            time.sleep(delay)
    finally:
        if child and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
        paths["child_pid"].unlink(missing_ok=True)
        paths["guard_pid"].unlink(missing_ok=True)
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()
    return 0


def status(port: int) -> int:
    paths = runtime_paths(port)
    payload = {
        "port": port,
        "guard_pid": read_pid(paths["guard_pid"]),
        "server_pid": read_pid(paths["child_pid"]),
    }
    payload["guard_running"] = process_alive(payload["guard_pid"])
    payload["server_running"] = process_alive(payload["server_pid"])
    payload["health_ok"] = health_ok(port)
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["guard_running"] and payload["server_running"] and payload["health_ok"] else 1


def stop(port: int) -> int:
    paths = runtime_paths(port)
    guard_pid = read_pid(paths["guard_pid"])
    if not process_alive(guard_pid):
        paths["guard_pid"].unlink(missing_ok=True)
        print(f"PulseSoc local guard is not running on port {port}.")
        return 0
    os.kill(guard_pid, signal.SIGTERM)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and process_alive(guard_pid):
        time.sleep(0.2)
    print(f"PulseSoc local guard stopped on port {port}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    if args.status:
        return status(args.port)
    if args.stop:
        return stop(args.port)
    if args.daemon:
        daemonize()
    return run_guard(args.port)


if __name__ == "__main__":
    raise SystemExit(main())
