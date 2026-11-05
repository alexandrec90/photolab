"""Assembling the application."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anthropic
import httpx
from fastapi import FastAPI

from photolab import generate
from photolab.api import actions, photos
from photolab.api.deps import AppContext
from photolab.config import Settings
from photolab.db import make_engine, make_session_factory
from photolab.jobs import JobBoard


def make_anthropic(settings: Settings) -> anthropic.AsyncAnthropic | None:
    """A client, or None when no key means annotation has to say so rather than try."""
    if not settings.anthropic_api_key:
        return None
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


def build_app(context: AppContext) -> FastAPI:
    """The routes around `context`, with no startup work of its own."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await generate.fail_interrupted(context.sessions)
        yield
        context.board.cancel_all()

    app = FastAPI(title="Photolab", lifespan=lifespan)
    app.state.context = context
    app.include_router(photos.router)
    app.include_router(actions.router)
    return app


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings()  # type: ignore[call-arg] # filled from the environment
    context = AppContext(
        settings=resolved,
        sessions=make_session_factory(make_engine(resolved.database_url)),
        board=JobBoard(),
        http=httpx.AsyncClient(),
        anthropic=make_anthropic(resolved),
    )
    return build_app(context)
