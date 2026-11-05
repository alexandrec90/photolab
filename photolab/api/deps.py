"""What every route needs, resolved from the app's state."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Annotated

import anthropic
import httpx
from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from photolab.agents import Clis
from photolab.config import Settings
from photolab.db import SessionFactory
from photolab.jobs import JobBoard
from photolab.models import Photo


@dataclass
class AppContext:
    """Built once at startup; a test builds one with fakes in it."""

    settings: Settings
    sessions: SessionFactory
    board: JobBoard
    http: httpx.AsyncClient
    anthropic: anthropic.AsyncAnthropic | None
    clis: Clis = field(default_factory=Clis)


def get_context(request: Request) -> AppContext:
    return request.app.state.context


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.context.sessions() as session:
        yield session


Context = Annotated[AppContext, Depends(get_context)]
Session = Annotated[AsyncSession, Depends(get_session)]


async def require_photo(photo_id: int, session: Session) -> Photo:
    photo = await session.get(Photo, photo_id)
    if photo is None:
        raise HTTPException(status_code=404, detail=f"no photo {photo_id}")
    return photo


FoundPhoto = Annotated[Photo, Depends(require_photo)]
