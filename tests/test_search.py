from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from photolab import search
from photolab.models import Annotation, Photo, utcnow
from tests.conftest import ANNOTATION


def sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


def annotated_photo(**overrides) -> Photo:
    fields = {"id": 7, "path": "trips/Beach.JPG", "origin": "library", "people": ["Alex"]}
    photo = Photo(**(fields | overrides))
    photo.review_status = "pending"
    photo.annotation = Annotation(data=ANNOTATION, original=ANNOTATION, model="m", prompt_version=1)
    return photo


def test_utcnow_is_timezone_aware():
    assert utcnow().tzinfo is not None


def test_build_search_text_flattens_everything_searchable():
    text = search.build_search_text(annotated_photo())

    assert text == text.lower()
    for expected in ("trips/beach.jpg", "alex", "sunset", "red jacket", "golden_hour"):
        assert expected in text


def test_build_search_text_works_without_an_annotation():
    photo = Photo(path="a.jpg", people=[])

    assert search.build_search_text(photo) == "a.jpg"


def test_apply_query_requires_every_word():
    statement = search.apply_query(select(Photo), "red BEACH")

    assert sql(statement).count("search_text LIKE") == 2


def test_apply_query_without_words_adds_nothing():
    assert "WHERE" not in sql(search.apply_query(select(Photo), "   "))
    assert "WHERE" not in sql(search.apply_query(select(Photo), None))


def test_photos_statement_applies_each_filter():
    text = sql(search.photos_statement(origin="stock", review="approved", annotated=False))

    assert "photos.origin" in text
    assert "photos.review_status" in text
    assert "annotations.photo_id IS NULL" in text


def test_shelf_conditions_are_shared_with_the_counts():
    listing = sql(select(Photo).where(search.awaits_review()))
    awaiting = sql(select(Photo).where(search.awaits_model()))

    assert "photos.review_status" in listing
    assert "photos.origin" in listing
    assert "annotations.photo_id IS NULL" in awaiting
    assert "photos.origin !=" in awaiting


def test_catalog_statement_drops_the_rejected():
    assert "photos.review_status !=" in sql(search.catalog_statement())


def test_candidates_put_audited_photos_first_and_cap_the_list():
    statement = sql(search.candidates_statement(400))

    assert "photos.review_status !=" in statement
    assert statement.index("CASE WHEN") > statement.index("ORDER BY")
    assert "LIMIT" in statement


def test_stats_statement_returns_every_counter():
    names = {column.name for column in search.stats_statement().selected_columns}

    assert names >= {"photos", "outputs", "annotated", "to_review", "approved", "rejected"}


def test_render_catalog_is_one_line_per_photo():
    photo = annotated_photo(taken_at=datetime(2024, 7, 1, tzinfo=UTC))

    lines = search.render_catalog([photo, annotated_photo(id=8)]).splitlines()

    assert len(lines) == 2
    assert lines[0].split("\t")[:3] == ["#7", "2024-07-01", "unverified"]
    assert "named=Alex" in lines[0]
    assert "light=left,hard,golden_hour" in lines[0]
    assert lines[0].endswith(ANNOTATION["caption"])
    assert lines[1].split("\t")[1] == "undated"


def test_render_catalog_marks_what_a_human_verified_and_where_it_came_from():
    photo = annotated_photo(origin="stock")
    photo.review_status = "approved"

    fields = search.render_catalog([photo]).split("\t")

    assert fields[2] == "verified"
    assert fields[3] == "stock"


def test_render_catalog_of_nothing_is_empty():
    assert search.render_catalog([]) == ""


def test_catalog_is_far_smaller_than_an_image_token_cost():
    # The point of the project. A 1568px image costs roughly 2,000 input tokens, and four
    # characters is roughly a token.
    line = search.render_catalog([annotated_photo()])

    assert len(line) / 4 < 100
