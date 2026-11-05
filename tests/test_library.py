import shutil
from pathlib import Path

from PIL import Image
from sqlalchemy import select

from photolab import library
from photolab.annotate import PhotoAnnotation, store_annotation
from photolab.library import (
    PhotoFacts,
    ScanResult,
    ingest_file,
    iter_photo_files,
    scan_library,
    sha256_file,
)
from photolab.models import ORIGIN_GENERATED, Photo
from tests.conftest import ANNOTATION, write_photo


async def all_photos(session) -> list[Photo]:
    return list((await session.scalars(select(Photo).order_by(Photo.id))).unique())


def test_iter_photo_files_finds_images_and_skips_the_rest(tmp_path: Path):
    (tmp_path / "nested").mkdir()
    (tmp_path / ".hidden").mkdir()
    for name in ("a.JPG", "nested/b.heic", "c.png", "notes.txt", ".hidden/d.jpg"):
        (tmp_path / name).write_bytes(b"x")

    found = [path.relative_to(tmp_path).as_posix() for path in iter_photo_files(tmp_path)]

    assert found == ["a.JPG", "c.png", "nested/b.heic"]


def test_sha256_file_is_content_addressed(tmp_path: Path):
    (tmp_path / "a").write_bytes(b"same")
    (tmp_path / "b").write_bytes(b"same")

    assert sha256_file(tmp_path / "a") == sha256_file(tmp_path / "b")


def test_ingest_file_writes_thumbnail_and_returns_facts(settings):
    path = write_photo(settings, size=(800, 600))

    facts = ingest_file(path, settings.data_dir)

    assert isinstance(facts, PhotoFacts)
    assert (facts.width, facts.height, facts.format) == (800, 600, "JPEG")
    assert library.thumbnail_path(settings.data_dir, facts.sha256).exists()


async def test_scan_adds_new_photos(sessions, settings):
    write_photo(settings, "a.jpg", (10, 20, 30))
    write_photo(settings, "trip/b.jpg", (30, 20, 10))

    async with sessions() as session:
        result = await scan_library(session, settings)
        photos = await all_photos(session)

    assert isinstance(result, ScanResult)
    assert result.added == 2
    assert [photo.path for photo in photos] == ["a.jpg", "trip/b.jpg"]
    assert "trip/b.jpg" in photos[1].search_text


async def test_rescan_of_unchanged_files_rehashes_nothing(sessions, settings, monkeypatch):
    write_photo(settings)
    async with sessions() as session:
        await scan_library(session, settings)

    def fail(*_: object) -> None:
        raise AssertionError("an unchanged file was read again")

    monkeypatch.setattr(library, "ingest_file", fail)
    async with sessions() as session:
        result = await scan_library(session, settings)

    assert result.unchanged == 1


async def annotate_first(sessions) -> None:
    async with sessions() as session:
        photo = (await all_photos(session))[0]
        store_annotation(photo, PhotoAnnotation.model_validate(ANNOTATION), "model")
        await session.commit()


async def test_a_moved_file_keeps_its_annotation(sessions, settings):
    path = write_photo(settings, "old/name.jpg")
    async with sessions() as session:
        await scan_library(session, settings)
    await annotate_first(sessions)

    moved_to = settings.library_dir / "new" / "renamed.jpg"
    moved_to.parent.mkdir()
    shutil.move(path, moved_to)
    async with sessions() as session:
        result = await scan_library(session, settings)
        photos = await all_photos(session)

    assert result.moved == 1
    assert len(photos) == 1
    assert photos[0].path == "new/renamed.jpg"
    assert photos[0].annotation is not None
    assert "new/renamed.jpg" in photos[0].search_text


async def test_a_copy_of_a_known_photo_is_a_duplicate_not_a_new_row(sessions, settings):
    path = write_photo(settings, "a.jpg")
    async with sessions() as session:
        await scan_library(session, settings)

    shutil.copy(path, settings.library_dir / "copy.jpg")
    async with sessions() as session:
        result = await scan_library(session, settings)

    assert result.duplicates == 1
    async with sessions() as session:
        assert len(await all_photos(session)) == 1


async def test_edited_bytes_at_a_path_discard_the_old_annotation(sessions, settings):
    write_photo(settings, "a.jpg", (10, 20, 30))
    async with sessions() as session:
        await scan_library(session, settings)
    await annotate_first(sessions)

    write_photo(settings, "a.jpg", (200, 10, 10), size=(70, 50))
    async with sessions() as session:
        result = await scan_library(session, settings)
    async with sessions() as session:
        photo = (await all_photos(session))[0]

    assert result.changed == 1
    assert photo.annotation is None
    assert photo.review_status == "pending"


async def test_a_vanished_file_keeps_its_row(sessions, settings):
    path = write_photo(settings)
    async with sessions() as session:
        await scan_library(session, settings)
    await annotate_first(sessions)

    path.unlink()
    async with sessions() as session:
        await scan_library(session, settings)
    async with sessions() as session:
        photos = await all_photos(session)

    assert len(photos) == 1
    assert photos[0].annotation is not None


async def test_an_unreadable_file_is_reported_and_does_not_stop_the_scan(sessions, settings):
    (settings.library_dir / "broken.jpg").write_bytes(b"not an image")
    write_photo(settings, "good.jpg")

    async with sessions() as session:
        result = await scan_library(session, settings)

    assert result.added == 1
    assert len(result.errors) == 1
    assert result.errors[0].startswith("broken.jpg")


async def test_missing_library_folder_is_an_error_not_an_empty_library(sessions, settings):
    shutil.rmtree(settings.library_dir)

    async with sessions() as session:
        result = await scan_library(session, settings)

    assert "not found" in result.errors[0]


async def test_scan_reports_progress(sessions, settings):
    write_photo(settings, "a.jpg", (1, 2, 3))
    write_photo(settings, "b.jpg", (4, 5, 6))
    seen: list[tuple[int, int]] = []

    async with sessions() as session:
        await scan_library(session, settings, lambda done, total: seen.append((done, total)))

    assert seen == [(1, 2), (2, 2)]


async def test_an_output_copied_into_the_library_stays_an_output(sessions, settings):
    path = write_photo(settings, "output.jpg")
    facts = ingest_file(path, settings.data_dir)
    async with sessions() as session:
        photo = Photo(path="outputs/x.jpg", origin=ORIGIN_GENERATED, people=[])
        library.apply_facts(photo, facts)
        session.add(photo)
        await session.commit()

    async with sessions() as session:
        result = await scan_library(session, settings)
        photos = await all_photos(session)

    assert result.duplicates == 1
    assert [(photo.origin, photo.path) for photo in photos] == [(ORIGIN_GENERATED, "outputs/x.jpg")]


def test_photo_file_resolves_by_origin(settings):
    assert (
        library.photo_file(Photo(path="a.jpg", origin="library"), settings)
        == settings.library_dir / "a.jpg"
    )
    assert (
        library.photo_file(Photo(path="stock/a.jpg", origin="stock"), settings)
        == settings.data_dir / "stock/a.jpg"
    )


def test_thumbnails_never_touch_the_library(settings):
    path = write_photo(settings)
    before = sorted(settings.library_dir.rglob("*"))

    ingest_file(path, settings.data_dir)

    assert sorted(settings.library_dir.rglob("*")) == before
    with Image.open(path) as image:
        assert image.size == (64, 48)
