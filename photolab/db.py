"""The async engine, and the event loop psycopg can run on."""

import asyncio
from collections.abc import Coroutine
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

SessionFactory = async_sessionmaker[AsyncSession]


def make_engine(url: str, **kwargs: Any) -> AsyncEngine:
    return create_async_engine(url, **kwargs)


def make_session_factory(engine: AsyncEngine) -> SessionFactory:
    # `expire_on_commit=False`: a route serialises an object after committing it, and an
    # expired attribute there is an implicit refresh, which async SQLAlchemy cannot do.
    return async_sessionmaker(engine, expire_on_commit=False)


def run_async[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine on a selector loop.

    psycopg's async mode cannot use Windows' default proactor loop, so a host CLI run
    needs this. On Linux it is the default anyway.
    """
    return asyncio.run(coroutine, loop_factory=asyncio.SelectorEventLoop)
