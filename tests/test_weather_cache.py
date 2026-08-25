import pytest

from utils import weather


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"results": [{"name": "Hong Kong", "latitude": 22.3, "longitude": 114.2}]}


class _Client:
    def __init__(self):
        self.calls = 0

    async def get(self, url, params):
        self.calls += 1
        return _Response()


@pytest.mark.asyncio
async def test_geocoding_reuses_short_lived_cache():
    weather._GEOCODE_CACHE.clear()
    client = _Client()

    first = await weather._geocode(client, "Hong Kong")
    second = await weather._geocode(client, " hong kong ")

    assert first == second
    assert client.calls == 1
