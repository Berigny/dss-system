#!/usr/bin/env python3
"""Native demo wrapper for ds-frontend-local using pywebview.

Behavior:
- starts the full stack via `make launch-all`
- waits for frontend health to become ready
- opens a native macOS window to the local UI
- runs `make kill-all` on window close or process termination
"""

from __future__ import annotations

import atexit
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

try:
    import webview
except ImportError as exc:
    raise SystemExit(
        "pywebview is not installed in this environment. "
        "Install it with: source .venv/bin/activate && pip install pywebview"
    ) from exc

ROOT_DIR = Path(__file__).resolve().parent
MAKE_CMD = os.getenv("DEMO_MAKE_CMD", "make")
LAUNCH_TARGET = os.getenv("DEMO_LAUNCH_TARGET", "launch-all")
UI_URL = os.getenv("DEMO_UI_URL", "")
HEALTH_URL = os.getenv("DEMO_HEALTH_URL", f"{UI_URL}/health")
BOOT_TIMEOUT_SECONDS = int(os.getenv("DEMO_BOOT_TIMEOUT", "300"))
WINDOW_TITLE = os.getenv("DEMO_WINDOW_TITLE", "DS Frontend Demo")
STACK_LOG_PATH = Path(os.getenv("DEMO_STACK_LOG", "/tmp/ds-demo-stack.log"))

stack_process: subprocess.Popen[str] | None = None
_cleanup_started = threading.Event()


def _run_make(target: str, timeout: int = 90) -> None:
    subprocess.run(
        [MAKE_CMD, target],
        cwd=ROOT_DIR,
        check=False,
        timeout=timeout,
    )


def _wait_for_ui(timeout_seconds: int) -> str:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if stack_process and stack_process.poll() is not None:
            return "stack-exited"
        try:
            with urlopen(HEALTH_URL, timeout=2) as response:  # nosec B310
                if response.status == 200:
                    return "ready"
        except URLError:
            pass
        except Exception:
            pass
        time.sleep(1)
    return "timeout"


def _cleanup(reason: str) -> None:
    if _cleanup_started.is_set():
        return
    _cleanup_started.set()

    print(f"[{WINDOW_TITLE}] Cleanup triggered: {reason}")
    try:
        _run_make("kill-all")
    except Exception as exc:
        print(f"[{WINDOW_TITLE}] kill-all failed: {exc}", file=sys.stderr)

    if stack_process and stack_process.poll() is None:
        stack_process.terminate()
        try:
            stack_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            stack_process.kill()


def _handle_signal(signum: int, _frame: object) -> None:
    _cleanup(f"signal {signum}")
    raise SystemExit(0)


def main() -> int:
    global stack_process

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    atexit.register(lambda: _cleanup("atexit"))

    STACK_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    stack_log = STACK_LOG_PATH.open("w", encoding="utf-8")

    print(f"[{WINDOW_TITLE}] Starting stack via `{MAKE_CMD} {LAUNCH_TARGET}`...")
    print(f"[{WINDOW_TITLE}] Stack logs: {STACK_LOG_PATH}")
    stack_process = subprocess.Popen(
        [MAKE_CMD, LAUNCH_TARGET],
        cwd=ROOT_DIR,
        stdout=stack_log,
        stderr=subprocess.STDOUT,
        text=True,
    )

    print(f"[{WINDOW_TITLE}] Waiting for UI health at {HEALTH_URL}...")
    boot_state = _wait_for_ui(BOOT_TIMEOUT_SECONDS)
    if boot_state != "ready":
        if boot_state == "stack-exited":
            return_code = stack_process.poll() if stack_process else "unknown"
            print(
                f"[{WINDOW_TITLE}] Stack exited before UI became ready (code {return_code}). "
                f"See logs: {STACK_LOG_PATH}",
                file=sys.stderr,
            )
        else:
            print(
                f"[{WINDOW_TITLE}] UI was not ready after {BOOT_TIMEOUT_SECONDS}s. "
                f"See logs: {STACK_LOG_PATH}",
                file=sys.stderr,
            )
        stack_log.flush()
        stack_log.close()
        _cleanup(boot_state)
        return 1

    stack_log.flush()
    stack_log.close()

    if not (stack_process and stack_process.poll() is None):
        print(
            f"[{WINDOW_TITLE}] UI ready check passed but stack is not running. See logs: {STACK_LOG_PATH}",
            file=sys.stderr,
        )
        _cleanup("stack-not-running")
        return 1

    print(f"[{WINDOW_TITLE}] Opening native window at {UI_URL}")
    window = webview.create_window(
        title=WINDOW_TITLE,
        url=UI_URL,
        width=1280,
        height=820,
        min_size=(980, 640),
    )
    window.events.closed += lambda: _cleanup("window-closed")

    try:
        webview.start()
    finally:
        _cleanup("webview-exit")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
