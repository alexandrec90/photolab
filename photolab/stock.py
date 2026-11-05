"""Licensed backgrounds from Pexels.

An API and not a web scraper, for one reason: anything published needs rights to its
background, and an image scraped from a search result comes with none. Pexels' licence
allows modification and commercial use, and its API returns the attribution to store.
"""

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

API_ROOT = "https://api.pexels.com/v1"
# Downloads go only to Pexels' image host, whatever a response says.
_IMAGE_HOST = "images.pexels.com"
_TIMEOUT = 30.0


class StockError(Exception):
    """Pexels could not be reached, or refused."""


@dataclass(frozen=True)
class StockPhoto:
    id: int
    width: int
    height: int
    alt: str
    photographer: str
    photographer_url: str
    page_url: str
    preview_url: str
    download_url: str


def parse_photo(item: dict[str, Any]) -> StockPhoto:
    return StockPhoto(
        id=item["id"],
        width=item["width"],
        height=item["height"],
        alt=item.get("alt") or "",
        photographer=item.get("photographer") or "",
        photographer_url=item.get("photographer_url") or "",
        page_url=item.get("url") or "",
        preview_url=item["src"]["medium"],
        download_url=item["src"]["original"],
    )


def attribution(photo: StockPhoto) -> dict[str, Any]:
    """What `Photo.source` records."""
    return {
        "provider": "pexels",
        "id": photo.id,
        "license": "https://www.pexels.com/license/",
        "photographer": photo.photographer,
        "photographer_url": photo.photographer_url,
        "page_url": photo.page_url,
        "alt": photo.alt,
    }


async def _get_json(client: httpx.AsyncClient, key: str, url: str, **params: Any) -> Any:
    try:
        response = await client.get(
            url, params=params, headers={"Authorization": key}, timeout=_TIMEOUT
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise StockError(f"Pexels request failed: {exc}") from exc
    return response.json()


async def search_photos(
    client: httpx.AsyncClient, key: str, query: str, per_page: int = 24
) -> list[StockPhoto]:
    payload = await _get_json(client, key, f"{API_ROOT}/search", query=query, per_page=per_page)
    return [parse_photo(item) for item in payload.get("photos", [])]


async def get_photo(client: httpx.AsyncClient, key: str, photo_id: int) -> StockPhoto:
    return parse_photo(await _get_json(client, key, f"{API_ROOT}/photos/{photo_id}"))


async def download_photo(client: httpx.AsyncClient, photo: StockPhoto) -> bytes:
    if urlsplit(photo.download_url).hostname != _IMAGE_HOST:
        raise StockError(f"refusing to download from {photo.download_url}")
    try:
        response = await client.get(photo.download_url, timeout=_TIMEOUT, follow_redirects=False)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise StockError(f"Pexels download failed: {exc}") from exc
    return response.content
