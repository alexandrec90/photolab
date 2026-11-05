import json

import pytest
from PIL import Image

from photolab import ai_images
from photolab.agents import AgentFailed, CliResult
from photolab.ai_images import Reference, Suggestion
from photolab.annotate import PhotoAnnotation, store_annotation
from photolab.library import scan_library
from photolab.models import Annotation, Photo
from tests.conftest import ANNOTATION, FakeCli, codex_events, write_photo


def annotated_photo(people: list[str] | None = None) -> Photo:
    photo = Photo(path="a.jpg", people=people or [])
    photo.annotation = Annotation(data=ANNOTATION, original=ANNOTATION, model="m", prompt_version=1)
    return photo


def test_describe_reference_uses_the_annotation_and_the_names():
    text = ai_images.describe_reference(annotated_photo(["Alex"]))

    assert "shows Alex" in text
    assert ANNOTATION["caption"] in text
    assert "light from left" in text


def test_describe_reference_copes_with_knowing_nothing():
    assert ai_images.describe_reference(Photo(path="a.jpg", people=[])) == "no notes"


def test_build_prompt_numbers_images_in_attachment_order():
    prompt = ai_images.build_prompt("a beach", "free", ["first", "second"])

    assert "a beach" in prompt
    assert prompt.index("Image 1: first.") < prompt.index("Image 2: second.")


def test_build_prompt_asks_for_a_real_photograph():
    prompt = ai_images.build_prompt("a beach", "free", [])

    assert "No HDR look" in prompt
    assert "image_gen" in prompt


def test_build_prompt_relight_protects_the_person():
    prompt = ai_images.build_prompt("", "relight", ["a composite"])

    assert "face" in prompt
    assert "exactly the same" in prompt
    assert "Image 1: a composite." in prompt


def test_render_strips_metadata_before_anything_leaves(settings, fake_cli):
    path = settings.library_dir / "gps.jpg"
    exif = Image.Exif()
    exif[0x010F] = "SecretCamera"
    Image.new("RGB", (64, 48), (1, 2, 3)).save(path, exif=exif)
    sent: list[bytes] = []
    real_run = fake_cli.run

    def spy(command, stdin, cwd):
        sent.append((cwd / "image-1.jpg").read_bytes())
        return real_run(command, stdin, cwd)

    fake_cli.run = spy
    fake_cli.make_image = True

    image = ai_images.render("draw", [Reference(path, "x")], fake_cli.clis(), "gpt-x")

    assert image.startswith(b"\x89PNG")
    assert b"SecretCamera" not in sent[0]


async def album(sessions, settings, count: int = 2) -> list[int]:
    for index in range(count):
        write_photo(settings, f"{index}.jpg", (index * 30, 50, 60))
    async with sessions() as session:
        await scan_library(session, settings)
    async with sessions() as session:
        from sqlalchemy import select

        photos = list((await session.scalars(select(Photo).order_by(Photo.id))).unique())
        for photo in photos:
            store_annotation(photo, PhotoAnnotation.model_validate(ANNOTATION), "m")
        await session.commit()
        return [photo.id for photo in photos]


async def test_suggest_reads_the_catalog_and_sends_no_image(sessions, settings, fake_cli):
    ids = await album(sessions, settings)
    settings.annotation_backend = "codex"
    answer = json.dumps({"reference_ids": [ids[1]], "why": "best light"})
    fake_cli.result = CliResult(0, codex_events(answer))

    suggestion = await ai_images.suggest("me on a beach", sessions, settings, fake_cli.clis())

    assert suggestion == Suggestion(reference_ids=[ids[1]], why="best light")
    assert "--image" not in fake_cli.calls[0]
    assert f"#{ids[0]}" in fake_cli.stdin[0]
    assert "me on a beach" in fake_cli.stdin[0]


async def test_suggest_drops_ids_the_model_made_up(sessions, settings, fake_cli):
    ids = await album(sessions, settings)
    settings.annotation_backend = "codex"
    answer = json.dumps({"reference_ids": [ids[0], 99999], "why": ""})
    fake_cli.result = CliResult(0, codex_events(answer))

    suggestion = await ai_images.suggest("x", sessions, settings, fake_cli.clis())

    assert suggestion.reference_ids == [ids[0]]


async def test_suggest_with_nothing_annotated_asks_no_model(sessions, settings, fake_cli):
    suggestion = await ai_images.suggest("x", sessions, settings, fake_cli.clis())

    assert suggestion.reference_ids == []
    assert fake_cli.calls == []


async def test_suggest_uses_claude_when_it_is_the_one_configured(sessions, settings, fake_cli):
    await album(sessions, settings)
    settings.annotation_backend = "claude"
    answer = {"reference_ids": [], "why": "none fit"}
    fake_cli.result = CliResult(0, json.dumps({"is_error": False, "structured_output": answer}))

    suggestion = await ai_images.suggest("x", sessions, settings, fake_cli.clis())

    assert suggestion.why == "none fit"
    assert fake_cli.calls[0][0] == "claude"


async def test_suggest_cannot_use_the_api_backend(sessions, settings, tmp_path):
    await album(sessions, settings)

    with pytest.raises(ai_images.NoSuggester):
        await ai_images.suggest("x", sessions, settings, FakeCli(tmp_path, ()).clis())


async def test_suggest_refuses_an_answer_of_the_wrong_shape(sessions, settings, fake_cli):
    await album(sessions, settings)
    settings.annotation_backend = "codex"
    fake_cli.result = CliResult(0, codex_events('{"reference_ids": "all"}'))

    with pytest.raises(AgentFailed, match="schema"):
        await ai_images.suggest("x", sessions, settings, fake_cli.clis())
