"""Calling the Codex and Claude command lines, which run on the owner's subscriptions.

These are agents, not plain completions, and a photo can carry text that reads like an
instruction. So every call here is locked down rather than inheriting the owner's setup,
which may well allow anything: no user configuration, no shell, no plugins or MCP servers,
a read-only sandbox, a throwaway working directory, and nothing kept afterwards.

The calls block, and `subprocess` rather than `asyncio.create_subprocess_exec` is the only
option: psycopg needs the selector loop on Windows, which cannot run subprocesses.
Callers wrap them in `asyncio.to_thread`.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from photolab.config import Settings

Backend = Literal["codex", "claude", "api"]

CLI_TIMEOUT_SECONDS = 300

# Cheap models on purpose. Annotating is transcription, and for an image this model only
# hands the prompt to the image tool, so a stronger one adds cost and no quality.
DEFAULT_MODELS: dict[str, str] = {
    "codex": "gpt-6-luna",
    "claude": "haiku",
    "api": "claude-haiku-5-5",
}

# A key in the environment would quietly bill an API account instead of the subscription.
_BILLED_KEYS = frozenset({"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY"})

_CODEX_DISABLED = (
    "apps",
    "browser_use",
    "computer_use",
    "goals",
    "multi_agent",
    "plugins",
    "shell_tool",
    "skill_mcp_dependency_install",
    "tool_suggest",
)

# What says every later call would fail too, as opposed to this one.
_RUN_WIDE_MARKERS = (
    "usage limit",
    "rate limit",
    "too many requests",
    "quota",
    "credit balance",
    "not logged in",
    "please log in",
    "unauthorized",
    "not supported",
)

_THREAD_ID = re.compile(r"[0-9a-zA-Z-]{8,64}")


class AgentFailed(ValueError):
    """This one request failed. The next may well not."""


class AgentUnavailable(Exception):
    """Every request would fail the same way: a limit, a login, a wrong model name."""


@dataclass
class CliResult:
    returncode: int
    stdout: str
    stderr: str = ""


def run_cli(command: Sequence[str], stdin: str, cwd: Path) -> CliResult:
    """Run one command, with the prompt on stdin so that it is never parsed as arguments."""
    environment = {key: value for key, value in os.environ.items() if key not in _BILLED_KEYS}
    executable = shutil.which(command[0]) or command[0]
    try:
        done = subprocess.run(  # noqa: S603 - a fixed argv, with the prompt on stdin
            [executable, *command[1:]],
            input=stdin,
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CLI_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AgentFailed(f"{command[0]} took over {CLI_TIMEOUT_SECONDS}s") from exc
    except OSError as exc:
        raise AgentUnavailable(f"could not start {command[0]}: {exc}") from exc
    return CliResult(done.returncode, done.stdout, done.stderr)


def default_codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


@dataclass(frozen=True)
class Clis:
    """How the command lines are reached; a test builds one with fakes in it."""

    run: Callable[[Sequence[str], str, Path], CliResult] = run_cli
    which: Callable[[str], str | None] = shutil.which
    codex_home: Path = field(default_factory=default_codex_home)

    @property
    def can_make_images(self) -> bool:
        return self.which("codex") is not None


def available_backends(settings: Settings, clis: Clis) -> list[Backend]:
    """What can annotate here. A container has neither CLI, or either login."""
    found: list[Backend] = []
    if clis.which("codex"):
        found.append("codex")
    if clis.which("claude"):
        found.append("claude")
    if settings.anthropic_api_key:
        found.append("api")
    return found


def default_backend(settings: Settings, clis: Clis) -> Backend:
    """The configured backend, or else the first one that is actually here."""
    found = available_backends(settings, clis)
    if settings.annotation_backend in found or not found:
        return settings.annotation_backend
    return found[0]


def resolve_model(settings: Settings, backend: Backend, model: str | None = None) -> str:
    """`ANNOTATION_MODEL` belongs to `ANNOTATION_BACKEND`, not to whichever is asked for."""
    configured = settings.annotation_model if backend == settings.annotation_backend else ""
    return model or configured or DEFAULT_MODELS[backend]


def classify_failure(message: str) -> Exception:
    """The error for what a CLI reported, telling a stopped run from one bad request."""
    lowered = message.lower()
    if any(marker in lowered for marker in _RUN_WIDE_MARKERS):
        return AgentUnavailable(message)
    return AgentFailed(message)


def codex_command(model: str, working_dir: Path, extra: Sequence[str] = ()) -> list[str]:
    """`codex exec` with nothing of the owner's setup, reading its prompt from stdin."""
    disabled = [flag for name in _CODEX_DISABLED for flag in ("--disable", name)]
    return [
        "codex",
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        *disabled,
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--skip-git-repo-check",
        "--cd",
        str(working_dir),
        "--model",
        model,
        "--json",
        "--config",
        'model_reasoning_effort="low"',
        *extra,
        "-",
    ]


@dataclass
class CodexRun:
    thread_id: str = ""
    message: str = ""
    error: str = ""


def _read_event(line: str) -> dict[str, Any]:
    """One event. A line that is not JSON is noise, not a failure."""
    try:
        return json.loads(line)
    except ValueError:
        return {}


def _apply_event(run: CodexRun, event: dict[str, Any]) -> None:
    kind = event.get("type")
    if kind == "thread.started":
        run.thread_id = event.get("thread_id", "")
    elif kind == "turn.failed":
        run.error = event.get("error", {}).get("message", "codex failed")
    elif kind == "error":
        run.error = run.error or event.get("message", "codex failed")
    elif kind == "item.completed" and event["item"].get("type") == "agent_message":
        run.message = event["item"].get("text", "")


def parse_codex_events(stdout: str) -> CodexRun:
    """What happened, from `--json`'s event stream."""
    run = CodexRun()
    for line in stdout.splitlines():
        _apply_event(run, _read_event(line))
    return run


def _run_codex(
    prompt: str, model: str, working: Path, extra: Sequence[str], clis: Clis
) -> CodexRun:
    result = clis.run(codex_command(model, working, extra), prompt, working)
    run = parse_codex_events(result.stdout)
    if run.error or result.returncode != 0:
        detail = run.error or result.stderr.strip()[-500:]
        raise classify_failure(detail or f"codex exited with {result.returncode}")
    return run


def codex_json(
    system: str, prompt: str, schema: dict[str, Any], model: str, jpeg: bytes | None, clis: Clis
) -> str:
    """A schema-shaped answer from Codex, as text, about `jpeg` if there is one."""
    with tempfile.TemporaryDirectory() as folder:
        working = Path(folder)
        schema_file = working / "schema.json"
        schema_file.write_text(json.dumps(schema), encoding="utf-8")
        extra = ["--disable", "image_generation", "--output-schema", str(schema_file)]
        if jpeg is not None:
            photo = working / "photo.jpg"
            photo.write_bytes(jpeg)
            extra += ["--image", str(photo)]
        run = _run_codex(f"{system}\n\n{prompt}", model, working, extra, clis)
    if not run.message:
        raise AgentFailed("codex gave no answer")
    return run.message


def take_generated_image(run: CodexRun, codex_home: Path) -> bytes:
    """The image a run made, moved out of Codex's folder: the album keeps the only copy."""
    # It names a folder to delete, so it is checked rather than trusted.
    if not _THREAD_ID.fullmatch(run.thread_id):
        raise AgentFailed("codex did not report which session it ran")
    folder = codex_home / "generated_images" / run.thread_id
    images = sorted(folder.glob("*.png"), key=lambda path: path.stat().st_mtime)
    if not images:
        raise AgentFailed(f"codex made no image: {run.message or 'it gave no reason'}")
    content = images[-1].read_bytes()
    shutil.rmtree(folder)
    return content


def codex_image(prompt: str, references: Sequence[bytes], model: str, clis: Clis) -> bytes:
    """PNG bytes from Codex's `image_gen` tool, which needs a ChatGPT login and no API key."""
    with tempfile.TemporaryDirectory() as folder:
        working = Path(folder)
        extra: list[str] = []
        for index, jpeg in enumerate(references, start=1):
            reference = working / f"image-{index}.jpg"
            reference.write_bytes(jpeg)
            extra += ["--image", str(reference)]
        run = _run_codex(prompt, model, working, extra, clis)
    return take_generated_image(run, clis.codex_home)


def claude_command(system: str, schema: dict[str, Any], model: str, read: bool) -> list[str]:
    """`claude -p` with one read-only tool at most, and no MCP servers, skills or session."""
    tools = ["--tools", "Read", "--allowedTools", "Read"] if read else ["--tools", ""]
    return [
        "claude",
        "--print",
        "--model",
        model,
        "--no-session-persistence",
        *tools,
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--system-prompt",
        system,
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(schema),
    ]


def claude_json(
    system: str, prompt: str, schema: dict[str, Any], model: str, jpeg: bytes | None, clis: Clis
) -> str:
    """A schema-shaped answer from Claude, as text, about `jpeg` if there is one."""
    with tempfile.TemporaryDirectory() as folder:
        working = Path(folder)
        if jpeg is not None:
            (working / "photo.jpg").write_bytes(jpeg)
            prompt = f"The photo is the file ./photo.jpg. Read it first.\n\n{prompt}"
        result = clis.run(claude_command(system, schema, model, jpeg is not None), prompt, working)
    try:
        answer = json.loads(result.stdout)
    except ValueError as exc:
        detail = result.stderr.strip() or result.stdout.strip()
        raise classify_failure(detail or f"claude exited with {result.returncode}") from exc
    if answer.get("is_error") or result.returncode != 0:
        raise classify_failure(
            str(answer.get("result") or answer.get("subtype") or "claude failed")
        )
    if answer.get("structured_output") is None:
        raise AgentFailed("claude gave no structured answer")
    return json.dumps(answer["structured_output"])
