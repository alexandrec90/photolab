"""`.github/dependabot.yml` names only what this repository actually has, and all of it.

An entry whose directory holds no manifest does not fail in CI: Dependabot's scheduled
job fails on its own, every run, with `Error during file fetching; aborting:
/frontend/package.json not found`, and nothing in a PR says so. The other direction is
just as quiet: scaffold `frontend/` without the entry and its dependencies simply never
get an update. This test is the one place either shows up before a scheduled run does.

The file is read textually, as `scripts/project_dependabot.py` reads it: the standard
library has no YAML parser, and PyYAML reaches this project only through pre-commit.
"""

import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPENDABOT = ".github/dependabot.yml"

# What Dependabot fetches for each ecosystem this project uses, relative to the entry's
# directory. An ecosystem missing here fails the test rather than passing unchecked.
MANIFESTS = {
    "uv": "pyproject.toml",
    "npm": "package.json",
    "github-actions": ".github/workflows",
    "docker": "Dockerfile",
}

_HEAD = re.compile(r"\s*-\s*package-ecosystem:\s*['\"]?([\w-]+)")
_DIRECTORY = re.compile(r"\s*directory:\s*['\"]?([^'\"#\s]+)")


def dependabot_entries(text: str) -> list[tuple[str, str | None]]:
    """`(ecosystem, directory)` per `updates:` item; `None` for one naming no `directory:`.

    A `directory:` counts only at the entry's own key column, so a same-named key nested
    deeper is not taken for it. Comments are not code, so a commented-out entry is no entry.
    """
    entries: list[tuple[str, str | None]] = []
    column = 0
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if head := _HEAD.match(line):
            entries.append((head.group(1), None))
            column = line.index("package-ecosystem:")
        elif entries and (found := _DIRECTORY.match(line)):
            if len(line) - len(line.lstrip()) == column:
                entries[-1] = (entries[-1][0], found.group(1))
    return entries


def missing_manifests(root: Path, entries: list[tuple[str, str | None]]) -> list[str]:
    """One line per entry Dependabot could not fetch a manifest for under `root`."""
    problems = []
    for ecosystem, directory in entries:
        if directory is None:
            problems.append(f"{ecosystem}: names no `directory:`")
        elif ecosystem not in MANIFESTS:
            problems.append(f"{ecosystem}: no manifest known; add it to MANIFESTS")
        elif not (root / directory.strip("/") / MANIFESTS[ecosystem]).exists():
            problems.append(f"{ecosystem}: {directory} has no {MANIFESTS[ecosystem]}")
    return problems


def uncovered_frontend(
    root: Path, frontend_dir: str, entries: list[tuple[str, str | None]]
) -> bool:
    """Whether the frontend has a `package.json` that no `npm` entry updates."""
    if not (root / frontend_dir / "package.json").is_file():
        return False
    return ("npm", f"/{frontend_dir}") not in entries


def _frontend_dir() -> str:
    config = tomllib.loads((REPO_ROOT / ".devkit.toml").read_text(encoding="utf-8"))
    return config["frontend"]["dir"]


def _entries() -> list[tuple[str, str | None]]:
    return dependabot_entries((REPO_ROOT / DEPENDABOT).read_text(encoding="utf-8"))


def test_every_entry_points_at_a_manifest_that_exists():
    assert missing_manifests(REPO_ROOT, _entries()) == [], (
        f"{DEPENDABOT} has an entry Dependabot cannot fetch, so its scheduled job fails "
        "every run. Create the manifest or delete the entry."
    )


def test_a_scaffolded_frontend_has_an_update_entry():
    frontend = _frontend_dir()
    assert not uncovered_frontend(REPO_ROOT, frontend, _entries()), (
        f"{frontend}/package.json exists but {DEPENDABOT} has no npm entry for it, so its "
        "dependencies never get an update. Restore the entry from devkit's "
        "templates/core/dot-github/dependabot.yml.tmpl, groups included."
    )


def test_entries_are_read_from_the_entry_own_key_column():
    text = (
        "version: 2\n"
        "updates:\n"
        "  - package-ecosystem: 'npm'\n"
        "    directory: /web  # the app\n"
        "    groups:\n"
        "      g:\n"
        "        directory: /nested\n"
        "  # - package-ecosystem: pip\n"
        "  #   directory: /commented\n"
        "  - package-ecosystem: docker\n"
        "    schedule:\n"
        "      interval: weekly\n"
    )
    assert dependabot_entries(text) == [("npm", "/web"), ("docker", None)]


def test_no_entries_in_a_file_declaring_none():
    assert dependabot_entries("version: 2\nupdates: []\n") == []


def test_the_failing_shape_is_reported(tmp_path):
    """The run that sent this test here: an npm entry with no `frontend/package.json`."""
    assert missing_manifests(tmp_path, [("npm", "/frontend")]) == [
        "npm: /frontend has no package.json"
    ]


def test_a_manifest_that_exists_is_not_reported(tmp_path):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    assert missing_manifests(tmp_path, [("npm", "/frontend"), ("docker", "/")]) == []


@pytest.mark.parametrize(
    ("entry", "problem"),
    [
        (("npm", None), "npm: names no `directory:`"),
        (("cargo", "/"), "cargo: no manifest known; add it to MANIFESTS"),
    ],
)
def test_an_entry_that_cannot_be_checked_is_reported(tmp_path, entry, problem):
    assert missing_manifests(tmp_path, [entry]) == [problem]


def test_a_frontend_with_no_entry_is_reported(tmp_path):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text("{}", encoding="utf-8")
    assert uncovered_frontend(tmp_path, "frontend", [("uv", "/")])
    assert not uncovered_frontend(tmp_path, "frontend", [("npm", "/frontend")])


def test_no_frontend_needs_no_entry(tmp_path):
    assert not uncovered_frontend(tmp_path, "frontend", [("uv", "/")])
