#!/usr/bin/env python3
"""Open the album: Postgres in Docker, the rest on this machine, until the terminal closes.

The entrypoint for devkit's "Photolab: Open Album" task, and just as good by hand:

    python scripts/album.py [--no-open]

Not all in Docker, because the `codex` and `claude` commands and their logins are out here,
and AI images and subscription annotation both need them. A run here also has the machine's
whole memory, which the sharper cut-out model needs.

Three things follow from how devkit's dispatcher runs a task, the same three as ibkr_trader's
`ui-task.py`:

  - **Stdlib only.** It starts under the machine's `python`, not the project's, so nothing
    installed can be imported here. The servers get the project's interpreter through `uv`.
  - **Close the terminal to stop it.** `log-wrap.py` starts this with no console, so Ctrl+C
    never arrives, and closing the terminal ends only `log-wrap.py`: Windows does not kill a
    child with its parent. So this watches the process that launched it, and stops with it.
    Run by hand, that process is your shell, and Ctrl+C works too.
  - **The whole tree stops.** See `stop_tree`.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

_DEFAULT_API_PORT = "8007"
_DEFAULT_FRONTEND_PORT = "5180"

# A first run downloads Python packages and may install the frontend.
_READY_TIMEOUT_SECONDS = 180
_POLL_SECONDS = 0.5

# Windows: the right to wait on a process, and "wait forever".
_SYNCHRONIZE = 0x0010_0000
_INFINITE = 0xFFFF_FFFF


def read_dotenv(text: str) -> dict[str, str]:
    """`KEY=value` lines, which is all `.env` holds. Here, not python-dotenv: see the top."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.strip().partition("=")
        if separator and not key.startswith("#"):
            found[key.strip()] = value.strip().strip("\"'")
    return found


def build_environment(dotenv: dict[str, str], inherited: dict[str, str]) -> dict[str, str]:
    """`.env` under the process's own environment, plus what wires the two servers together."""
    merged = dotenv | inherited
    api_port = merged.get("APP_HOST_PORT", _DEFAULT_API_PORT)
    merged["API_PROXY_TARGET"] = f"http://127.0.0.1:{api_port}"
    merged["VITE_PORT"] = merged.get("FRONTEND_HOST_PORT", _DEFAULT_FRONTEND_PORT)
    return merged


def python_prefix(uv: str | None, root: Path = ROOT) -> list[str]:
    """What runs the project's Python. `uv run` also builds a `.venv` a fresh checkout lacks."""
    if uv:
        return [uv, "run", "python"]
    folder = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    candidate = root / ".venv" / folder
    return [str(candidate)] if candidate.is_file() else [sys.executable]


def setup_commands(python: list[str], npm: str, has_node_modules: bool) -> list[list[str]]:
    """What must finish before the servers start."""
    commands = [
        ["docker", "compose", "up", "--detach", "--wait", "db"],
        [*python, "-m", "alembic", "upgrade", "head"],
    ]
    if not has_node_modules:
        commands.append([npm, "--prefix", "frontend", "install", "--no-audit", "--no-fund"])
    return commands


def server_commands(
    python: list[str], node: str, env: dict[str, str]
) -> list[tuple[list[str], Path]]:
    """Each server and where it runs. Vite goes straight to node, with no npm above it."""
    port = env.get("APP_HOST_PORT", _DEFAULT_API_PORT)
    return [
        ([*python, "-m", "photolab", "serve", "--port", port], ROOT),
        ([node, "node_modules/vite/bin/vite.js"], ROOT / "frontend"),
    ]


def stop_tree(server: subprocess.Popen[Any]) -> None:
    """End a server and whatever it started.

    On Windows what Popen holds may only be a parent: `node` can be nvm's shim, a venv's
    `python` is a redirector, and `uv` is another layer. Ending that alone leaves the real
    server holding its port.
    """
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(server.pid)],
            capture_output=True,
            check=False,
        )
    else:
        server.terminate()
    server.wait()


def wait_for_exit(pid: int) -> None:
    """Block until process `pid` has ended; return at once if it already has.

    A handle on Windows, held from the start so that a reused PID cannot pass for the
    launcher, and never `os.kill(pid, 0)` there, which terminates the process it is asked
    about.
    """
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
        if not handle:
            return
        try:
            kernel32.WaitForSingleObject(handle, _INFINITE)
        finally:
            kernel32.CloseHandle(handle)
        return
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            pass
        time.sleep(_POLL_SECONDS)


def watch_launcher(pid: int, stop: threading.Event) -> None:
    """Set `stop` once process `pid` ends."""

    def run() -> None:
        wait_for_exit(pid)
        stop.set()

    threading.Thread(target=run, name=f"watch-pid-{pid}", daemon=True).start()


def run_servers(
    commands: list[tuple[list[str], Path]], env: dict[str, str], stop: threading.Event
) -> int:
    """Run both until either stops, or `stop` is set, and then stop what is left."""
    servers: list[subprocess.Popen[Any]] = [
        subprocess.Popen(command, cwd=cwd, env=env) for command, cwd in commands
    ]
    try:
        while not stop.is_set() and all(server.poll() is None for server in servers):
            stop.wait(_POLL_SECONDS)
    except KeyboardInterrupt:
        pass
    finally:
        stopped_by_itself = [server.poll() for server in servers]
        for server in servers:
            stop_tree(server)
    return next((code for code in stopped_by_itself if code), 0)


def is_up(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return bool(response.status == 200)
    except (OSError, urllib.error.URLError):
        return False


def open_when_ready(
    page: str,
    probe: Callable[[str], bool] = is_up,
    show: Callable[[str], Any] = webbrowser.open,
    timeout: float = _READY_TIMEOUT_SECONDS,
) -> bool:
    """Open `page` once the API answers through Vite's proxy, so both servers are up."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if probe(f"{page}/api/stats"):
            show(page)
            return True
        time.sleep(_POLL_SECONDS)
    return False


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the album on this machine.")
    parser.add_argument("--no-open", action="store_true", help="do not open a browser")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    # Read first: a first run's setup is slow, and the launcher may be gone by its end.
    stop = threading.Event()
    watch_launcher(os.getppid(), stop)
    dotenv = ROOT / ".env"
    if not dotenv.is_file():
        print("[album] no .env: copy .env.example and set PHOTO_LIBRARY_HOST_DIR", flush=True)
        return 1
    env = build_environment(read_dotenv(dotenv.read_text(encoding="utf-8")), dict(os.environ))
    python = python_prefix(shutil.which("uv"))
    npm = shutil.which("npm") or "npm"
    has_node_modules = (ROOT / "frontend" / "node_modules" / "vite").exists()
    for command in setup_commands(python, npm, has_node_modules):
        code = subprocess.run(command, cwd=ROOT, env=env, check=False).returncode
        if code:
            print(f"[album] failed with {code}: {' '.join(command)}", flush=True)
            return code
    page = f"http://localhost:{env['VITE_PORT']}"
    print(f"[album] {page}  (close this terminal, or Ctrl+C by hand, to stop)", flush=True)
    if not args.no_open:
        threading.Thread(target=open_when_ready, args=(page,), daemon=True).start()
    return run_servers(server_commands(python, shutil.which("node") or "node", env), env, stop)


if __name__ == "__main__":
    raise SystemExit(main())
