"""Browsing and auditing the album."""

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import ValidationError
from sqlalchemy import func, select

from photolab import agents, imaging, library, search
from photolab.annotate import PhotoAnnotation
from photolab.api.deps import Context, FoundPhoto, Session
from photolab.api.schemas import (
    PhotoDetail,
    PhotoEdit,
    PhotoFilters,
    PhotoPage,
    PhotoSummary,
    Stats,
)
from photolab.models import Generation, Photo

router = APIRouter(prefix="/api")


@router.get("/stats")
async def stats(session: Session, context: Context) -> Stats:
    row = (await session.execute(search.stats_statement())).one()
    settings, clis = context.settings, context.clis
    backends = agents.available_backends(settings, clis)
    backend = agents.default_backend(settings, clis)
    return Stats(
        **row._asdict(),
        annotation_backends=list(backends),
        annotation_backend=backend,
        annotation_model=agents.resolve_model(settings, backend),
        annotation_ready=bool(backends),
        image_ready=clis.can_make_images,
        stock_ready=bool(settings.pexels_api_key),
        jobs=context.board.snapshot(),
    )


@router.get("/photos")
async def list_photos(session: Session, filters: Annotated[PhotoFilters, Query()]) -> PhotoPage:
    statement = search.photos_statement(
        filters.q, filters.origin, filters.review, filters.annotated, filters.reviewable
    )
    total = await session.scalar(
        select(func.count()).select_from(statement.order_by(None).subquery())
    )
    rows = await session.scalars(statement.limit(filters.limit).offset(filters.offset))
    return PhotoPage(total=total or 0, items=[PhotoSummary.of(photo) for photo in rows.unique()])


@router.get("/catalog", response_class=PlainTextResponse)
async def catalog(session: Session, q: str | None = None) -> str:
    """What an LLM reads instead of the photos."""
    rows = await session.scalars(search.catalog_statement(q))
    return search.render_catalog(list(rows.unique()))


async def _made_by(session: Session, photo: Photo) -> Generation | None:
    return await session.scalar(select(Generation).where(Generation.output_id == photo.id))


@router.get("/photos/{photo_id}")
async def get_photo(photo: FoundPhoto, session: Session) -> PhotoDetail:
    return PhotoDetail.build(photo, await _made_by(session, photo))


def _merge_annotation(photo: Photo, fields: dict[str, Any]) -> None:
    if photo.annotation is None:
        raise HTTPException(status_code=409, detail="this photo has no annotation to correct yet")
    merged = photo.annotation.data | fields
    try:
        PhotoAnnotation.model_validate(merged)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    photo.annotation.data = merged


@router.patch("/photos/{photo_id}")
async def edit_photo(edit: PhotoEdit, photo: FoundPhoto, session: Session) -> PhotoDetail:
    if edit.annotation is not None:
        _merge_annotation(photo, edit.annotation)
    if edit.people is not None:
        photo.people = sorted({name.strip() for name in edit.people if name.strip()})
    if edit.review_status is not None:
        photo.review_status = edit.review_status
    photo.search_text = search.build_search_text(photo)
    await session.commit()
    return PhotoDetail.build(photo, await _made_by(session, photo))


_CACHE_FOREVER = {"Cache-Control": "private, max-age=31536000, immutable"}


@router.get("/photos/{photo_id}/thumb", response_model=None)
async def thumbnail(photo: FoundPhoto, context: Context) -> FileResponse:
    path = library.thumbnail_path(context.settings.data_dir, photo.sha256)
    if not path.exists():
        raise HTTPException(status_code=404, detail="thumbnail not found; rescan the library")
    # Addressed by id but its content follows the hash, which a replaced file changes.
    headers = {"ETag": f'"{photo.sha256}"', "Cache-Control": "private, no-cache"}
    return FileResponse(path, media_type="image/webp", headers=headers)


def _serve_original(path: Path, file_format: str) -> Response:
    if not path.exists():
        raise HTTPException(status_code=404, detail="the file has moved; rescan the library")
    if file_format.upper() not in imaging.TRANSCODED_FORMATS:
        return FileResponse(path)
    return Response(
        imaging.encode_jpeg(imaging.load_rgb(path), quality=90), media_type="image/jpeg"
    )


@router.get("/photos/{photo_id}/image", response_model=None)
async def image(photo: FoundPhoto, context: Context) -> Response:
    return _serve_original(library.photo_file(photo, context.settings), photo.format)
