"""Starting work: scans, annotation runs, stock imports and generations."""

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from photolab import agents, ai_images, generate, stock
from photolab import annotate as annotation_runs
from photolab.advice import advise
from photolab.api.deps import AppContext, Context, Session
from photolab.api.schemas import (
    AdviceView,
    AnnotateStart,
    GenerationView,
    PhotoSummary,
    StockImport,
    SuggestRequest,
)
from photolab.generate import AiRequest, GenerationRequest
from photolab.jobs import Job, JobBusy
from photolab.library import scan_library
from photolab.models import Generation, Photo

router = APIRouter(prefix="/api")


def _start(context: AppContext, name: str, work: Any, job: Job) -> dict[str, str]:
    try:
        context.board.start(name, work, job)
    except JobBusy as exc:
        raise HTTPException(status_code=409, detail=f"{exc} is already running") from exc
    return {"status": "started"}


async def _scan(context: AppContext, job: Job) -> str:
    def progress(done: int, total: int) -> None:
        job.done, job.total = done, total

    async with context.sessions() as session:
        result = await scan_library(session, context.settings, progress)
    job.failed = len(result.errors)
    summary = f"{result.added} new, {result.moved} moved, {result.changed} changed"
    return f"{summary}; first error: {result.errors[0]}" if result.errors else summary


@router.post("/library/scan", status_code=202)
async def start_scan(context: Context) -> dict[str, str]:
    job = Job(name="scan")
    return _start(context, "scan", _scan(context, job), job)


async def _annotate(
    context: AppContext,
    job: Job,
    annotator: annotation_runs.Annotator,
    request: AnnotateStart,
) -> str:
    def progress(done: int, failed: int, total: int) -> None:
        job.done, job.failed, job.total = done, failed, total

    result = await annotation_runs.annotate_pending(
        annotator,
        context.sessions,
        context.settings,
        limit=request.limit,
        retry_failed=request.retry_failed,
        progress=progress,
    )
    if result.aborted:
        return f"stopped: {result.aborted}"
    return f"{result.annotated} annotated, {result.failed} failed"


@router.post("/annotations/run", status_code=202)
async def start_annotation(context: Context, request: AnnotateStart) -> dict[str, str]:
    try:
        annotator = annotation_runs.make_annotator(
            context.settings, context.anthropic, context.clis, request.backend, request.model
        )
    except annotation_runs.NoAnnotator as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    job = Job(name="annotate")
    return _start(context, "annotate", _annotate(context, job, annotator, request), job)


@router.post("/ai/suggest")
async def suggest_references(request: SuggestRequest, context: Context) -> ai_images.Suggestion:
    """Choose reference photos by reading the catalog, without looking at any image."""
    try:
        return await ai_images.suggest(
            request.prompt, context.sessions, context.settings, context.clis
        )
    except (ai_images.NoSuggester, agents.AgentUnavailable) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except agents.AgentFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/stock/search")
async def search_stock(q: str, context: Context) -> list[dict[str, Any]]:
    key = context.settings.pexels_api_key
    if not key:
        raise HTTPException(status_code=409, detail="PEXELS_API_KEY is not set in .env")
    try:
        photos = await stock.search_photos(context.http, key, q)
    except stock.StockError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return [
        {
            "id": photo.id,
            "preview_url": photo.preview_url,
            "photographer": photo.photographer,
            "alt": photo.alt,
        }
        for photo in photos
    ]


@router.post("/stock/import", status_code=201)
async def import_stock(request: StockImport, context: Context, session: Session) -> PhotoSummary:
    """Bring one Pexels photo in by id. Its URL is looked up here, never taken from a client."""
    key = context.settings.pexels_api_key
    if not key:
        raise HTTPException(status_code=409, detail="PEXELS_API_KEY is not set in .env")
    try:
        found = await stock.get_photo(context.http, key, request.pexels_id)
        content = await stock.download_photo(context.http, found)
    except stock.StockError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    photo = await asyncio.to_thread(
        generate.store_stock, content, stock.attribution(found), context.settings
    )
    existing = await session.scalar(select(Photo).where(Photo.sha256 == photo.sha256))
    if existing is not None:
        return PhotoSummary.of(existing)
    session.add(photo)
    await session.commit()
    return PhotoSummary.of(photo)


@router.get("/compose/advice")
async def compose_advice(subject_id: int, background_id: int, session: Session) -> AdviceView:
    subject = await session.get(Photo, subject_id)
    background = await session.get(Photo, background_id)
    if subject is None or background is None:
        raise HTTPException(status_code=404, detail="no such photo")
    advice = advise(
        subject.annotation.data if subject.annotation else {},
        background.annotation.data if background.annotation else {},
    )
    return AdviceView(light=advice.light, flip=advice.flip, warnings=advice.warnings)


@router.post("/generations", status_code=202)
async def create_generation(
    request: GenerationRequest, context: Context, session: Session
) -> GenerationView:
    if isinstance(request, AiRequest) and not context.clis.can_make_images:
        raise HTTPException(
            status_code=409,
            detail="AI images need the codex command, so run the album on this machine with "
            "scripts/album.py rather than in Docker",
        )
    row = generate.queue_generation(request)
    for photo_id in generate.request_inputs(request):
        if await session.get(Photo, photo_id) is None:
            raise HTTPException(status_code=404, detail=f"no photo {photo_id}")
    session.add(row)
    await session.commit()
    context.board.render(
        generate.run_generation(row.id, context.sessions, context.settings, context.clis)
    )
    return GenerationView.model_validate(row)


@router.get("/generations")
async def list_generations(session: Session, limit: int = 50) -> list[GenerationView]:
    statement = select(Generation).order_by(Generation.id.desc()).limit(limit)
    return [GenerationView.model_validate(row) for row in await session.scalars(statement)]
