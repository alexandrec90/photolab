from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from photolab.api.schemas import (
    AdviceView,
    AnnotateStart,
    AnnotationView,
    GenerationView,
    PhotoDetail,
    PhotoEdit,
    PhotoFilters,
    PhotoPage,
    PhotoSummary,
    Stats,
    StockImport,
    SuggestRequest,
)
from photolab.models import Annotation, Generation, Photo
from tests.conftest import ANNOTATION

NOW = datetime(2024, 7, 1, tzinfo=UTC)


def photo(annotated: bool = True) -> Photo:
    row = Photo(
        id=1,
        origin="library",
        path="a.jpg",
        width=10,
        height=20,
        format="JPEG",
        people=[],
        exif={},
        source={},
        review_status="pending",
    )
    if annotated:
        row.annotation = Annotation(
            data=ANNOTATION, original=ANNOTATION, model="m", prompt_version=1, updated_at=NOW
        )
    return row


def test_photo_summary_lifts_the_caption():
    summary = PhotoSummary.of(photo())

    assert summary.caption == ANNOTATION["caption"]
    assert summary.annotated is True


def test_photo_summary_without_an_annotation():
    summary = PhotoSummary.of(photo(annotated=False))

    assert summary.caption is None
    assert summary.annotated is False


def test_photo_detail_carries_the_annotation_and_lineage():
    made_by = Generation(
        id=9,
        kind="enhance",
        params={},
        status="done",
        subject_id=1,
        created_at=NOW,
    )

    detail = PhotoDetail.build(photo(), made_by)

    assert isinstance(detail.annotation, AnnotationView)
    assert isinstance(detail.made_by, GenerationView)
    assert detail.made_by.id == 9


def test_photo_detail_tolerates_neither():
    detail = PhotoDetail.build(photo(annotated=False), None)

    assert detail.annotation is None
    assert detail.made_by is None


def test_photo_page_holds_summaries():
    page = PhotoPage(total=1, items=[PhotoSummary.of(photo())])

    assert page.items[0].id == 1


def test_photo_edit_refuses_an_unknown_review_status_and_field():
    with pytest.raises(ValidationError):
        PhotoEdit(review_status="maybe")
    with pytest.raises(ValidationError):
        PhotoEdit.model_validate({"sha256": "abc"})


def test_photo_filters_bound_the_page_size():
    assert PhotoFilters().limit == 60
    with pytest.raises(ValidationError):
        PhotoFilters(limit=1000)


def test_annotate_start_limit_must_be_positive():
    assert AnnotateStart().limit is None
    with pytest.raises(ValidationError):
        AnnotateStart(limit=0)


def test_annotate_start_knows_its_backends():
    assert AnnotateStart(backend="codex", model="gpt-x").backend == "codex"
    with pytest.raises(ValidationError):
        AnnotateStart(backend="gemini")


def test_suggest_request_needs_a_prompt():
    assert SuggestRequest(prompt="a beach").prompt == "a beach"
    with pytest.raises(ValidationError):
        SuggestRequest(prompt="")


def test_a_generation_from_words_alone_has_no_subject():
    made_by = Generation(id=9, kind="ai", params={}, status="done", created_at=NOW)

    assert GenerationView.model_validate(made_by).subject_id is None


def test_small_views_validate():
    assert AdviceView(light="left", flip=False, warnings=[]).light == "left"
    assert StockImport(pexels_id=4).pexels_id == 4
    stats = Stats(
        photos=1,
        outputs=0,
        annotated=0,
        unannotated=1,
        failed=0,
        to_review=0,
        approved=0,
        rejected=0,
        annotation_backends=[],
        annotation_backend="codex",
        annotation_model="m",
        annotation_ready=False,
        image_ready=False,
        stock_ready=False,
        jobs=[],
    )
    assert stats.unannotated == 1
