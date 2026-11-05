"""The JSON the album sends and receives."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from photolab.models import Generation, Photo

ReviewStatus = Literal["pending", "approved", "rejected"]


class PhotoSummary(BaseModel):
    id: int
    origin: str
    path: str
    width: int
    height: int
    taken_at: datetime | None
    review_status: str
    people: list[str]
    caption: str | None
    annotated: bool
    annotation_error: str | None

    @classmethod
    def of(cls, photo: Photo) -> "PhotoSummary":
        annotation = photo.annotation
        return cls(
            id=photo.id,
            origin=photo.origin,
            path=photo.path,
            width=photo.width,
            height=photo.height,
            taken_at=photo.taken_at,
            review_status=photo.review_status,
            people=photo.people,
            caption=annotation.data.get("caption") if annotation else None,
            annotated=annotation is not None,
            annotation_error=photo.annotation_error,
        )


class AnnotationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    data: dict[str, Any]
    original: dict[str, Any]
    model: str
    prompt_version: int
    updated_at: datetime


class GenerationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    params: dict[str, Any]
    status: str
    error: str | None
    subject_id: int | None
    background_id: int | None
    output_id: int | None
    created_at: datetime
    finished_at: datetime | None


class PhotoDetail(PhotoSummary):
    format: str
    exif: dict[str, Any]
    source: dict[str, Any]
    annotation: AnnotationView | None
    made_by: GenerationView | None

    @classmethod
    def build(cls, photo: Photo, made_by: Generation | None) -> "PhotoDetail":
        return cls(
            **PhotoSummary.of(photo).model_dump(),
            format=photo.format,
            exif=photo.exif,
            source=photo.source,
            annotation=AnnotationView.model_validate(photo.annotation)
            if photo.annotation
            else None,
            made_by=GenerationView.model_validate(made_by) if made_by else None,
        )


class PhotoPage(BaseModel):
    total: int
    items: list[PhotoSummary]


class PhotoFilters(BaseModel):
    """The album listing's query string."""

    q: str | None = None
    origin: str | None = None
    review: str | None = None
    annotated: bool | None = None
    reviewable: bool = False
    limit: int = Field(default=60, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class PhotoEdit(BaseModel):
    """A partial edit. `annotation` holds only the fields being corrected."""

    model_config = ConfigDict(extra="forbid")

    review_status: ReviewStatus | None = None
    people: list[str] | None = None
    annotation: dict[str, Any] | None = None


class AnnotateStart(BaseModel):
    limit: int | None = Field(default=None, ge=1)
    retry_failed: bool = False
    # Left open per run, so that a sample from each can be compared in the audit.
    backend: Literal["codex", "claude", "api"] | None = None
    model: str | None = Field(default=None, min_length=1, max_length=64)


class SuggestRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)


class AdviceView(BaseModel):
    light: str
    flip: bool
    warnings: list[str]


class Stats(BaseModel):
    photos: int
    outputs: int
    annotated: int
    unannotated: int
    failed: int
    to_review: int
    approved: int
    rejected: int
    annotation_backends: list[str]
    annotation_backend: str
    annotation_model: str
    annotation_ready: bool
    image_ready: bool
    stock_ready: bool
    jobs: list[dict[str, Any]]


class StockImport(BaseModel):
    pexels_id: int
