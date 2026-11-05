import httpx
import pytest

from photolab import stock

ITEM = {
    "id": 42,
    "width": 4000,
    "height": 3000,
    "alt": "Empty beach at sunset",
    "photographer": "Ana",
    "photographer_url": "https://www.pexels.com/@ana",
    "url": "https://www.pexels.com/photo/42/",
    "src": {
        "medium": "https://images.pexels.com/photos/42/m.jpeg",
        "original": "https://images.pexels.com/photos/42/o.jpeg",
    },
}


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_parse_photo_tolerates_missing_alt_text():
    item = {**ITEM, "alt": None}

    parsed = stock.parse_photo(item)

    assert isinstance(parsed, stock.StockPhoto)
    assert parsed.alt == ""


def test_attribution_records_the_licence_and_the_photographer():
    record = stock.attribution(stock.parse_photo(ITEM))

    assert record["provider"] == "pexels"
    assert record["photographer"] == "Ana"
    assert record["license"].startswith("https://www.pexels.com/")


async def test_search_sends_the_key_and_parses_results():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        seen["query"] = request.url.params["query"]
        return httpx.Response(200, json={"photos": [ITEM]})

    async with client_for(handler) as client:
        photos = await stock.search_photos(client, "key", "beach")

    assert seen == {"auth": "key", "query": "beach"}
    assert photos[0].id == 42


async def test_get_photo_reads_one_by_id():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/photos/42")
        return httpx.Response(200, json=ITEM)

    async with client_for(handler) as client:
        assert (await stock.get_photo(client, "key", 42)).photographer == "Ana"


async def test_a_rejected_key_is_a_stock_error():
    async with client_for(lambda _: httpx.Response(401)) as client:
        with pytest.raises(stock.StockError):
            await stock.search_photos(client, "bad", "beach")


async def test_download_returns_bytes():
    async with client_for(lambda _: httpx.Response(200, content=b"jpeg")) as client:
        assert await stock.download_photo(client, stock.parse_photo(ITEM)) == b"jpeg"


async def test_download_failure_is_a_stock_error():
    async with client_for(lambda _: httpx.Response(500)) as client:
        with pytest.raises(stock.StockError):
            await stock.download_photo(client, stock.parse_photo(ITEM))


async def test_download_refuses_any_other_host():
    item = {**ITEM, "src": {**ITEM["src"], "original": "http://169.254.169.254/latest"}}

    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("must not be requested")

    async with client_for(handler) as client:
        with pytest.raises(stock.StockError, match="refusing"):
            await stock.download_photo(client, stock.parse_photo(item))
