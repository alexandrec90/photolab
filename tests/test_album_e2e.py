"""The album, opened on this machine the way devkit's task opens it, end to end.

In the local tier (`scripts/e2e.py`, `.claude/rules/local-e2e.md`): it needs the Docker
daemon for Postgres, node for Vite, uv and this checkout's `.env`, none of which a hosted
runner has. It is the run CLAUDE.md asks for after any change to how the album starts or
stops, because the Docker stack never showed what breaks natively: uvicorn's default loop
killing psycopg on Windows, or a stop that orphans the real `node` and `python` and leaves
them holding the ports.

`scripts/album.py` is started under a stand-in for the terminal, which is then killed
alone, as closing the terminal does. The album is pointed at spare ports, a library of one
photo in a temporary folder and a database of its own on the album's Postgres, created
for the run and dropped after it, so a run can neither collide with an open album nor
touch the one whose annotations cost money to produce.
"""

import os
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, make_url, text

from tests.conftest import database_url, jpeg_bytes
from tests.test_album_script import SCRIPT, album

pytestmark = pytest.mark.local

ROOT = Path(__file__).resolve().parent.parent

# A first open on this machine may sync the venv and install the frontend.
READY_SECONDS = 300
SCAN_SECONDS = 60
STOP_SECONDS = 60
POLL_SECONDS = 0.5

# What devkit's task runs the album under: the machine's interpreter, not the venv's. On
# Windows a venv's `python.exe` is a redirector that starts the real one as its child, so a
# terminal run under it is two processes: killing the one Popen holds left the real one
# alive, and the album, watching its own parent, never saw its terminal close.
MACHINE_PYTHON = getattr(sys, "_base_executable", sys.executable)

# The terminal devkit's task opens: it starts the album and waits, and nothing else.
TERMINAL = (
    "import subprocess, sys; "
    "album = subprocess.Popen([sys.executable, sys.argv[1], '--no-open']); "
    "open(sys.argv[2], 'w').write(str(album.pid)); "
    "album.wait()"
)


def spare_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def answers(port: int) -> bool:
    """Whether anything accepts a connection on `port`, over either loopback family."""
    try:
        with socket.create_connection(("localhost", port), timeout=1):
            return True
    except OSError:
        return False


def tail(log: Path, lines: int = 60) -> str:
    try:
        text_ = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(no output)"
    return "\n".join(text_.splitlines()[-lines:])


def wait_for(check: Callable[[], bool], seconds: float, what: str, log: Path) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(POLL_SECONDS)
    pytest.fail(f"{what} within {seconds:.0f}s; the album printed:\n{tail(log)}")


@pytest.fixture
def album_database() -> Iterator[str]:
    """A database of the run's own on the album's Postgres, which this starts as album.py does."""
    started = subprocess.run(
        ["docker", "compose", "up", "--detach", "--wait", "db"],  # noqa: S607 - docker on PATH is the point
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if started.returncode:
        pytest.fail(
            "the album's Postgres did not start -- is Docker Desktop running?\n"
            + (started.stderr or started.stdout)[-2000:]
        )
    url = make_url(database_url())
    name = f"photolab_e2e_{uuid.uuid4().hex[:12]}"
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield url.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def force_stop(pid: int) -> None:
    """End an album the test could not stop the proper way, and what it started."""
    if sys.platform == "win32":
        subprocess.run(  # noqa: S603 - a pid this test started
            ["taskkill", "/T", "/F", "/PID", str(pid)],  # noqa: S607 - a Windows built-in
            capture_output=True,
            check=False,
        )
    else:
        # The album turns an interrupt into stopping its servers' process groups.
        try:
            os.kill(pid, signal.SIGINT)
        except ProcessLookupError:
            pass


def test_the_album_opens_scans_serves_and_stops_with_its_terminal(
    album_database: str, tmp_path: Path
):
    library = tmp_path / "library"
    library.mkdir()
    original = jpeg_bytes((30, 140, 200), (96, 64))
    (library / "lake.jpg").write_bytes(original)
    data = tmp_path / "data"
    data.mkdir()
    api_port, page_port = spare_port(), spare_port()
    env = os.environ | {
        "DATABASE_URL": album_database,
        "PHOTO_LIBRARY_DIR": str(library),
        "PHOTO_LIBRARY_HOST_DIR": str(library),
        "PHOTOLAB_DATA_DIR": str(data),
        "PHOTOLAB_DATA_HOST_DIR": str(data),
        "APP_HOST_PORT": str(api_port),
        "FRONTEND_HOST_PORT": str(page_port),
    }
    log, pid_file = tmp_path / "album.log", tmp_path / "album.pid"
    with log.open("w", encoding="utf-8") as output:
        terminal = subprocess.Popen(  # noqa: S603 - this test's own constants, not input
            [MACHINE_PYTHON, "-c", TERMINAL, str(SCRIPT), str(pid_file)],
            cwd=ROOT,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
    album_pid = 0
    gone = threading.Event()

    def watch() -> None:
        album.wait_for_exit(album_pid)
        gone.set()

    try:
        wait_for(pid_file.is_file, 30, "the album did not start", log)
        album_pid = int(pid_file.read_text(encoding="utf-8"))
        threading.Thread(target=watch, daemon=True).start()
        page = f"http://localhost:{page_port}"
        with httpx.Client(base_url=page, timeout=10) as http:

            def ready() -> bool:
                if gone.is_set():
                    pytest.fail(f"the album exited before it was up:\n{tail(log)}")
                try:
                    return http.get("/api/stats").status_code == 200
                except httpx.HTTPError:
                    return False

            # Through Vite's proxy, so both servers and the migrations are up.
            wait_for(ready, READY_SECONDS, f"{page}/api/stats did not answer", log)
            shell = http.get("/")
            assert shell.status_code == 200
            assert '<div id="root">' in shell.text

            assert http.post("/api/library/scan").status_code == 202
            wait_for(
                lambda: http.get("/api/photos").json()["total"] == 1,
                SCAN_SECONDS,
                "the scan did not list the library's one photo",
                log,
            )
            (photo,) = http.get("/api/photos").json()["items"]
            image = http.get(f"/api/photos/{photo['id']}/image")
            assert image.status_code == 200
            assert image.content == original
            thumb = http.get(f"/api/photos/{photo['id']}/thumb")
            assert thumb.status_code == 200
            assert thumb.headers["content-type"] == "image/webp"

        # Closing the terminal ends it alone: Windows does not kill a child with its parent.
        terminal.kill()
        terminal.wait()
        if not gone.wait(STOP_SECONDS):
            pytest.fail(f"the album outlived its terminal by {STOP_SECONDS}s:\n{tail(log)}")
        wait_for(
            lambda: not answers(api_port) and not answers(page_port),
            10,
            f"a server still holds port {api_port} or {page_port} after the album stopped",
            log,
        )
    finally:
        if terminal.poll() is None:
            terminal.kill()
            terminal.wait()
        # Never once it has gone: its pid may since be someone else's.
        if album_pid and not gone.is_set():
            force_stop(album_pid)
