import asyncio
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

import photolab.__main__ as cli
from photolab.annotate import PhotoAnnotation, store_annotation
from photolab.config import Settings
from photolab.db import make_engine, make_session_factory, run_async
from photolab.library import scan_library
from photolab.models import Photo
from tests.conftest import ANNOTATION, FakeAnthropic, FakeCli, database_url, write_photo


@pytest.fixture
def same_engine(monkeypatch, engine: AsyncEngine) -> None:
    """Point the CLI's own `make_engine` at the test schema."""
    monkeypatch.setattr("photolab.db.make_engine", lambda _: engine)


def test_parser_defaults_to_serving():
    assert cli.build_parser().parse_args([]).command is None


def test_parser_reads_annotate_options():
    args = cli.build_parser().parse_args(["annotate", "--limit", "5", "--retry-failed"])

    assert (args.limit, args.retry_failed) == (5, True)


def test_parser_reads_a_backend_and_model_for_one_run():
    args = cli.build_parser().parse_args(["annotate", "--backend", "claude", "--model", "sonnet"])

    assert (args.backend, args.model) == ("claude", "sonnet")


def test_the_api_stays_on_loopback_unless_asked():
    assert cli.build_parser().parse_args(["serve"]).host == "127.0.0.1"


def test_serve_runs_the_app_on_a_loop_psycopg_can_use(monkeypatch):
    # uvicorn's own default on Windows is the proactor loop, and the API then dies at startup.
    seen = {}

    async def record(self, sockets=None) -> None:
        seen.update(config=self.config, loop=asyncio.get_running_loop())

    monkeypatch.setattr("uvicorn.Server.serve", record)

    assert cli.serve("127.0.0.1", 9000) == 0
    assert isinstance(seen["loop"], asyncio.SelectorEventLoop)
    assert seen["config"].app == "photolab.api.app:create_app"
    assert (seen["config"].host, seen["config"].port) == ("127.0.0.1", 9000)


def test_main_with_no_command_serves(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "serve", lambda host, port: calls.append((host, port)) or 0)

    assert cli.main([]) == 0
    assert calls == [("127.0.0.1", 8000)]


def test_run_async_returns_the_result():
    async def answer() -> int:
        await asyncio.sleep(0)
        return 42

    assert run_async(answer()) == 42


async def test_scan_prints_a_summary_and_fails_on_errors(same_engine, settings, capsys):
    write_photo(settings)
    (settings.library_dir / "broken.jpg").write_bytes(b"no")

    code = await cli.run_scan(settings)

    captured = capsys.readouterr()
    assert code == 1
    assert "1 added" in captured.out
    assert "broken.jpg" in captured.err


async def test_annotate_with_nothing_to_annotate_with_says_so(
    settings, monkeypatch, tmp_path, capsys
):
    settings.anthropic_api_key = ""
    monkeypatch.setattr("photolab.agents.Clis", FakeCli(tmp_path, ()).clis)

    assert await cli.run_annotate(settings, None, False) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


async def test_annotate_runs_with_a_client(same_engine, sessions, settings, monkeypatch, capsys):
    write_photo(settings)
    async with sessions() as session:
        await scan_library(session, settings)
    monkeypatch.setattr("photolab.api.app.make_anthropic", lambda _: FakeAnthropic())

    assert await cli.run_annotate(settings, None, False) == 0
    assert "1 annotated" in capsys.readouterr().out


async def test_catalog_prints_annotated_photos(same_engine, sessions, settings, capsys):
    write_photo(settings)
    async with sessions() as session:
        await scan_library(session, settings)
        photo = (await session.scalars(__import__("sqlalchemy").select(Photo))).unique().one()
        store_annotation(photo, PhotoAnnotation.model_validate(ANNOTATION), "m")
        await session.commit()

    assert await cli.run_catalog(settings, "beach") == 0
    assert "#1" in capsys.readouterr().out


def test_settings_read_the_aliased_folders(tmp_path: Path):
    settings = Settings(
        database_url="postgresql+psycopg://x",
        PHOTO_LIBRARY_DIR=tmp_path,
        _env_file=None,  # type: ignore[call-arg] # pydantic-settings' init-only override
    )

    assert settings.library_dir == tmp_path
    assert settings.annotation_backend == "codex"
    assert settings.annotation_model == ""


def test_settings_on_the_host_read_the_same_folders_compose_mounts(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PHOTO_LIBRARY_DIR", raising=False)
    monkeypatch.setenv("PHOTO_LIBRARY_HOST_DIR", str(tmp_path / "host"))
    monkeypatch.setenv("PHOTOLAB_DATA_HOST_DIR", str(tmp_path / "data"))

    settings = Settings(database_url="postgresql+psycopg://x", _env_file=None)  # type: ignore[call-arg] # as above

    assert settings.library_dir == tmp_path / "host"
    assert settings.data_dir == tmp_path / "data"


def test_settings_in_a_container_prefer_the_mounted_folder(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PHOTO_LIBRARY_HOST_DIR", str(tmp_path / "host"))
    monkeypatch.setenv("PHOTO_LIBRARY_DIR", "/photos")

    settings = Settings(database_url="postgresql+psycopg://x", _env_file=None)  # type: ignore[call-arg] # as above

    assert settings.library_dir == Path("/photos")


def test_migrations_build_exactly_the_models(monkeypatch):
    """The real upgrade path, in a throwaway schema, compared against the models."""
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.config import Config
    from alembic.migration import MigrationContext

    from photolab.models import Base

    name = "test_migrations"
    url = database_url()
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{name}" CASCADE'))
        connection.execute(text(f'CREATE SCHEMA "{name}"'))
    scoped = f"{url}{'&' if '?' in url else '?'}options=-csearch_path%3D{name}"
    monkeypatch.setenv("DATABASE_URL", scoped)
    try:
        command.upgrade(Config("alembic.ini"), "head")
        engine = create_engine(scoped)
        with engine.connect() as connection:
            assert set(inspect(connection).get_table_names()) >= {
                "photos",
                "annotations",
                "generations",
            }
            context = MigrationContext.configure(connection)
            assert compare_metadata(context, Base.metadata) == []
        engine.dispose()
    finally:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{name}" CASCADE'))
        admin.dispose()


async def test_make_engine_builds_a_usable_factory():
    engine = make_engine(database_url())
    async with make_session_factory(engine)() as session:
        assert await session.scalar(text("select 1")) == 1
    await engine.dispose()
