"""`scripts/e2e.py`, the local tier's runner, and the deselection that keeps the tier local."""

import ast
import importlib.util
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "e2e.py"

spec = importlib.util.spec_from_file_location("e2e_script", SCRIPT)
assert spec is not None
assert spec.loader is not None
e2e = importlib.util.module_from_spec(spec)
sys.modules["e2e_script"] = e2e
spec.loader.exec_module(e2e)

REPORT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest">
  <testcase classname="tests.test_album_e2e" name="test_opens" time="1.0"/>
  <testcase classname="tests.test_album_e2e" name="test_scans" time="1.0">
    <failure message="AssertionError: no photo&#10;more detail">trace</failure>
  </testcase>
  <testcase classname="tests.test_album_e2e" name="test_db" time="0.1">
    <error message="failed on setup with Docker down">trace</error>
  </testcase>
  <testcase classname="tests.test_album_e2e.TestStop" name="test_ports" time="0.0">
    <skipped message="no node" type="pytest.skip">skip</skipped>
  </testcase>
</testsuite></testsuites>
"""


def write_report(path: Path, body: str = REPORT) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_it_imports_only_the_standard_library():
    # The scheduler may start it under the machine's python, not the project's.
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

    assert imported <= set(sys.stdlib_module_names) | {"__future__"}


@pytest.mark.parametrize(
    ("windows", "relative"), [(True, "Scripts/python.exe"), (False, "bin/python")]
)
def test_venv_python_prefers_the_checkouts_own_interpreter(
    tmp_path: Path, windows: bool, relative: str
):
    interpreter = tmp_path / ".venv" / relative
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("", encoding="utf-8")

    assert e2e.venv_python(tmp_path, "fallback", windows=windows) == str(interpreter)


def test_venv_python_falls_back_without_a_venv(tmp_path: Path):
    assert e2e.venv_python(tmp_path, "fallback", windows=True) == "fallback"


@pytest.mark.parametrize(
    ("classname", "name", "expected"),
    [
        ("tests.test_x", "test_y", "tests/test_x.py::test_y"),
        ("tests.test_x.TestY", "test_z", "tests/test_x.py::TestY::test_z"),
        ("", "tests.test_broken", "tests.test_broken"),
        ("tests.test_x", "test_y[a b]", "tests/test_x.py::test_y[a_b]"),
    ],
)
def test_pytest_id_names_a_case_as_pytests_summary_does(classname, name, expected):
    assert e2e.pytest_id(classname, name) == expected


def test_cases_reads_every_outcome(tmp_path: Path):
    found = e2e.cases(write_report(tmp_path / "junit.xml"))

    assert found == [
        e2e.Case("tests/test_album_e2e.py::test_opens", e2e.PASSED),
        e2e.Case(
            "tests/test_album_e2e.py::test_scans",
            e2e.FAILED,
            "AssertionError: no photo\nmore detail",
        ),
        e2e.Case(
            "tests/test_album_e2e.py::test_db", e2e.FAILED, "failed on setup with Docker down"
        ),
        e2e.Case("tests/test_album_e2e.py::TestStop::test_ports", e2e.SKIPPED, "no node"),
    ]


@pytest.mark.parametrize("body", [None, "<testsuites><unclosed"])
def test_cases_of_a_missing_or_broken_report_is_none(tmp_path: Path, body: str | None):
    report = tmp_path / "junit.xml"
    if body is not None:
        write_report(report, body)

    assert e2e.cases(report) == []


def test_verdict_keeps_a_green_run_green():
    code, lines = e2e.verdict(0, [e2e.Case("t.py::a", e2e.PASSED)], strict=True)

    assert (code, lines) == (0, [])


def test_verdict_prints_each_failure_and_error_as_a_failed_line():
    found = [
        e2e.Case("t.py::a", e2e.PASSED),
        e2e.Case("t.py::b", e2e.FAILED, "boom\nsecond line"),
        e2e.Case("t.py::c", e2e.FAILED, ""),
    ]

    code, lines = e2e.verdict(1, found, strict=False)

    assert code == 1
    assert lines == ["FAILED t.py::b - boom", "FAILED t.py::c - no reason given"]


@pytest.mark.parametrize("found", [[], [e2e.Case("t.py::a", e2e.SKIPPED, "no docker")]])
def test_verdict_makes_a_run_that_executed_nothing_red(found):
    code, lines = e2e.verdict(0, found, strict=False)

    assert code != 0
    assert lines[-1] == "e2e: no local test executed, which is not a pass"


def test_verdict_lists_a_skip_by_hand_without_failing():
    found = [e2e.Case("t.py::a", e2e.PASSED), e2e.Case("t.py::b", e2e.SKIPPED, "no node")]

    assert e2e.verdict(0, found, strict=False) == (0, ["SKIPPED t.py::b - skipped: no node"])


def test_verdict_fails_a_skip_under_the_scheduler():
    found = [e2e.Case("t.py::a", e2e.PASSED), e2e.Case("t.py::b", e2e.SKIPPED, "no node")]

    code, lines = e2e.verdict(0, found, strict=True)

    assert code != 0
    assert lines[0] == "FAILED t.py::b - skipped: no node"


def test_pytest_argv_selects_the_tier_after_addopts(tmp_path: Path):
    argv = e2e.pytest_argv("py", tmp_path / "r.xml")

    assert argv[:4] == ["py", "-m", "pytest", "tests"]
    assert argv[argv.index("tests") + 1 : argv.index("tests") + 3] == ["-m", "local"]
    assert f"--junitxml={tmp_path / 'r.xml'}" in argv


def test_pytest_argv_runs_named_ids_in_place_of_the_whole_tier(tmp_path: Path):
    argv = e2e.pytest_argv("py", tmp_path / "r.xml", ["tests/test_album_e2e.py::test_x"])

    assert "tests" not in argv
    assert argv[3:6] == ["tests/test_album_e2e.py::test_x", "-m", "local"]


def fake_runner(report: Path, code: int, body: str | None, calls: list[dict[str, Any]]):
    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"argv": argv, **kwargs})
        if body is not None:
            write_report(report, body)
        return subprocess.CompletedProcess(argv, code, stdout="pytest said\n", stderr="")

    return run


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    report = tmp_path / "logs" / "e2e-junit.xml"
    monkeypatch.setattr(e2e, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(e2e, "REPORT", report)
    return report


def test_main_prints_the_failures_and_exits_red(isolated: Path, capsys):
    calls: list[dict[str, Any]] = []

    code = e2e.main([], runner=fake_runner(isolated, 1, REPORT, calls), env={})

    out = capsys.readouterr().out
    assert code == 1
    assert "pytest said" in out
    assert "FAILED tests/test_album_e2e.py::test_scans - AssertionError: no photo" in out
    assert "SKIPPED tests/test_album_e2e.py::TestStop::test_ports - skipped: no node" in out
    assert "e2e: report in logs/e2e-junit.xml" in out
    assert calls[0]["env"]["PYTHONUTF8"] == "1"
    assert calls[0]["creationflags"] == e2e.NO_WINDOW
    assert calls[0]["cwd"] == isolated.parent.parent


def test_main_is_red_under_the_scheduler_for_a_skip(isolated: Path):
    body = REPORT.replace("<failure", "<!--").replace("</failure>", "-->")
    body = body.replace("<error", "<!--").replace("</error>", "-->")

    assert e2e.main([], runner=fake_runner(isolated, 0, body, []), env={}) == 0
    assert e2e.main([], runner=fake_runner(isolated, 0, body, []), env={"DEVKIT_E2E": "1"}) != 0


def test_main_is_red_when_pytest_wrote_no_report_even_with_a_stale_one(isolated: Path, capsys):
    isolated.parent.mkdir()
    write_report(isolated, REPORT.replace("<failure", "<!--").replace("</failure>", "-->"))

    code = e2e.main([], runner=fake_runner(isolated, 0, None, []), env={})

    assert code != 0
    assert "no local test executed" in capsys.readouterr().out


def test_the_default_run_deselects_the_tier_and_registers_its_marker():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    options = config["tool"]["pytest"]["ini_options"]
    addopts = shlex.split(options["addopts"])

    assert addopts[addopts.index("-m") + 1] == "not local"
    assert any(marker.startswith(f"{e2e.MARKER}:") for marker in options["markers"])


def test_the_album_e2e_test_is_in_the_tier():
    tree = ast.parse((ROOT / "tests" / "test_album_e2e.py").read_text(encoding="utf-8"))
    marks = [
        ast.unparse(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets)
    ]

    assert marks == ["pytest.mark.local"]
