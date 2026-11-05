"""Making a new photo from existing ones, and recording exactly how.

A generation's `params` are the whole recipe, so an output can be audited, and later
reproduced, from its row alone.
"""

import asyncio
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import update

from photolab import agents, ai_images, compositing, imaging, library, matting, transforms
from photolab.agents import Clis
from photolab.compositing import CompositeOptions, Light, Placement
from photolab.config import Settings
from photolab.db import SessionFactory
from photolab.models import (
    ORIGIN_GENERATED,
    ORIGIN_STOCK,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    Generation,
    Photo,
    utcnow,
)
from photolab.search import build_search_text

# A larger background adds memory and minutes, not visible quality.
_WORKING_EDGE = 3000
_SOFTWARE_TAG = "photolab"
_FAILURES = (OSError, ValueError, agents.AgentUnavailable)
# More images add little and spend the plan faster.
_MAX_REFERENCES = 6


class EnhanceRequest(BaseModel):
    kind: Literal["enhance"] = "enhance"
    photo_id: int
    recipe: Literal["natural", "film", "golden_hour"] = "natural"
    seed: int = 0


class CompositeRequest(BaseModel):
    kind: Literal["composite"] = "composite"
    subject_id: int
    background_id: int
    center_x: float = Field(default=0.5, ge=-0.5, le=1.5)
    baseline_y: float = Field(default=0.92, ge=0.0, le=2.0)
    height: float = Field(default=0.6, gt=0.0, le=3.0)
    flip: bool = False
    color_match: float = Field(default=0.6, ge=0.0, le=1.0)
    shadow_strength: float = Field(default=0.6, ge=0.0, le=1.0)
    light_wrap: float = Field(default=0.3, ge=0.0, le=1.0)
    light: Light = "diffuse"
    seed: int = 0


class AiRequest(BaseModel):
    """An image from Codex: from words, from album photos, or a relight of one photo."""

    kind: Literal["ai"] = "ai"
    prompt: str = Field(default="", max_length=4000)
    reference_ids: list[int] = Field(default_factory=list, max_length=_MAX_REFERENCES)
    preset: ai_images.Preset = "free"

    @model_validator(mode="after")
    def _has_something_to_do(self) -> "AiRequest":
        if self.preset == "relight" and len(self.reference_ids) != 1:
            raise ValueError("a relight takes exactly one photo")
        if self.preset == "free" and not self.prompt.strip():
            raise ValueError("say what to make")
        return self


AnyRequest = EnhanceRequest | CompositeRequest | AiRequest
GenerationRequest = Annotated[AnyRequest, Field(discriminator="kind")]


def output_file(data_dir: Path, generation_id: int, suffix: str = ".jpg") -> Path:
    return data_dir / "outputs" / f"generation-{generation_id}{suffix}"


def stock_file(data_dir: Path, sha256: str) -> Path:
    return data_dir / "stock" / f"{sha256}.jpg"


def render_enhance(request: EnhanceRequest, source: Path) -> bytes:
    pixels = imaging.to_array(imaging.load_rgb(source))
    output = transforms.apply_recipe(pixels, request.recipe, request.seed)
    return imaging.encode_jpeg(imaging.from_array(output), software=_SOFTWARE_TAG)


def render_composite(
    request: CompositeRequest,
    subject: Path,
    background: Path,
    matte_file: Path,
    matting_model: str,
) -> bytes:
    person = imaging.load_rgb(subject)
    matte = matting.subject_matte(person, matte_file, matting_model)
    scene = imaging.fit_within(imaging.load_rgb(background), _WORKING_EDGE)
    output = compositing.composite(
        imaging.to_array(person),
        matte,
        imaging.to_array(scene),
        Placement(request.center_x, request.baseline_y, request.height, request.flip),
        CompositeOptions(
            color_match=request.color_match,
            shadow_strength=request.shadow_strength,
            light_wrap=request.light_wrap,
            light=request.light,
            seed=request.seed,
        ),
    )
    return imaging.encode_jpeg(imaging.from_array(output), software=_SOFTWARE_TAG)


def request_from_row(row: Generation) -> AnyRequest:
    kinds: dict[str, type[AnyRequest]] = {
        "enhance": EnhanceRequest,
        "composite": CompositeRequest,
        "ai": AiRequest,
    }
    return kinds[row.kind].model_validate(row.params)


def request_inputs(request: AnyRequest) -> list[int]:
    """Every photo `request` is made from."""
    if isinstance(request, EnhanceRequest):
        return [request.photo_id]
    if isinstance(request, CompositeRequest):
        return [request.subject_id, request.background_id]
    return request.reference_ids


def queue_generation(request: AnyRequest) -> Generation:
    """A row for `request`, not yet added to a session.

    The columns hold the first two inputs, which keeps those photos from being deleted;
    the whole list of an AI image's references is in `params`.
    """
    inputs = request_inputs(request)
    subject_id = inputs[0] if inputs else None
    background_id = inputs[1] if len(inputs) > 1 else None
    return Generation(
        kind=request.kind,
        params=request.model_dump(),
        subject_id=subject_id,
        background_id=background_id,
        status=STATUS_QUEUED,
    )


async def _load_inputs(request: AnyRequest, sessions: SessionFactory) -> list[Photo]:
    photos = []
    async with sessions() as session:
        for photo_id in request_inputs(request):
            photo = await session.get(Photo, photo_id)
            if photo is None:
                raise ValueError(f"photo {photo_id} no longer exists")
            photos.append(photo)
    return photos


async def _render_ai(
    request: AiRequest, photos: list[Photo], settings: Settings, clis: Clis
) -> bytes:
    references = [
        ai_images.Reference(
            library.photo_file(photo, settings), ai_images.describe_reference(photo)
        )
        for photo in photos
    ]
    prompt = ai_images.build_prompt(
        request.prompt, request.preset, [reference.description for reference in references]
    )
    return await asyncio.to_thread(
        ai_images.render, prompt, references, clis, settings.image_agent_model
    )


async def _render(
    row: Generation, sessions: SessionFactory, settings: Settings, clis: Clis
) -> tuple[bytes, str]:
    """The finished image, and the file suffix it is to be kept under."""
    request = request_from_row(row)
    photos = await _load_inputs(request, sessions)
    if isinstance(request, AiRequest):
        # Kept as it came: re-encoding would lose quality, and any provenance it carries.
        return await _render_ai(request, photos, settings, clis), ".png"
    source = library.photo_file(photos[0], settings)
    if isinstance(request, EnhanceRequest):
        return await asyncio.to_thread(render_enhance, request, source), ".jpg"
    model = settings.matting_model
    rendered = await asyncio.to_thread(
        render_composite,
        request,
        source,
        library.photo_file(photos[1], settings),
        matting.matte_path(settings.data_dir, photos[0].sha256, model),
        model,
    )
    return rendered, ".jpg"


def _record_output(photo_bytes: bytes, destination: Path, settings: Settings) -> Photo:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(photo_bytes)
    facts = library.ingest_file(destination, settings.data_dir)
    photo = Photo(
        path=destination.relative_to(settings.data_dir).as_posix(),
        origin=ORIGIN_GENERATED,
        people=[],
        annotation=None,
    )
    library.apply_facts(photo, facts)
    photo.search_text = build_search_text(photo)
    return photo


async def run_generation(
    generation_id: int,
    sessions: SessionFactory,
    settings: Settings,
    clis: Clis | None = None,
) -> None:
    """Render one queued generation, leaving its row `done` or `failed`, never `running`."""
    async with sessions() as session:
        row = await session.get(Generation, generation_id)
        if row is None:
            return
        row.status = STATUS_RUNNING
        await session.commit()
        try:
            rendered, suffix = await _render(row, sessions, settings, clis or Clis())
            destination = output_file(settings.data_dir, generation_id, suffix)
            output = await asyncio.to_thread(_record_output, rendered, destination, settings)
            session.add(output)
            await session.flush()
        except _FAILURES as exc:
            await session.rollback()
            row.status = STATUS_FAILED
            row.error = str(exc)
        else:
            row.output_id = output.id
            row.status = STATUS_DONE
        row.finished_at = utcnow()
        await session.commit()


async def fail_interrupted(sessions: SessionFactory) -> int:
    """Close out what a restart left behind: nothing resumes a job that lived in memory."""
    statement = (
        update(Generation)
        .where(Generation.status.in_([STATUS_QUEUED, STATUS_RUNNING]))
        .values(status=STATUS_FAILED, error="interrupted by a restart", finished_at=utcnow())
    )
    async with sessions() as session:
        result = await session.execute(statement.returning(Generation.id))
        await session.commit()
    return len(result.all())


def store_stock(image_bytes: bytes, source: dict[str, Any], settings: Settings) -> Photo:
    """Save a downloaded background, and describe it as a row not yet in a session."""
    interim = settings.data_dir / "stock" / f"incoming-{source['id']}.tmp"
    interim.parent.mkdir(parents=True, exist_ok=True)
    interim.write_bytes(image_bytes)
    try:
        sha256 = library.sha256_file(interim)
        destination = stock_file(settings.data_dir, sha256)
        interim.replace(destination)
    finally:
        interim.unlink(missing_ok=True)
    facts = library.ingest_file(destination, settings.data_dir)
    photo = Photo(
        path=destination.relative_to(settings.data_dir).as_posix(),
        origin=ORIGIN_STOCK,
        source=source,
        people=[],
        annotation=None,
    )
    library.apply_facts(photo, facts)
    photo.search_text = build_search_text(photo)
    return photo
