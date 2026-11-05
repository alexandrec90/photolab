"""Migrations stay committable: a revision id is hex, which detect-secrets reads as a key.

The pragma has to come from `script.py.mako`, or every new revision is refused at commit,
and it has to survive the `ruff format` that alembic's post-write hook runs on the file.
The ids here are not hex, so that this file does not need the same pragma.
"""

import subprocess
import sys
from pathlib import Path

import pytest
from alembic import util

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
PRAGMA = "# pragma: allowlist secret"


def id_lines(source: str) -> list[str]:
    return [
        line for line in source.splitlines() if line.startswith(("revision:", "down_revision:"))
    ]


def new_revision(down_revision: str | tuple[str, ...] | None, folder: Path) -> str:
    """The template as alembic renders it, then the formatter that decides its line breaks."""
    revision = folder / "revision.py"
    util.template_to_file(
        MIGRATIONS / "script.py.mako",
        revision,
        "utf-8",
        message="a change",
        up_revision="this_change",
        down_revision=down_revision,
        create_date="2026-11-04 22:00:00",
        imports="",
        branch_labels=None,
        depends_on=None,
        upgrades="pass",
        downgrades="pass",
        comma=util.format_as_comma,
    )
    command = [sys.executable, "-m", "ruff", "format", "--config", str(ROOT / "ruff.toml")]
    subprocess.run(  # noqa: S603 - this venv's ruff on a file this test wrote
        [*command, str(revision)], capture_output=True, check=True
    )
    return revision.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "down_revision",
    [None, "first_parent", ("first_parent", "other_parent")],
    ids=["first", "next", "merge"],
)
def test_a_new_revision_is_allowlisted_on_both_id_lines(down_revision, tmp_path):
    lines = id_lines(new_revision(down_revision, tmp_path))

    assert len(lines) == 2
    assert all(PRAGMA in line for line in lines)


def test_a_merge_keeps_every_parent_on_the_allowlisted_line(tmp_path):
    down_revision = id_lines(new_revision(("first_parent", "other_parent"), tmp_path))[1]

    assert "first_parent" in down_revision
    assert "other_parent" in down_revision


def test_a_new_revision_is_a_module_that_compiles(tmp_path):
    compile(new_revision("first_parent", tmp_path), "revision.py", "exec")


@pytest.mark.parametrize(
    "revision",
    sorted((MIGRATIONS / "versions").glob("*.py")),
    ids=lambda path: path.name,
)
def test_every_committed_revision_is_allowlisted(revision):
    lines = id_lines(revision.read_text(encoding="utf-8"))

    assert len(lines) == 2
    assert all(PRAGMA in line for line in lines)
