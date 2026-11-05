import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError
from sqlalchemy import select

from photolab import generate, matting
from photolab.agents import CliResult
from photolab.generate import AiRequest, CompositeRequest, EnhanceRequest
from photolab.library import scan_library
from photolab.models import ORIGIN_GENERATED, ORIGIN_STOCK, Generation, Photo
from tests.conftest import codex_events, codex_failure, jpeg_bytes, write_photo


def write_gradient(settings, name: str, size=(320, 240)) -> None:
    rng = np.random.default_rng(1)
    pixels = rng.integers(40, 200, (size[1], size[0], 3), dtype=np.uint8)
    Image.fromarray(pixels).save(settings.library_dir / name, quality=95)


@pytest.fixture
def fake_matte(monkeypatch):
    def model(image: Image.Image, name: str) -> Image.Image:
        mask = Image.new("L", image.size, 0)
        width, height = image.size
        mask.paste(255, (width // 4, height // 8, width * 3 // 4, height * 7 // 8))
        return mask

    monkeypatch.setattr(matting, "run_model", model)


async def scanned_ids(sessions, settings) -> list[int]:
    async with sessions() as session:
        await scan_library(session, settings)
        return list(await session.scalars(select(Photo.id).order_by(Photo.id)))


def test_queue_generation_for_each_kind():
    enhance = generate.queue_generation(EnhanceRequest(photo_id=3))
    both = generate.queue_generation(CompositeRequest(subject_id=1, background_id=2))

    assert (enhance.subject_id, enhance.background_id, enhance.status) == (3, None, "queued")
    assert (both.subject_id, both.background_id) == (1, 2)


def test_request_from_row_round_trips_the_recipe():
    request = CompositeRequest(subject_id=1, background_id=2, light="left", height=0.4)

    assert generate.request_from_row(generate.queue_generation(request)) == request


def test_enhance_and_composite_render_jpegs(settings, fake_matte):
    write_gradient(settings, "subject.jpg")
    write_gradient(settings, "scene.jpg", (640, 480))
    subject = settings.library_dir / "subject.jpg"

    enhanced = generate.render_enhance(EnhanceRequest(photo_id=1, recipe="film"), subject)
    composited = generate.render_composite(
        CompositeRequest(subject_id=1, background_id=2),
        subject,
        settings.library_dir / "scene.jpg",
        settings.data_dir / "matte.png",
        "m",
    )

    assert enhanced[:2] == b"\xff\xd8"
    assert composited[:2] == b"\xff\xd8"


async def test_run_generation_records_an_output_with_lineage(sessions, settings):
    write_gradient(settings, "a.jpg")
    photo_id = (await scanned_ids(sessions, settings))[0]
    async with sessions() as session:
        row = generate.queue_generation(EnhanceRequest(photo_id=photo_id))
        session.add(row)
        await session.commit()

    await generate.run_generation(row.id, sessions, settings)

    async with sessions() as session:
        done = await session.get(Generation, row.id)
        output = await session.get(Photo, done.output_id)
    assert done.status == "done"
    assert done.finished_at is not None
    assert output.origin == ORIGIN_GENERATED
    assert output.review_status == "pending"
    assert (settings.data_dir / output.path).exists()


async def test_run_generation_records_a_failure_rather_than_raising(sessions, settings):
    write_gradient(settings, "a.jpg")
    photo_id = (await scanned_ids(sessions, settings))[0]
    (settings.library_dir / "a.jpg").unlink()
    async with sessions() as session:
        row = generate.queue_generation(EnhanceRequest(photo_id=photo_id))
        session.add(row)
        await session.commit()

    await generate.run_generation(row.id, sessions, settings)

    async with sessions() as session:
        failed = await session.get(Generation, row.id)
    assert failed.status == "failed"
    assert failed.error
    assert failed.output_id is None


async def test_run_generation_of_a_missing_row_is_quiet(sessions, settings):
    await generate.run_generation(999, sessions, settings)


async def test_an_empty_matte_fails_with_the_reason(sessions, settings, monkeypatch):
    monkeypatch.setattr(matting, "run_model", lambda image, _: Image.new("L", image.size, 0))
    write_gradient(settings, "a.jpg")
    write_gradient(settings, "b.jpg", (300, 200))
    subject_id, background_id = await scanned_ids(sessions, settings)
    async with sessions() as session:
        row = generate.queue_generation(
            CompositeRequest(subject_id=subject_id, background_id=background_id)
        )
        session.add(row)
        await session.commit()

    await generate.run_generation(row.id, sessions, settings)

    async with sessions() as session:
        assert "no subject" in (await session.get(Generation, row.id)).error


async def test_fail_interrupted_closes_out_stranded_jobs(sessions, settings):
    write_photo(settings)
    photo_id = (await scanned_ids(sessions, settings))[0]
    async with sessions() as session:
        queued = generate.queue_generation(EnhanceRequest(photo_id=photo_id))
        running = generate.queue_generation(EnhanceRequest(photo_id=photo_id))
        running.status = "running"
        finished = generate.queue_generation(EnhanceRequest(photo_id=photo_id))
        finished.status = "done"
        session.add_all([queued, running, finished])
        await session.commit()

    assert await generate.fail_interrupted(sessions) == 2

    async with sessions() as session:
        rows = list(await session.scalars(select(Generation).order_by(Generation.id)))
    assert [row.status for row in rows] == ["failed", "failed", "done"]
    assert rows[0].error == "interrupted by a restart"


def test_store_stock_saves_by_hash_with_attribution(settings):
    photo = generate.store_stock(jpeg_bytes(), {"id": 7, "photographer": "Ana"}, settings)

    assert photo.origin == ORIGIN_STOCK
    assert photo.source["photographer"] == "Ana"
    assert (settings.data_dir / photo.path).exists()
    assert not list((settings.data_dir / "stock").glob("*.tmp"))


def test_file_helpers_stay_under_the_data_dir(settings):
    assert generate.output_file(settings.data_dir, 5).parent.name == "outputs"
    assert generate.output_file(settings.data_dir, 5, ".png").suffix == ".png"
    assert generate.stock_file(settings.data_dir, "abc").parent.name == "stock"


def test_an_ai_request_needs_words_unless_it_is_a_relight():
    with pytest.raises(ValidationError, match="say what to make"):
        AiRequest(prompt="   ")
    assert AiRequest(preset="relight", reference_ids=[1]).prompt == ""


def test_a_relight_takes_exactly_one_photo():
    with pytest.raises(ValidationError, match="exactly one"):
        AiRequest(preset="relight", reference_ids=[1, 2])


def test_an_ai_request_caps_its_references():
    with pytest.raises(ValidationError):
        AiRequest(prompt="x", reference_ids=list(range(7)))


def test_request_inputs_lists_every_photo():
    assert generate.request_inputs(EnhanceRequest(photo_id=3)) == [3]
    assert generate.request_inputs(CompositeRequest(subject_id=1, background_id=2)) == [1, 2]
    assert generate.request_inputs(AiRequest(prompt="x", reference_ids=[4, 5, 6])) == [4, 5, 6]


def test_an_image_from_words_alone_has_no_input_columns():
    row = generate.queue_generation(AiRequest(prompt="a pier in fog"))

    assert (row.subject_id, row.background_id) == (None, None)


def test_an_ai_requests_columns_hold_its_first_two_references():
    row = generate.queue_generation(AiRequest(prompt="x", reference_ids=[4, 5, 6]))

    assert (row.subject_id, row.background_id) == (4, 5)
    assert row.params["reference_ids"] == [4, 5, 6]


def test_request_from_row_rebuilds_an_ai_request():
    row = generate.queue_generation(AiRequest(prompt="x", reference_ids=[4]))

    assert generate.request_from_row(row) == AiRequest(prompt="x", reference_ids=[4])


async def ai_row(sessions, request: AiRequest) -> int:
    async with sessions() as session:
        row = generate.queue_generation(request)
        session.add(row)
        await session.commit()
        return row.id


async def test_an_ai_image_is_kept_as_it_came_and_marked_generated(sessions, settings, fake_cli):
    fake_cli.make_image = True
    generation_id = await ai_row(sessions, AiRequest(prompt="a pier in fog"))

    await generate.run_generation(generation_id, sessions, settings, fake_cli.clis())

    async with sessions() as session:
        row = await session.get(Generation, generation_id)
        output = await session.get(Photo, row.output_id)
    assert row.status == "done"
    assert output.origin == ORIGIN_GENERATED
    assert output.path == f"outputs/generation-{generation_id}.png"
    assert fake_cli.calls[0][0] == "codex"


async def test_an_ai_image_is_briefed_from_what_the_album_knows(sessions, settings, fake_cli):
    fake_cli.make_image = True
    write_photo(settings)
    subject_id = (await scanned_ids(sessions, settings))[0]
    async with sessions() as session:
        photo = await session.get(Photo, subject_id)
        photo.people = ["Alex"]
        await session.commit()

    request = AiRequest(prompt="on a beach", reference_ids=[subject_id])
    generation_id = await ai_row(sessions, request)
    await generate.run_generation(generation_id, sessions, settings, fake_cli.clis())

    assert "Image 1: shows Alex." in fake_cli.stdin[0]
    assert fake_cli.files == [["image-1.jpg"]]


async def test_a_deleted_reference_fails_with_the_reason(sessions, settings, fake_cli):
    generation_id = await ai_row(sessions, AiRequest(prompt="x"))
    async with sessions() as session:
        row = await session.get(Generation, generation_id)
        row.params = {**row.params, "reference_ids": [424242]}
        await session.commit()

    await generate.run_generation(generation_id, sessions, settings, fake_cli.clis())

    async with sessions() as session:
        assert "424242" in (await session.get(Generation, generation_id)).error


async def test_a_refused_image_leaves_the_reason_on_the_row(sessions, settings, fake_cli):
    fake_cli.result = CliResult(0, codex_events("I can't make that."))
    generation_id = await ai_row(sessions, AiRequest(prompt="x"))

    await generate.run_generation(generation_id, sessions, settings, fake_cli.clis())

    async with sessions() as session:
        row = await session.get(Generation, generation_id)
    assert row.status == "failed"
    assert "can't make that" in row.error


async def test_a_plan_limit_fails_the_render_rather_than_the_server(sessions, settings, fake_cli):
    fake_cli.result = CliResult(1, codex_failure("You've hit your usage limit"))
    generation_id = await ai_row(sessions, AiRequest(prompt="x"))

    await generate.run_generation(generation_id, sessions, settings, fake_cli.clis())

    async with sessions() as session:
        row = await session.get(Generation, generation_id)
    assert row.status == "failed"
    assert "usage limit" in row.error
