import ast
import http.server
import importlib.util
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "album.py"

spec = importlib.util.spec_from_file_location("album_script", SCRIPT)
assert spec is not None
assert spec.loader is not None
album = importlib.util.module_from_spec(spec)
sys.modules["album_script"] = album
spec.loader.exec_module(album)

HOLDER = (
    "import socket, sys, time; s = socket.socket(); s.bind(('127.0.0.1', 0)); s.listen(); "
    "open(sys.argv[1], 'w').write(str(s.getsockname()[1])); time.sleep(60)"
)
WRAPPER = "import subprocess, sys; subprocess.run([sys.executable, '-c', sys.argv[1], sys.argv[2]])"
SLEEPS = [sys.executable, "-c", "import time; time.sleep(60)"]


def test_it_imports_only_the_standard_library():
    # devkit's dispatcher starts it under the machine's python, not the project's.
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported = {
        name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for name in (
            [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else [node.module or ""]
        )
    }

    assert imported <= set(sys.stdlib_module_names)


def test_read_dotenv_keeps_values_and_skips_the_rest():
    text = '# a comment\n\nA=1\nB = two\nC="three"\nURL=postgres://x:y@h/db?a=b\nnot a line\n'

    assert album.read_dotenv(text) == {
        "A": "1",
        "B": "two",
        "C": "three",
        "URL": "postgres://x:y@h/db?a=b",
    }


def test_environment_lets_the_real_environment_win_over_dotenv():
    merged = album.build_environment({"A": "dotenv", "B": "dotenv"}, {"A": "real"})

    assert (merged["A"], merged["B"]) == ("real", "dotenv")


def test_environment_points_the_frontend_at_this_checkouts_ports():
    merged = album.build_environment({"APP_HOST_PORT": "8107", "FRONTEND_HOST_PORT": "5280"}, {})

    assert merged["API_PROXY_TARGET"] == "http://127.0.0.1:8107"
    assert merged["VITE_PORT"] == "5280"


def test_environment_has_defaults_for_a_bare_clone():
    merged = album.build_environment({}, {})

    assert merged["API_PROXY_TARGET"] == "http://127.0.0.1:8007"
    assert merged["VITE_PORT"] == "5180"


def test_python_prefix_prefers_uv_which_also_provisions():
    assert album.python_prefix("uv") == ["uv", "run", "python"]


def test_python_prefix_finds_the_venv_without_uv(tmp_path):
    folder = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    interpreter = tmp_path / ".venv" / folder
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()

    assert album.python_prefix(None, tmp_path) == [str(interpreter)]


def test_python_prefix_falls_back_to_what_is_running(tmp_path):
    assert album.python_prefix(None, tmp_path) == [sys.executable]


def test_setup_starts_only_the_database_in_docker():
    commands = album.setup_commands(["uv", "run", "python"], "npm", has_node_modules=True)

    assert commands[0] == ["docker", "compose", "up", "--detach", "--wait", "db"]
    assert commands[1] == ["uv", "run", "python", "-m", "alembic", "upgrade", "head"]
    assert len(commands) == 2


def test_setup_installs_the_frontend_only_when_it_is_missing():
    commands = album.setup_commands(["python"], "npm", has_node_modules=False)

    assert commands[-1][:4] == ["npm", "--prefix", "frontend", "install"]


def test_servers_never_widen_past_loopback():
    (api, _), _ = album.server_commands(["python"], "node", {"APP_HOST_PORT": "8107"})

    assert api == ["python", "-m", "photolab", "serve", "--port", "8107"]
    assert "--host" not in api


def test_vite_runs_directly_so_that_stopping_it_really_stops_it():
    _, (frontend, cwd) = album.server_commands(["python"], "node", {})

    assert frontend[0] == "node"
    assert cwd.name == "frontend"


def test_run_servers_stops_the_other_when_one_exits(tmp_path):
    quick = [sys.executable, "-c", "raise SystemExit(4)"]

    code = album.run_servers(
        [(SLEEPS, tmp_path), (quick, tmp_path)], dict(album.os.environ), threading.Event()
    )

    assert code == 4


def test_stopping_a_server_stops_what_it_started(tmp_path):
    # On Windows `node` can be nvm's shim and a venv's python is a redirector, so what Popen
    # holds is a parent, and ending only that leaves the real server holding its port.
    port_file = tmp_path / "port"
    wrapper = [sys.executable, "-c", WRAPPER, HOLDER, str(port_file)]
    quick = [
        sys.executable,
        "-c",
        "import pathlib, sys, time\n"
        "path = pathlib.Path(sys.argv[1])\n"
        "while not path.exists() or not path.read_text(): time.sleep(0.05)",
        str(port_file),
    ]

    album.run_servers(
        [(wrapper, tmp_path), (quick, tmp_path)], dict(album.os.environ), threading.Event()
    )

    try:
        socket.create_connection(("127.0.0.1", int(port_file.read_text())), timeout=1).close()
    except OSError:
        return
    raise AssertionError("the real server outlived the stop")


def test_stopping_a_server_kills_what_will_not_stop(tmp_path):
    port_file = tmp_path / "port"
    stubborn = "import signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); " + HOLDER
    server = subprocess.Popen(  # noqa: S603 - this test's own constants, not input
        [sys.executable, "-c", WRAPPER, stubborn, str(port_file)], start_new_session=True
    )
    deadline = time.monotonic() + 10
    while not (port_file.exists() and port_file.read_text()) and time.monotonic() < deadline:
        time.sleep(0.02)

    album.stop_tree(server, grace=0.5)

    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", int(port_file.read_text())), timeout=1).close()


def test_stop_tree_is_harmless_on_a_server_that_already_ended():
    server = subprocess.Popen([sys.executable, "-c", "raise SystemExit(5)"])
    server.wait()

    album.stop_tree(server)

    assert server.returncode == 5


def test_wait_for_exit_returns_when_the_process_ends():
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.3)"])
    waiter = threading.Thread(target=album.wait_for_exit, args=(process.pid,), daemon=True)
    if sys.platform != "win32":
        # A child nobody has reaped still has a PID there; reap it, as a real launcher's
        # parent would.
        threading.Thread(target=process.wait, daemon=True).start()

    waiter.start()
    waiter.join(timeout=10)

    assert not waiter.is_alive()
    process.wait()


def test_wait_for_exit_leaves_a_live_process_alone():
    # The trap this guards against: `os.kill(pid, 0)` on Windows terminates the process.
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    waiter = threading.Thread(target=album.wait_for_exit, args=(process.pid,), daemon=True)

    waiter.start()
    waiter.join(timeout=0.5)

    assert waiter.is_alive()
    assert process.poll() is None
    process.kill()
    process.wait()


def test_closing_the_terminal_stops_the_servers(tmp_path):
    # From a task, the launcher ending is the only signal there is: no Ctrl+C ever arrives.
    launcher = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.5)"])
    threading.Thread(target=launcher.wait, daemon=True).start()
    stop = threading.Event()

    album.watch_launcher(launcher.pid, stop)
    code = album.run_servers([(SLEEPS, tmp_path)], dict(album.os.environ), stop)

    assert stop.is_set()
    assert code == 0


class _Answers(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200 if self.path == "/api/stats" else 503)
        self.end_headers()

    def log_message(self, *_) -> None:
        pass


def test_is_up_asks_the_real_thing():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Answers)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"

    try:
        assert album.is_up(f"{base}/api/stats")
        assert not album.is_up(f"{base}/other")
    finally:
        server.shutdown()
        server.server_close()


def test_is_up_is_false_when_nothing_listens():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    assert not album.is_up(f"http://127.0.0.1:{port}/api/stats")


def test_open_when_ready_waits_for_both_servers():
    answers = iter([False, True])
    opened: list[str] = []

    done = album.open_when_ready("http://album", lambda _: next(answers), opened.append)

    assert done
    assert opened == ["http://album"]


def test_open_when_ready_gives_up_quietly():
    opened: list[str] = []

    assert not album.open_when_ready("http://a", lambda _: False, opened.append, timeout=0)
    assert opened == []


def test_parse_args_can_skip_the_browser():
    assert album.parse_args(["--no-open"]).no_open
    assert not album.parse_args([]).no_open


def test_main_says_what_to_do_without_a_dotenv(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(album, "ROOT", tmp_path)

    assert album.main([]) == 1
    assert ".env.example" in capsys.readouterr().out


def test_main_stops_at_a_setup_step_that_fails(tmp_path, monkeypatch, capsys):
    (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")
    monkeypatch.setattr(album, "ROOT", tmp_path)
    monkeypatch.setattr(
        album.subprocess, "run", lambda *_, **__: subprocess.CompletedProcess([], 7)
    )

    assert album.main(["--no-open"]) == 7
    assert "docker compose up" in capsys.readouterr().out
