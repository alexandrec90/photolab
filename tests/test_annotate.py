import base64
import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from sqlalchemy import select

from photolab.agents import CliResult
from photolab.annotate import (
    PROMPT_VERSION,
    AnnotateResult,
    AnnotationAborted,
    AnnotationFailed,
    Annotator,
    ApiAnnotator,
    CliAnnotator,
    NoAnnotator,
    PhotoAnnotation,
    annotate_jpeg,
    annotate_pending,
    build_request,
    make_annotator,
    parse_answer,
    parse_response,
    pending_ids,
    store_annotation,
)
from photolab.library import scan_library
from photolab.models import ORIGIN_GENERATED, Photo
from tests.conftest import (
    ANNOTATION,
    FakeAnthropic,
    FakeCli,
    codex_events,
    codex_failure,
    fake_response,
    write_photo,
)

ANSWER = PhotoAnnotation(**ANNOTATION).model_dump_json()


def api(client) -> ApiAnnotator:
    return ApiAnnotator(client, "test-model")


def status_error(kind: type[anthropic.APIStatusError], status: int) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return kind("nope", response=httpx2.Response(status, request=request), body=None)


async def scanned(sessions, settings, count: int = 1) -> None:
    for index in range(count):
        write_photo(settings, f"{index}.jpg", (index * 20, 40, 60))
    async with sessions() as session:
        await scan_library(session, settings)


async def stored(sessions) -> list[Photo]:
    async with sessions() as session:
        return list((await session.scalars(select(Photo).order_by(Photo.id))).unique())


def test_build_request_sends_the_image_and_constrains_the_answer():
    request = build_request(b"jpeg", "claude-opus-5-5")

    image = request["messages"][0]["content"][0]
    assert base64.standard_b64decode(image["source"]["data"]) == b"jpeg"
    assert image["source"]["media_type"] == "image/jpeg"
    schema = request["output_config"]["format"]["schema"]
    assert schema["additionalProperties"] is False
    assert "caption" in schema["required"]
    assert request["fallbacks"] == "default"


def test_the_model_is_not_asked_who_anyone_is():
    assert "people" not in PhotoAnnotation.model_fields
    assert "Never name or identify anyone" in build_request(b"x", "m")["system"]


def test_parse_response_reads_a_complete_annotation():
    assert parse_response(fake_response(PhotoAnnotation(**ANNOTATION).model_dump_json())).scene == (
        "beach"
    )


@pytest.mark.parametrize(
    ("stop_reason", "message"),
    [("refusal", "declined"), ("max_tokens", "cut off")],
)
def test_parse_response_rejects_incomplete_stops(stop_reason, message):
    with pytest.raises(AnnotationFailed, match=message):
        parse_response(fake_response("{}", stop_reason))


def test_parse_response_rejects_a_schema_mismatch():
    with pytest.raises(AnnotationFailed, match="schema"):
        parse_response(fake_response('{"caption": "only this"}'))


def test_parse_response_skips_non_text_blocks():
    response = SimpleNamespace(
        stop_reason="end_turn",
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=PhotoAnnotation(**ANNOTATION).model_dump_json()),
        ],
    )

    assert parse_response(response).person_count == 1


async def test_annotate_jpeg_sends_one_request():
    client = FakeAnthropic()

    annotation = await annotate_jpeg(client, b"jpeg", "m")

    assert annotation.caption == ANNOTATION["caption"]
    assert len(client.calls) == 1


def test_an_abort_is_distinct_from_a_per_photo_failure():
    # The run loop relies on this: one stops everything, the other stops one photo.
    assert not issubclass(AnnotationAborted, AnnotationFailed)


async def test_annotate_pending_stores_results(sessions, settings, anthropic_client):
    await scanned(sessions, settings)

    result = await annotate_pending(api(anthropic_client), sessions, settings)

    photo = (await stored(sessions))[0]
    assert isinstance(result, AnnotateResult)
    assert result.annotated == 1
    assert photo.annotation.data["scene"] == "beach"
    assert photo.annotation.original == photo.annotation.data
    assert photo.annotation.prompt_version == PROMPT_VERSION
    assert photo.annotation.model == "test-model"
    assert "sunset" in photo.search_text


async def test_a_second_run_costs_nothing(sessions, settings, anthropic_client):
    await scanned(sessions, settings, count=2)
    await annotate_pending(api(anthropic_client), sessions, settings)
    assert len(anthropic_client.calls) == 2

    result = await annotate_pending(api(anthropic_client), sessions, settings)

    assert result.annotated == 0
    assert len(anthropic_client.calls) == 2


async def test_limit_caps_a_run(sessions, settings, anthropic_client):
    await scanned(sessions, settings, count=3)

    result = await annotate_pending(api(anthropic_client), sessions, settings, limit=2)

    assert result.annotated == 2


async def test_a_refusal_is_recorded_and_skipped_next_time(sessions, settings):
    await scanned(sessions, settings)
    client = FakeAnthropic(fake_response("", "refusal"))

    result = await annotate_pending(api(client), sessions, settings)

    assert result.failed == 1
    assert "declined" in (await stored(sessions))[0].annotation_error
    await annotate_pending(api(client), sessions, settings)
    assert len(client.calls) == 1


async def test_retry_failed_tries_again_and_clears_the_error(sessions, settings):
    await scanned(sessions, settings)
    await annotate_pending(api(FakeAnthropic(fake_response("", "refusal"))), sessions, settings)

    result = await annotate_pending(api(FakeAnthropic()), sessions, settings, retry_failed=True)

    assert result.annotated == 1
    assert (await stored(sessions))[0].annotation_error is None


@pytest.mark.parametrize(
    ("kind", "status"),
    [
        (anthropic.AuthenticationError, 401),
        (anthropic.PermissionDeniedError, 403),
        (anthropic.NotFoundError, 404),
        (anthropic.RateLimitError, 429),
    ],
)
async def test_run_wide_errors_abort_without_marking_photos(sessions, settings, kind, status):
    await scanned(sessions, settings, count=2)
    client = FakeAnthropic(status_error(kind, status))

    result = await annotate_pending(api(client), sessions, settings)

    assert result.aborted
    assert result.annotated == 0
    # Nothing is wrong with the photos, so the next run must still try them.
    assert all(photo.annotation_error is None for photo in await stored(sessions))


async def test_a_server_error_fails_only_that_photo(sessions, settings):
    await scanned(sessions, settings)

    result = await annotate_pending(
        api(FakeAnthropic(status_error(anthropic.InternalServerError, 500))), sessions, settings
    )

    assert result.failed == 1
    assert not result.aborted


async def test_a_vanished_file_fails_only_that_photo(sessions, settings, anthropic_client):
    await scanned(sessions, settings)
    (settings.library_dir / "0.jpg").unlink()

    result = await annotate_pending(api(anthropic_client), sessions, settings)

    assert result.failed == 1
    assert (await stored(sessions))[0].annotation_error


async def test_a_rejected_annotation_is_redone(sessions, settings, anthropic_client):
    await scanned(sessions, settings)
    await annotate_pending(api(anthropic_client), sessions, settings)
    async with sessions() as session:
        photo = (await session.scalars(select(Photo))).unique().one()
        photo.review_status = "rejected"
        await session.commit()

    await annotate_pending(api(anthropic_client), sessions, settings)

    assert (await stored(sessions))[0].review_status == "pending"
    assert len(anthropic_client.calls) == 2


async def test_an_approved_annotation_is_left_alone(sessions, settings, anthropic_client):
    await scanned(sessions, settings)
    await annotate_pending(api(anthropic_client), sessions, settings)
    async with sessions() as session:
        photo = (await session.scalars(select(Photo))).unique().one()
        photo.review_status = "approved"
        await session.commit()

    assert await pending_ids(sessions, None) == []


async def test_generated_photos_are_never_sent(sessions, settings):
    await scanned(sessions, settings)
    async with sessions() as session:
        photo = (await session.scalars(select(Photo))).unique().one()
        photo.origin = ORIGIN_GENERATED
        await session.commit()

    assert await pending_ids(sessions, None) == []


def test_store_annotation_overwrites_both_copies_when_redone():
    photo = Photo(path="a.jpg", people=["Alex"])
    first = PhotoAnnotation.model_validate(ANNOTATION)
    store_annotation(photo, first, "m1")
    photo.annotation.data = {**photo.annotation.data, "caption": "edited"}

    store_annotation(photo, first.model_copy(update={"caption": "new"}), "m2")

    assert photo.annotation.data["caption"] == "new"
    assert photo.annotation.original["caption"] == "new"
    assert photo.annotation.model == "m2"
    assert photo.people == ["Alex"]


def test_parse_answer_refuses_text_that_is_not_an_annotation():
    with pytest.raises(AnnotationFailed, match="schema"):
        parse_answer("sure, here it is")


async def test_the_annotators_name_is_stored_on_the_annotation(sessions, settings, fake_cli):
    await scanned(sessions, settings)
    fake_cli.result = CliResult(0, codex_events(ANSWER))

    await annotate_pending(CliAnnotator("codex", "gpt-x", fake_cli.clis()), sessions, settings)

    assert (await stored(sessions))[0].annotation.model == "codex/gpt-x"


async def test_codex_annotates_a_photo(fake_cli):
    fake_cli.result = CliResult(0, codex_events(ANSWER))

    annotation = await CliAnnotator("codex", "gpt-x", fake_cli.clis())(b"jpeg")

    assert annotation.scene == "beach"
    assert fake_cli.calls[0][0] == "codex"


async def test_claude_annotates_a_photo(fake_cli):
    fake_cli.result = CliResult(
        0, json.dumps({"is_error": False, "structured_output": json.loads(ANSWER)})
    )

    annotation = await CliAnnotator("claude", "haiku", fake_cli.clis())(b"jpeg")

    assert annotation.scene == "beach"
    assert fake_cli.calls[0][0] == "claude"


async def test_a_plan_limit_stops_the_run_without_marking_photos(sessions, settings, fake_cli):
    await scanned(sessions, settings, count=2)
    fake_cli.result = CliResult(1, codex_failure("You've hit your usage limit"))

    result = await annotate_pending(
        CliAnnotator("codex", "gpt-x", fake_cli.clis()), sessions, settings
    )

    assert "usage limit" in result.aborted
    # The photos are fine, so the next run picks up where this one stopped.
    assert all(photo.annotation_error is None for photo in await stored(sessions))


async def test_one_bad_answer_fails_only_that_photo(sessions, settings, fake_cli):
    await scanned(sessions, settings)
    fake_cli.result = CliResult(0, codex_events("not json"))

    result = await annotate_pending(
        CliAnnotator("codex", "gpt-x", fake_cli.clis()), sessions, settings
    )

    assert result.failed == 1
    assert not result.aborted


async def test_every_annotator_has_the_same_shape(fake_cli):
    fake_cli.result = CliResult(0, codex_events(ANSWER))
    annotators: list[Annotator] = [
        api(FakeAnthropic()),
        CliAnnotator("codex", "gpt-x", fake_cli.clis()),
    ]

    names = [annotator.name for annotator in annotators]
    scenes = [(await annotator(b"jpeg")).scene for annotator in annotators]

    assert names == ["test-model", "codex/gpt-x"]
    assert scenes == ["beach", "beach"]


def test_make_annotator_defaults_to_the_configured_backend(settings, fake_cli):
    settings.annotation_backend, settings.annotation_model = "codex", ""

    annotator = make_annotator(settings, None, fake_cli.clis())

    assert annotator.name == "codex/gpt-6-luna"


def test_make_annotator_falls_back_to_haiku_without_codex(settings, tmp_path):
    settings.annotation_backend, settings.annotation_model = "codex", ""
    settings.anthropic_api_key = ""

    annotator = make_annotator(settings, None, FakeCli(tmp_path, ("claude",)).clis())

    assert annotator.name == "claude/haiku"


def test_make_annotator_takes_a_backend_and_model_per_run(settings, fake_cli):
    annotator = make_annotator(settings, None, fake_cli.clis(), "claude", "sonnet")

    assert annotator.name == "claude/sonnet"


def test_make_annotator_builds_the_api_one(settings, fake_cli):
    annotator = make_annotator(settings, FakeAnthropic(), fake_cli.clis(), "api")

    assert isinstance(annotator, ApiAnnotator)
    assert annotator.name == "test-model"


def test_make_annotator_refuses_what_is_not_here(settings, tmp_path):
    with pytest.raises(NoAnnotator, match="codex cannot annotate"):
        make_annotator(settings, None, FakeCli(tmp_path, ()).clis(), "codex")


def test_make_annotator_needs_a_client_for_the_api(settings, fake_cli):
    with pytest.raises(NoAnnotator, match="no client"):
        make_annotator(settings, None, fake_cli.clis(), "api")
