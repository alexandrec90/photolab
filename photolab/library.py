"""Bringing the photo folder into the database.

The folder is read-only to this project: nothing under it is ever written, moved or
deleted. Derived files go to the data directory.
"""

import asyncio
import hashlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from photolab import imaging
from photolab.config import Settings
from photolab.models import ORIGIN_LIBRARY, REVIEW_PENDING, Photo
from photolab.search import build_search_text

_READ_CHUNK = 1024 * 1024

Progress = Callable[[int, int], None]


@dataclass(frozen=True)
class PhotoFacts:
    """What one read of a file establishes."""

    sha256: str
    width: int
    height: int
    format: str
    taken_at: datetime | None
    exif: dict[str, Any]
    size_bytes: int
    mtime_ns: int


@dataclass
class ScanResult:
    added: int = 0
    moved: int = 0
    changed: int = 0
    unchanged: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)


def iter_photo_files(root: Path) -> Iterator[Path]:
    """Every supported image under `root`, in a stable order, skipping dot-directories."""
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if path.suffix.lower() in imaging.SUPPORTED_SUFFIXES and path.is_file():
            yield path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_READ_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def thumbnail_path(data_dir: Path, sha256: str) -> Path:
    return data_dir / "thumbs" / sha256[:2] / f"{sha256}.webp"


def photo_file(photo: Photo, settings: Settings) -> Path:
    root = settings.library_dir if photo.origin == ORIGIN_LIBRARY else settings.data_dir
    return root / photo.path


def ingest_file(path: Path, data_dir: Path) -> PhotoFacts:
    """Read one file: hash it, take its facts, and write its thumbnail."""
    sha256 = sha256_file(path)
    stat = path.stat()
    with Image.open(path) as image:
        exif = imaging.exif_summary(image)
        file_format = image.format or path.suffix.lstrip(".").upper()
        upright = imaging.to_srgb(ImageOps.exif_transpose(image) or image)
    imaging.write_thumbnail(upright, thumbnail_path(data_dir, sha256))
    return PhotoFacts(
        sha256=sha256,
        width=upright.width,
        height=upright.height,
        format=file_format,
        taken_at=imaging.parse_taken_at(exif.get("taken_at")),
        exif=exif,
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )


def apply_facts(photo: Photo, facts: PhotoFacts) -> None:
    photo.sha256 = facts.sha256
    photo.width = facts.width
    photo.height = facts.height
    photo.format = facts.format
    photo.taken_at = facts.taken_at
    photo.exif = facts.exif
    photo.size_bytes = facts.size_bytes
    photo.mtime_ns = facts.mtime_ns


def _record_new(session: AsyncSession, relative: str, facts: PhotoFacts) -> None:
    photo = Photo(path=relative, origin=ORIGIN_LIBRARY, people=[], annotation=None)
    photo.search_text = build_search_text(photo)
    apply_facts(photo, facts)
    session.add(photo)


def _record_replaced(photo: Photo, facts: PhotoFacts) -> None:
    """Same path, different bytes: everything said about the old image no longer applies."""
    apply_facts(photo, facts)
    photo.annotation = None
    photo.annotation_error = None
    photo.review_status = REVIEW_PENDING
    photo.search_text = build_search_text(photo)


async def _reconcile(
    session: AsyncSession,
    library_dir: Path,
    relative: str,
    at_path: Photo | None,
    facts: PhotoFacts,
    result: ScanResult,
) -> None:
    same_bytes = await session.scalar(select(Photo).where(Photo.sha256 == facts.sha256))
    if same_bytes is None:
        if at_path is None:
            _record_new(session, relative, facts)
            result.added += 1
        else:
            _record_replaced(at_path, facts)
            result.changed += 1
    elif same_bytes is at_path:
        apply_facts(same_bytes, facts)
        result.unchanged += 1
    elif same_bytes.origin != ORIGIN_LIBRARY or (library_dir / same_bytes.path).exists():
        # Includes an output copied into the library: it stays an output, with its lineage.
        result.duplicates += 1
    else:
        same_bytes.path = relative
        same_bytes.search_text = build_search_text(same_bytes)
        result.moved += 1


async def scan_library(
    session: AsyncSession, settings: Settings, progress: Progress | None = None
) -> ScanResult:
    """Bring the database up to date with the folder.

    A file missing from the folder keeps its row: an unmounted drive looks exactly like a
    deleted library, and throwing away paid-for annotations over that is not recoverable.
    """
    result = ScanResult()
    if not settings.library_dir.is_dir():
        result.errors.append(f"library folder not found: {settings.library_dir}")
        return result

    rows = await session.scalars(select(Photo).where(Photo.origin == ORIGIN_LIBRARY))
    known = {photo.path: photo for photo in rows.unique()}
    paths = await asyncio.to_thread(lambda: list(iter_photo_files(settings.library_dir)))

    for index, path in enumerate(paths, start=1):
        relative = path.relative_to(settings.library_dir).as_posix()
        at_path = known.get(relative)
        stat = path.stat()
        if at_path and (at_path.size_bytes, at_path.mtime_ns) == (stat.st_size, stat.st_mtime_ns):
            result.unchanged += 1
        else:
            try:
                facts = await asyncio.to_thread(ingest_file, path, settings.data_dir)
            except (
                OSError,
                ValueError,
                UnidentifiedImageError,
                Image.DecompressionBombError,
            ) as exc:
                result.errors.append(f"{relative}: {exc}")
            else:
                await _reconcile(session, settings.library_dir, relative, at_path, facts, result)
                # Per file, so a long first scan that dies keeps what it finished.
                await session.commit()
        if progress:
            progress(index, len(paths))
    return result
