"""The persisted shape of the album.

A `Photo` is keyed by the SHA-256 of its bytes, not by its path. That is what makes
annotations a once-only cost: renaming, moving or re-importing a file reaches the same
row, so the LLM never sees those bytes twice.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

ORIGIN_LIBRARY = "library"
ORIGIN_STOCK = "stock"
ORIGIN_GENERATED = "generated"

REVIEW_PENDING = "pending"
REVIEW_APPROVED = "approved"
REVIEW_REJECTED = "rejected"

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Photo(Base):
    __tablename__ = "photos"

    id: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    origin: Mapped[str] = mapped_column(String(16), default=ORIGIN_LIBRARY, index=True)
    # Relative to the library for a library photo, to the data directory otherwise.
    path: Mapped[str] = mapped_column(Text, index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    mtime_ns: Mapped[int] = mapped_column(BigInteger, default=0)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    format: Mapped[str] = mapped_column(String(16))
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    exif: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # Where an imported photo came from: photographer, licence, link. Kept so a published
    # composite can still credit its background.
    source: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    # Human-supplied, and on this row rather than in the annotation so that it survives
    # re-annotation and can exist before the LLM has run. The model never names anyone.
    people: Mapped[list[str]] = mapped_column(JSONB, default=list)
    review_status: Mapped[str] = mapped_column(String(16), default=REVIEW_PENDING, index=True)
    annotation_error: Mapped[str | None] = mapped_column(Text)
    search_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    annotation: Mapped["Annotation | None"] = relationship(
        back_populates="photo", cascade="all, delete-orphan", uselist=False, lazy="joined"
    )


class Annotation(Base):
    __tablename__ = "annotations"

    photo_id: Mapped[int] = mapped_column(
        ForeignKey("photos.id", ondelete="CASCADE"), primary_key=True
    )
    # `data` is what the album shows and the catalog serves; `original` is what the model
    # said. Keeping both is what lets an audit show exactly what a human changed.
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    original: Mapped[dict[str, Any]] = mapped_column(JSONB)
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    photo: Mapped[Photo] = relationship(back_populates="annotation")


class Generation(Base):
    """One request to make a new photo, and the lineage of the result."""

    __tablename__ = "generations"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default=STATUS_QUEUED, index=True)
    error: Mapped[str | None] = mapped_column(Text)
    # Both empty for an image made from words alone.
    subject_id: Mapped[int | None] = mapped_column(ForeignKey("photos.id", ondelete="RESTRICT"))
    background_id: Mapped[int | None] = mapped_column(ForeignKey("photos.id", ondelete="RESTRICT"))
    output_id: Mapped[int | None] = mapped_column(ForeignKey("photos.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
