"""The URL contract the album is written against, and the dependencies under it."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from photolab.api import actions, deps, photos
from photolab.api.app import build_app
from tests.conftest import write_photo

ROUTES = {
    ("GET", "/api/stats"): photos.stats,
    ("GET", "/api/photos"): photos.list_photos,
    ("GET", "/api/catalog"): photos.catalog,
    ("GET", "/api/photos/{photo_id}"): photos.get_photo,
    ("PATCH", "/api/photos/{photo_id}"): photos.edit_photo,
    ("GET", "/api/photos/{photo_id}/thumb"): photos.thumbnail,
    ("GET", "/api/photos/{photo_id}/image"): photos.image,
    ("POST", "/api/library/scan"): actions.start_scan,
    ("POST", "/api/annotations/run"): actions.start_annotation,
    ("POST", "/api/ai/suggest"): actions.suggest_references,
    ("GET", "/api/stock/search"): actions.search_stock,
    ("POST", "/api/stock/import"): actions.import_stock,
    ("GET", "/api/compose/advice"): actions.compose_advice,
    ("POST", "/api/generations"): actions.create_generation,
    ("GET", "/api/generations"): actions.list_generations,
}


def test_every_route_is_registered_at_its_path(context):
    registered = {
        (method, route.path): route.endpoint
        for router in (photos.router, actions.router)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }
    served = {
        (method.upper(), path)
        for path, operations in build_app(context).openapi()["paths"].items()
        for method in operations
    }

    assert registered == ROUTES, "a route was added or moved without pinning it here"
    assert served == set(ROUTES), "a router is not included in the app"


def request_for(context) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(context=context)))


def test_get_context_reads_the_app_state(context):
    assert deps.get_context(request_for(context)) is context


async def test_get_session_yields_a_working_session(context):
    sessions = deps.get_session(request_for(context))

    session = await anext(sessions)

    assert session.bind is not None
    await sessions.aclose()


async def test_require_photo_raises_a_404(context):
    async with context.sessions() as session:
        with pytest.raises(HTTPException) as caught:
            await deps.require_photo(999, session)

    assert caught.value.status_code == 404


def test_the_context_holds_what_the_routes_need(context, settings):
    write_photo(settings)

    assert isinstance(context, deps.AppContext)
    assert context.settings is settings
