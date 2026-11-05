import json

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from photolab.agents import CliResult
from photolab.annotate import PhotoAnnotation, store_annotation
from photolab.api.app import build_app, create_app, make_anthropic
from photolab.library import ingest_file, scan_library
from photolab.models import Generation, Photo
from tests.conftest import (
    ANNOTATION,
    FakeCli,
    codex_events,
    codex_failure,
    jpeg_bytes,
    write_photo,
)


async def seed(context, count: int = 1, annotate: bool = True) -> list[int]:
    for index in range(count):
        write_photo(context.settings, f"{index}.jpg", (index * 30, 50, 90))
    async with context.sessions() as session:
        await scan_library(session, context.settings)
        rows = list((await session.scalars(select(Photo).order_by(Photo.id))).unique())
        if annotate:
            for photo in rows:
                store_annotation(photo, PhotoAnnotation.model_validate(ANNOTATION), "m")
        await session.commit()
        return [photo.id for photo in rows]


async def test_stats_counts_the_album(client, context):
    await seed(context, count=2)

    body = (await client.get("/api/stats")).json()

    assert body["photos"] == 2
    assert body["annotated"] == 2
    assert body["to_review"] == 2
    assert body["annotation_ready"] is True
    assert body["stock_ready"] is False


async def test_list_filters_by_search_and_review(client, context):
    first, second = await seed(context, count=2)
    await client.patch(f"/api/photos/{first}", json={"review_status": "approved"})

    approved = (await client.get("/api/photos", params={"review": "approved"})).json()
    found = (await client.get("/api/photos", params={"q": "sunset beach"})).json()
    missed = (await client.get("/api/photos", params={"q": "sunset mountain"})).json()

    assert [item["id"] for item in approved["items"]] == [first]
    assert found["total"] == 2
    assert missed["total"] == 0
    assert {item["caption"] for item in found["items"]} == {ANNOTATION["caption"]}
    assert second


async def test_search_treats_like_wildcards_as_text(client, context):
    await seed(context)

    assert (await client.get("/api/photos", params={"q": "%"})).json()["total"] == 0


async def test_list_filters_by_annotation_state(client, context):
    await seed(context, annotate=False)

    missing = (await client.get("/api/photos", params={"annotated": False})).json()
    done = (await client.get("/api/photos", params={"annotated": True})).json()

    assert missing["total"] == 1
    assert done["total"] == 0


async def test_the_review_shelf_lists_exactly_what_its_count_says(client, context):
    # The shelf is the audit queue, and an output never has an annotation: it has to be
    # included on review status alone, while a photo awaiting the model has nothing to audit.
    photo_id = (await seed(context))[0]
    await seed(context, count=2, annotate=False)
    await client.post("/api/generations", json={"kind": "enhance", "photo_id": photo_id})
    await context.board.wait()

    stats = (await client.get("/api/stats")).json()
    shelf = (await client.get("/api/photos", params={"reviewable": True})).json()

    assert stats["to_review"] == 2
    assert shelf["total"] == 2
    assert {item["origin"] for item in shelf["items"]} == {"library", "generated"}


async def test_the_unannotated_shelf_leaves_outputs_out(client, context):
    photo_id = (await seed(context))[0]
    await client.post("/api/generations", json={"kind": "enhance", "photo_id": photo_id})
    await context.board.wait()

    stats = (await client.get("/api/stats")).json()
    shelf = (await client.get("/api/photos", params={"annotated": False})).json()

    assert stats["unannotated"] == shelf["total"] == 0


async def test_list_pages(client, context):
    await seed(context, count=3)

    page = (await client.get("/api/photos", params={"limit": 2, "offset": 2})).json()

    assert page["total"] == 3
    assert len(page["items"]) == 1


async def test_detail_shows_the_annotation_and_a_404_for_nothing(client, context):
    photo_id = (await seed(context))[0]

    detail = (await client.get(f"/api/photos/{photo_id}")).json()

    assert detail["annotation"]["data"]["scene"] == "beach"
    assert detail["made_by"] is None
    assert (await client.get("/api/photos/999")).status_code == 404


async def test_an_edit_keeps_what_the_model_said(client, context):
    photo_id = (await seed(context))[0]

    response = await client.patch(
        f"/api/photos/{photo_id}",
        json={"annotation": {"caption": "Me at Praia do Forte"}},
    )

    annotation = response.json()["annotation"]
    assert annotation["data"]["caption"] == "Me at Praia do Forte"
    assert annotation["original"]["caption"] == ANNOTATION["caption"]
    found = await client.get("/api/photos", params={"q": "praia"})
    assert found.json()["total"] == 1


async def test_an_edit_must_stay_valid(client, context):
    photo_id = (await seed(context))[0]

    response = await client.patch(
        f"/api/photos/{photo_id}", json={"annotation": {"setting": "underwater"}}
    )

    assert response.status_code == 422


async def test_correcting_a_missing_annotation_is_a_conflict(client, context):
    photo_id = (await seed(context, annotate=False))[0]

    response = await client.patch(f"/api/photos/{photo_id}", json={"annotation": {"caption": "x"}})

    assert response.status_code == 409


async def test_people_can_be_named_before_any_annotation_exists(client, context):
    photo_id = (await seed(context, annotate=False))[0]

    response = await client.patch(
        f"/api/photos/{photo_id}", json={"people": ["  Alex ", "Alex", "", "Sam"]}
    )

    assert response.json()["people"] == ["Alex", "Sam"]
    found = await client.get("/api/photos", params={"q": "alex"})
    assert found.json()["total"] == 1


async def test_catalog_lists_what_is_known_and_drops_the_rejected(client, context):
    first, second = await seed(context, count=2)
    await client.patch(f"/api/photos/{first}", json={"review_status": "approved"})
    await client.patch(f"/api/photos/{second}", json={"review_status": "rejected"})

    text = (await client.get("/api/catalog")).text

    lines = text.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith(f"#{first}\t")
    assert "verified" in lines[0]
    assert "tags=beach,sunset" in lines[0]


async def test_catalog_never_lists_an_unannotated_photo(client, context):
    await seed(context, annotate=False)

    assert (await client.get("/api/catalog")).text == ""


async def test_thumbnail_and_image_are_served(client, context):
    photo_id = (await seed(context))[0]

    thumb = await client.get(f"/api/photos/{photo_id}/thumb")
    image = await client.get(f"/api/photos/{photo_id}/image")

    assert thumb.headers["content-type"] == "image/webp"
    assert image.status_code == 200
    assert image.content == jpeg_bytes((0, 50, 90))


async def test_a_vanished_file_is_a_404_not_a_500(client, context):
    photo_id = (await seed(context))[0]
    (context.settings.library_dir / "0.jpg").unlink()

    assert (await client.get(f"/api/photos/{photo_id}/image")).status_code == 404


async def test_a_format_browsers_cannot_show_is_transcoded(client, context):
    path = context.settings.library_dir / "scan.tif"
    Image.new("RGB", (16, 16), (10, 20, 30)).save(path)
    async with context.sessions() as session:
        await scan_library(session, context.settings)
        photo_id = await session.scalar(select(Photo.id))

    response = await client.get(f"/api/photos/{photo_id}/image")

    assert response.headers["content-type"] == "image/jpeg"


async def test_scan_runs_in_the_background(client, context):
    write_photo(context.settings)

    response = await client.post("/api/library/scan")
    await context.board.wait()

    assert response.status_code == 202
    stats = (await client.get("/api/stats")).json()
    assert stats["photos"] == 1
    assert stats["jobs"][0]["message"] == "1 new, 0 moved, 0 changed"


async def test_annotation_run_reaches_the_client(client, context, anthropic_client):
    await seed(context, annotate=False)

    response = await client.post("/api/annotations/run", json={"limit": 5})
    await context.board.wait()

    assert response.status_code == 202
    assert len(anthropic_client.calls) == 1
    stats = (await client.get("/api/stats")).json()
    assert stats["jobs"][0]["message"] == "1 annotated, 0 failed"


async def test_annotation_refuses_to_start_with_nothing_to_use(client, context, tmp_path):
    context.anthropic = None
    context.settings.anthropic_api_key = ""
    context.clis = FakeCli(tmp_path, ()).clis()

    response = await client.post("/api/annotations/run", json={})

    assert response.status_code == 409
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]


async def test_a_run_can_pick_another_backend(client, context, fake_cli):
    await seed(context, annotate=False)
    fake_cli.result = CliResult(0, json.dumps({"is_error": False, "structured_output": ANNOTATION}))

    response = await client.post(
        "/api/annotations/run", json={"backend": "claude", "model": "sonnet"}
    )
    await context.board.wait()

    assert response.status_code == 202
    assert fake_cli.calls[0][0] == "claude"
    assert "sonnet" in fake_cli.calls[0]


async def test_stats_says_what_can_run_here(client, context, tmp_path):
    body = (await client.get("/api/stats")).json()
    assert body["annotation_backends"] == ["codex", "claude", "api"]
    assert body["annotation_backend"] == "api"
    assert body["annotation_model"] == "test-model"
    assert body["image_ready"] is True

    context.clis = FakeCli(tmp_path, ()).clis()
    body = (await client.get("/api/stats")).json()
    assert body["annotation_backends"] == ["api"]
    assert body["image_ready"] is False


async def test_suggest_answers_from_the_catalog(client, context, fake_cli):
    [photo_id] = await seed(context)
    context.settings.annotation_backend = "codex"
    answer = json.dumps({"reference_ids": [photo_id], "why": "the only one"})
    fake_cli.result = CliResult(0, codex_events(answer))

    response = await client.post("/api/ai/suggest", json={"prompt": "a beach"})

    assert response.json() == {"reference_ids": [photo_id], "why": "the only one"}


async def test_suggest_needs_a_command_line(client, context, tmp_path):
    await seed(context)
    context.clis = FakeCli(tmp_path, ()).clis()

    response = await client.post("/api/ai/suggest", json={"prompt": "a beach"})

    assert response.status_code == 409


async def test_suggest_reports_a_plan_limit_as_a_conflict(client, context, fake_cli):
    await seed(context)
    context.settings.annotation_backend = "codex"
    fake_cli.result = CliResult(1, codex_failure("You've hit your usage limit"))

    response = await client.post("/api/ai/suggest", json={"prompt": "a beach"})

    assert response.status_code == 409
    assert "usage limit" in response.json()["detail"]


async def test_suggest_reports_a_bad_answer_as_a_bad_gateway(client, context, fake_cli):
    await seed(context)
    context.settings.annotation_backend = "codex"
    fake_cli.result = CliResult(0, codex_events("no idea"))

    response = await client.post("/api/ai/suggest", json={"prompt": "a beach"})

    assert response.status_code == 502


async def test_an_ai_image_runs_end_to_end(client, context, fake_cli):
    [photo_id] = await seed(context)
    fake_cli.make_image = True

    response = await client.post(
        "/api/generations", json={"kind": "ai", "prompt": "a beach", "reference_ids": [photo_id]}
    )
    await context.board.wait()

    assert response.status_code == 202
    assert response.json()["subject_id"] == photo_id
    [run] = (await client.get("/api/generations")).json()
    assert run["status"] == "done"
    detail = (await client.get(f"/api/photos/{run['output_id']}")).json()
    assert detail["origin"] == "generated"
    assert detail["made_by"]["params"]["prompt"] == "a beach"


async def test_an_image_from_words_alone_needs_no_photo(client, context, fake_cli):
    fake_cli.make_image = True

    response = await client.post("/api/generations", json={"kind": "ai", "prompt": "a pier"})
    await context.board.wait()

    assert response.status_code == 202
    assert response.json()["subject_id"] is None
    assert (await client.get("/api/generations")).json()[0]["status"] == "done"


async def test_an_ai_image_refuses_a_photo_that_is_not_there(client):
    response = await client.post(
        "/api/generations", json={"kind": "ai", "prompt": "x", "reference_ids": [424242]}
    )

    assert response.status_code == 404


async def test_ai_images_say_how_to_get_them_in_docker(client, context, tmp_path):
    context.clis = FakeCli(tmp_path, ()).clis()

    response = await client.post("/api/generations", json={"kind": "ai", "prompt": "x"})

    assert response.status_code == 409
    assert "scripts/album.py" in response.json()["detail"]


async def test_a_second_scan_while_one_runs_is_a_conflict(client, context):
    write_photo(context.settings)

    first = await client.post("/api/library/scan")
    second = await client.post("/api/library/scan")
    await context.board.wait()

    assert first.status_code == 202
    assert second.status_code == 409


async def test_stock_routes_explain_a_missing_key(client):
    assert (await client.get("/api/stock/search", params={"q": "x"})).status_code == 409
    assert (await client.post("/api/stock/import", json={"pexels_id": 1})).status_code == 409


PEXELS_ITEM = {
    "id": 42,
    "width": 64,
    "height": 48,
    "alt": "beach",
    "photographer": "Ana",
    "photographer_url": "https://www.pexels.com/@ana",
    "url": "https://www.pexels.com/photo/42/",
    "src": {
        "medium": "https://images.pexels.com/photos/42/m.jpeg",
        "original": "https://images.pexels.com/photos/42/o.jpeg",
    },
}


def pexels(request: httpx.Request) -> httpx.Response:
    if request.url.host == "images.pexels.com":
        return httpx.Response(200, content=jpeg_bytes((10, 120, 200)))
    if request.url.path.endswith("/search"):
        return httpx.Response(200, json={"photos": [PEXELS_ITEM]})
    return httpx.Response(200, json=PEXELS_ITEM)


@pytest.fixture
def stock_transport() -> httpx.MockTransport:
    return httpx.MockTransport(pexels)


async def test_stock_search_and_import(client, context):
    context.settings.pexels_api_key = "key"  # pragma: allowlist secret

    found = (await client.get("/api/stock/search", params={"q": "beach"})).json()
    imported = await client.post("/api/stock/import", json={"pexels_id": found[0]["id"]})
    again = await client.post("/api/stock/import", json={"pexels_id": found[0]["id"]})

    assert imported.status_code == 201
    assert imported.json()["origin"] == "stock"
    assert again.json()["id"] == imported.json()["id"]
    detail = (await client.get(f"/api/photos/{imported.json()['id']}")).json()
    assert detail["source"]["photographer"] == "Ana"


async def test_compose_advice_reads_the_annotations(client, context):
    subject, background = await seed(context, count=2)
    async with context.sessions() as session:
        scene = await session.get(Photo, background)
        scene.annotation.data = {**scene.annotation.data, "light_direction": "right"}
        await session.commit()

    advice = await client.get(
        "/api/compose/advice", params={"subject_id": subject, "background_id": background}
    )

    assert advice.json()["flip"] is True
    assert advice.json()["light"] == "right"


async def test_compose_advice_for_a_missing_photo(client):
    response = await client.get("/api/compose/advice", params={"subject_id": 1, "background_id": 2})

    assert response.status_code == 404


async def test_generation_runs_and_lands_in_the_album(client, context):
    photo_id = (await seed(context))[0]

    response = await client.post(
        "/api/generations", json={"kind": "enhance", "photo_id": photo_id, "recipe": "natural"}
    )
    await context.board.wait()

    assert response.status_code == 202
    rows = (await client.get("/api/generations")).json()
    assert rows[0]["status"] == "done"
    output_id = rows[0]["output_id"]
    detail = (await client.get(f"/api/photos/{output_id}")).json()
    assert detail["origin"] == "generated"
    assert detail["made_by"]["subject_id"] == photo_id
    outputs = (await client.get("/api/photos", params={"origin": "generated"})).json()
    assert outputs["total"] == 1


async def test_generation_rejects_an_unknown_photo_and_a_bad_recipe(client, context):
    missing = await client.post("/api/generations", json={"kind": "enhance", "photo_id": 999})
    photo_id = (await seed(context))[0]
    bad = await client.post(
        "/api/generations", json={"kind": "enhance", "photo_id": photo_id, "recipe": "instagram"}
    )

    assert missing.status_code == 404
    assert bad.status_code == 422
    async with context.sessions() as session:
        assert await session.scalar(select(Generation.id)) is None


async def test_lifespan_closes_out_stranded_generations(context):
    photo_path = write_photo(context.settings)
    facts = ingest_file(photo_path, context.settings.data_dir)
    async with context.sessions() as session:
        photo = Photo(path="one.jpg", people=[])
        from photolab.library import apply_facts

        apply_facts(photo, facts)
        session.add(photo)
        await session.flush()
        session.add(Generation(kind="enhance", subject_id=photo.id, status="running"))
        await session.commit()

    app = build_app(context)
    async with app.router.lifespan_context(app):
        pass

    async with context.sessions() as session:
        assert (await session.scalar(select(Generation.status))) == "failed"


def test_make_anthropic_needs_a_key(settings):
    settings.anthropic_api_key = ""
    assert make_anthropic(settings) is None
    settings.anthropic_api_key = "sk-test"  # pragma: allowlist secret
    assert make_anthropic(settings) is not None


def test_create_app_wires_the_context(settings):
    app = create_app(settings)

    assert app.state.context.settings is settings
