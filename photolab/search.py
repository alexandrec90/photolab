"""Finding photos by what is known about them, without looking at them again.

`Photo.search_text` is the one flat field every search runs against, and the catalog is
the compact listing an LLM reads instead of the images.
"""

from typing import Any

from sqlalchemy import ColumnElement, Select, and_, case, func, or_, select

from photolab.models import (
    ORIGIN_GENERATED,
    REVIEW_APPROVED,
    REVIEW_PENDING,
    REVIEW_REJECTED,
    Annotation,
    Photo,
)


def build_search_text(photo: Photo) -> str:
    """Flatten everything searchable about `photo`, lower-cased."""
    parts: list[str] = [photo.path, *photo.people]
    if photo.annotation is not None:
        data = photo.annotation.data
        parts += [
            data.get("caption", ""),
            data.get("scene", ""),
            data.get("setting", ""),
            data.get("time_of_day", ""),
            data.get("shot_type", ""),
            *data.get("tags", []),
            *data.get("people_descriptions", []),
        ]
    return " ".join(part for part in parts if part).lower()


def apply_query(statement: Select[Any], query: str | None) -> Select[Any]:
    """Require every word of `query`, in any order."""
    words = (query or "").lower().split()
    return statement.where(*(Photo.search_text.contains(word, autoescape=True) for word in words))


def awaits_review() -> ColumnElement[bool]:
    """Something a human can audit now: an annotation to check, or an output to judge."""
    return and_(
        Photo.review_status == REVIEW_PENDING,
        or_(Annotation.photo_id.is_not(None), Photo.origin == ORIGIN_GENERATED),
    )


def awaits_model() -> ColumnElement[bool]:
    """No annotation, and one is wanted: an output's contents are known from its recipe."""
    return and_(Annotation.photo_id.is_(None), Photo.origin != ORIGIN_GENERATED)


def photos_statement(
    query: str | None = None,
    origin: str | None = None,
    review: str | None = None,
    annotated: bool | None = None,
    reviewable: bool = False,
) -> Select[Any]:
    """The album's listing: newest first, undated photos after dated ones.

    The shelf filters are the same conditions `stats_statement` counts, so a shelf can
    never list a different number of photos from the one beside its name.
    """
    statement = select(Photo).outerjoin(Annotation)
    statement = apply_query(statement, query)
    if origin:
        statement = statement.where(Photo.origin == origin)
    if review:
        statement = statement.where(Photo.review_status == review)
    if annotated is not None:
        has_row = Annotation.photo_id.is_not(None)
        statement = statement.where(has_row if annotated else awaits_model())
    if reviewable:
        statement = statement.where(awaits_review())
    return statement.order_by(
        Photo.taken_at.desc().nulls_last(), Photo.created_at.desc(), Photo.id.desc()
    )


def catalog_statement(query: str | None = None) -> Select[Any]:
    """What a catalog lists: annotated photos a human has not thrown out."""
    statement = select(Photo).join(Annotation).where(Photo.review_status != REVIEW_REJECTED)
    return apply_query(statement, query).order_by(Photo.id)


def candidates_statement(limit: int) -> Select[Any]:
    """The catalog's most useful `limit` lines: audited photos first, then the newest."""
    approved_first = case((Photo.review_status == REVIEW_APPROVED, 0), else_=1)
    return catalog_statement().order_by(None).order_by(approved_first, Photo.id.desc()).limit(limit)


def _catalog_fields(photo: Photo) -> list[str]:
    data: dict[str, Any] = photo.annotation.data if photo.annotation else {}
    fields = [f"#{photo.id}", photo.taken_at.date().isoformat() if photo.taken_at else "undated"]
    fields.append("verified" if photo.review_status == REVIEW_APPROVED else "unverified")
    if photo.origin != "library":
        fields.append(photo.origin)
    fields.append(f"{data.get('setting', '?')}/{data.get('scene', '?')}")
    fields.append(
        f"light={data.get('light_direction', '?')},{data.get('light_quality', '?')},"
        f"{data.get('time_of_day', '?')}"
    )
    fields.append(f"people={data.get('person_count', 0)}")
    if photo.people:
        fields.append(f"named={','.join(photo.people)}")
    fields.append(data.get("shot_type", "?"))
    if tags := data.get("tags"):
        fields.append("tags=" + ",".join(tags))
    fields.append(data.get("caption", ""))
    return fields


def render_catalog(photos: list[Photo]) -> str:
    """One tab-separated line per photo, so a thousand of them cost a few thousand tokens."""
    return "\n".join("\t".join(_catalog_fields(photo)) for photo in photos)


def stats_statement() -> Select[Any]:
    """Counts for the album's sidebar, in one query."""

    def count_where(*conditions: Any) -> Any:
        return func.count(case((and_(*conditions), 1)))

    in_album = Photo.origin != ORIGIN_GENERATED
    is_output = Photo.origin == ORIGIN_GENERATED
    has_annotation = Annotation.photo_id.is_not(None)
    return select(
        count_where(in_album).label("photos"),
        count_where(is_output).label("outputs"),
        count_where(in_album, has_annotation).label("annotated"),
        count_where(awaits_model()).label("unannotated"),
        count_where(Photo.annotation_error.is_not(None)).label("failed"),
        count_where(awaits_review()).label("to_review"),
        count_where(Photo.review_status == REVIEW_APPROVED).label("approved"),
        count_where(Photo.review_status == REVIEW_REJECTED).label("rejected"),
    ).outerjoin(Annotation, Annotation.photo_id == Photo.id)
