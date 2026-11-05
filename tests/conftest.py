"""Shared fixtures.

The suite runs against real Postgres, in a schema of its own that is created for the run
and dropped after it. `DATABASE_URL` points at the same database as the album, and an
ordinary `TRUNCATE` between tests would destroy annotations that cost money to produce.
"""

import asyncio
import io
import json
import os
import sys
import uuid
from collections.abc import AsyncIterator, Iterator, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import numpy as np
import pytest
from dotenv import dotenv_values
from PIL import Image
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine

from photolab.agents import CliResult, Clis
from photolab.api.app import build_app
from photolab.api.deps import AppContext
from photolab.config import Settings
from photolab.db import SessionFactory, make_engine, make_session_factory
from photolab.jobs import JobBoard
from photolab.models import Base

if sys.platform == "win32":
    # psycopg's async mode cannot run on the proactor loop.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def database_url() -> str:
    url = os.environ.get("DATABASE_URL") or dotenv_values(".env").get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set, and the suite needs a real Postgres")
    return url


@pytest.fixture(scope="session")
def schema() -> Iterator[str]:
    name = f"test_{uuid.uuid4().hex[:12]}"
    engine = create_engine(database_url(), connect_args={"options": f"-csearch_path={name}"})
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{name}"'))
        Base.metadata.create_all(connection)
    yield name
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{name}" CASCADE'))
    engine.dispose()


@pytest.fixture
async def engine(schema: str) -> AsyncIterator[AsyncEngine]:
    built = make_engine(database_url(), connect_args={"options": f"-csearch_path={schema}"})
    yield built
    async with built.begin() as connection:
        tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
        await connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    await built.dispose()


@pytest.fixture
def sessions(engine: AsyncEngine) -> SessionFactory:
    return make_session_factory(engine)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    library = tmp_path / "library"
    library.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    return Settings(
        database_url=database_url(),
        PHOTO_LIBRARY_DIR=library,
        PHOTOLAB_DATA_DIR=data,
        annotation_backend="api",
        annotation_model="test-model",
        anthropic_api_key="test-key",  # pragma: allowlist secret
        pexels_api_key="",
        _env_file=None,  # type: ignore[call-arg] # pydantic-settings' init-only override
    )


def jpeg_bytes(
    color: tuple[int, int, int] = (120, 90, 60), size: tuple[int, int] = (64, 48)
) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def write_photo(
    settings: Settings,
    name: str = "one.jpg",
    color: tuple[int, int, int] = (120, 90, 60),
    size: tuple[int, int] = (64, 48),
) -> Path:
    path = settings.library_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(jpeg_bytes(color, size))
    return path


ANNOTATION: dict[str, Any] = {
    "caption": "A person walks along a beach at sunset.",
    "tags": ["beach", "sunset"],
    "scene": "beach",
    "setting": "outdoor",
    "time_of_day": "golden_hour",
    "person_count": 1,
    "people_descriptions": ["person in a red jacket"],
    "shot_type": "full_body",
    "camera_height": "eye_level",
    "light_direction": "left",
    "light_quality": "hard",
    "color_temperature": "warm",
    "quality_issues": [],
    "usable_as_subject": True,
    "usable_as_background": False,
}


def fake_response(payload: str, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=payload)]
    )


class FakeAnthropic:
    """Stands in for `AsyncAnthropic`, recording requests and replaying answers."""

    def __init__(self, answer: Any = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.answer = answer
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **request: Any) -> Any:
        self.calls.append(request)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer or fake_response(_annotation_json())


def _annotation_json() -> str:
    return json.dumps(ANNOTATION)


THREAD = "01a1a922-0d06-7ef0-b324-98aa899c4bd7"


def codex_events(message: str = "done", thread: str = THREAD) -> str:
    """What `codex exec --json` prints for a run that went well, as observed for real."""
    events: list[dict[str, Any]] = [
        {"type": "thread.started", "thread_id": thread},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Working on it."}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": message}},
        {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}},
    ]
    return "\n".join(json.dumps(event) for event in events)


def codex_failure(message: str) -> str:
    """The same for a failed run, which still exits with a status and these two events."""
    return "\n".join(
        json.dumps(event)
        for event in [
            {"type": "thread.started", "thread_id": THREAD},
            {"type": "error", "message": message},
            {"type": "turn.failed", "error": {"message": message}},
        ]
    )


class FakeCli:
    """Stands in for the codex and claude commands, recording how they were called."""

    def __init__(self, home: Path, installed: Sequence[str] = ("codex", "claude")) -> None:
        self.home = home
        self.installed = installed
        self.result = CliResult(0, codex_events())
        self.make_image = False
        self.calls: list[list[str]] = []
        self.stdin: list[str] = []
        self.files: list[list[str]] = []

    def run(self, command: Sequence[str], stdin: str, cwd: Path) -> CliResult:
        self.calls.append(list(command))
        self.stdin.append(stdin)
        self.files.append(sorted(path.name for path in cwd.iterdir()))
        if self.make_image:
            folder = self.home / "generated_images" / THREAD
            folder.mkdir(parents=True, exist_ok=True)
            buffer = io.BytesIO()
            Image.new("RGB", (32, 24), (10, 120, 200)).save(buffer, format="PNG")
            (folder / "exec-1.png").write_bytes(buffer.getvalue())
        return self.result

    def clis(self) -> Clis:
        return Clis(
            run=self.run,
            which=lambda name: name if name in self.installed else None,
            codex_home=self.home,
        )


@pytest.fixture
def fake_cli(tmp_path: Path) -> FakeCli:
    return FakeCli(tmp_path / "codex-home")


@pytest.fixture
def anthropic_client() -> FakeAnthropic:
    return FakeAnthropic()


@pytest.fixture
def stock_transport() -> httpx.MockTransport:
    """No test reaches the internet: an unexpected request fails loudly."""

    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request: {request.url}")

    return httpx.MockTransport(refuse)


@pytest.fixture
async def context(
    settings: Settings,
    sessions: SessionFactory,
    anthropic_client: FakeAnthropic,
    stock_transport: httpx.MockTransport,
    fake_cli: FakeCli,
) -> AsyncIterator[AppContext]:
    async with httpx.AsyncClient(transport=stock_transport) as http:
        yield AppContext(
            settings=settings,
            sessions=sessions,
            board=JobBoard(),
            http=http,
            anthropic=anthropic_client,  # type: ignore[arg-type] # a fake with the same shape
            clis=fake_cli.clis(),
        )


@pytest.fixture
async def client(context: AppContext) -> AsyncIterator[httpx.AsyncClient]:
    app = build_app(context)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://album") as http:
        yield http
    await context.board.wait()


@pytest.fixture
def scene() -> np.ndarray:
    """A textured, noisy outdoor-ish scene: bright above, darker ground below."""
    rng = np.random.default_rng(7)
    height, width = 240, 320
    ramp = np.linspace(210, 110, height)[:, None, None]
    base = np.tile(ramp, (1, width, 3)) * np.array([1.0, 0.92, 0.75])
    noisy = base + rng.normal(0, 6, base.shape)
    return np.clip(noisy, 0, 255).astype(np.uint8)


@pytest.fixture
def person() -> tuple[np.ndarray, np.ndarray]:
    """A clean, blue, ellipse-shaped subject on green, with a soft matte."""
    import cv2

    height, width = 200, 120
    rgb = np.zeros((height, width, 3), dtype=np.uint8)
    rgb[...] = (20, 200, 40)
    outline = np.zeros((height, width), dtype=np.float32)
    cv2.ellipse(outline, (60, 100), (40, 90), 0, 0, 360, 1.0, -1)
    matte = np.asarray(cv2.GaussianBlur(outline, (0, 0), 2.0), dtype=np.float32)
    rgb[matte > 0.5] = (60, 80, 170)
    return rgb, matte
