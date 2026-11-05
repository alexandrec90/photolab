"""`python -m photolab`: the server, and the same work from a terminal."""

import argparse
import sys
from typing import TYPE_CHECKING

from photolab.config import Settings

if TYPE_CHECKING:
    from photolab.agents import Backend


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="photolab")
    commands = parser.add_subparsers(dest="command")

    serve = commands.add_parser("serve", help="run the API (the default)")
    # Loopback, because the API has no login. Compose asks for more, and publishes it to
    # loopback itself.
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    commands.add_parser("scan", help="bring the photo folder into the database")

    annotate = commands.add_parser(
        "annotate", help="annotate photos that need it (uses up a plan's limits, or money)"
    )
    annotate.add_argument("--limit", type=int)
    annotate.add_argument("--retry-failed", action="store_true")
    annotate.add_argument("--backend", choices=["codex", "claude", "api"])
    annotate.add_argument("--model")

    catalog = commands.add_parser("catalog", help="print the annotation catalog for an LLM")
    catalog.add_argument("query", nargs="?")
    return parser


async def run_scan(settings: Settings) -> int:
    from photolab.db import make_engine, make_session_factory
    from photolab.library import scan_library

    engine = make_engine(settings.database_url)
    async with make_session_factory(engine)() as session:
        result = await scan_library(session, settings)
    await engine.dispose()
    print(
        f"{result.added} added, {result.moved} moved, {result.changed} changed, "
        f"{result.unchanged} unchanged, {result.duplicates} duplicates"
    )
    for error in result.errors:
        print(f"error: {error}", file=sys.stderr)
    return 1 if result.errors else 0


async def run_annotate(
    settings: Settings,
    limit: int | None,
    retry_failed: bool,
    backend: "Backend | None" = None,
    model: str | None = None,
) -> int:
    from photolab.agents import Clis
    from photolab.annotate import NoAnnotator, annotate_pending, make_annotator
    from photolab.api.app import make_anthropic
    from photolab.db import make_engine, make_session_factory

    try:
        annotator = make_annotator(settings, make_anthropic(settings), Clis(), backend, model)
    except NoAnnotator as exc:
        print(exc, file=sys.stderr)
        return 2
    engine = make_engine(settings.database_url)
    result = await annotate_pending(
        annotator, make_session_factory(engine), settings, limit=limit, retry_failed=retry_failed
    )
    await engine.dispose()
    print(f"{result.annotated} annotated by {annotator.name}, {result.failed} failed")
    if result.aborted:
        print(f"stopped: {result.aborted}", file=sys.stderr)
    return 1 if result.aborted or result.failed else 0


async def run_catalog(settings: Settings, query: str | None) -> int:
    from photolab import search
    from photolab.db import make_engine, make_session_factory

    engine = make_engine(settings.database_url)
    async with make_session_factory(engine)() as session:
        rows = await session.scalars(search.catalog_statement(query))
        print(search.render_catalog(list(rows.unique())))
    await engine.dispose()
    return 0


def serve(host: str, port: int) -> int:
    """Run the API on `run_async`'s loop: `uvicorn.run` picks one psycopg cannot use on Windows."""
    import uvicorn

    from photolab.db import run_async

    config = uvicorn.Config("photolab.api.app:create_app", factory=True, host=host, port=port)
    run_async(uvicorn.Server(config).serve())
    return 0


def main(argv: list[str] | None = None) -> int:
    from photolab.db import run_async

    args = build_parser().parse_args(argv)
    if args.command in (None, "serve"):
        return serve(getattr(args, "host", "127.0.0.1"), getattr(args, "port", 8000))
    settings = Settings()  # type: ignore[call-arg] # filled from the environment
    if args.command == "scan":
        return run_async(run_scan(settings))
    if args.command == "annotate":
        return run_async(
            run_annotate(settings, args.limit, args.retry_failed, args.backend, args.model)
        )
    return run_async(run_catalog(settings, args.query))


if __name__ == "__main__":
    raise SystemExit(main())
