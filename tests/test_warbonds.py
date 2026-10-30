from pathlib import Path

import asyncio

from src.sources.warbonds import WarbondTrackerSource


WEB_DIR = Path(__file__).resolve().parents[1] / "web"


def test_best_warbond_source_uses_closest_eligible_ship() -> None:
    catalog = [
        {"name": "Constellation Taurus", "price": 200, "on_sale": True, "status": "flight ready"},
        {"name": "Apollo Medivac", "price": 290, "on_sale": False, "status": "concept"},
        {"name": "Vanguard Harbinger", "price": 290, "on_sale": False, "status": "flight ready"},
        {"name": "Too Expensive", "price": 300, "on_sale": True, "status": "flight ready"},
    ]

    assert WarbondTrackerSource._best_source(catalog, 295)["name"] == "Apollo Medivac"
    assert WarbondTrackerSource._best_source(catalog, 295, flight_ready=True) == {
        "name": "Vanguard Harbinger",
        "price": 5,
    }
    assert WarbondTrackerSource._best_source(catalog, 295, on_sale=True) == {
        "name": "Constellation Taurus",
        "price": 95,
    }


def test_rsi_discovery_uses_current_skus_not_historical_catalog_offers() -> None:
    async def scenario():
        source = WarbondTrackerSource.__new__(WarbondTrackerSource)
        calls = []
        catalog = [
            {"id": 10, "name": "Discovered Target", "msrp": 20000,
             "link": "/pledge/ships/target", "flyableStatus": "Flyable",
             "medias": {"productThumbMediumAndSmall": "https://media.robertsspaceindustries.com/test.jpg"},
             "skus": [{"id": 99, "title": "Expired Warbond Edition", "price": 10000, "available": True}]},
            {"id": 20, "name": "Source Concept", "msrp": 17500, "flyableStatus": "In Concept"},
            {"id": 30, "name": "Source Flyable", "msrp": 17000, "flyableStatus": "Flyable"},
        ]
        current = {
            "s10": {"skus": [
                {"id": 1, "title": "Warbond Edition", "price": 18000, "available": True},
                {"id": 2, "title": "Warbond Edition", "price": 15000, "available": False},
                {"id": 3, "title": "Standard Edition", "price": 19000, "available": True},
            ]},
            "s20": {"skus": [{"id": 4, "title": "Standard Edition", "available": True, "unlimitedStock": False}]},
            "s30": {"skus": [{"id": 5, "title": "Standard Edition", "available": True, "unlimitedStock": True}]},
        }
        async def graphql(query):
            calls.append(query)
            if query == source.CATALOG_QUERY:
                return {"ships": catalog, "app": {"pricing": {
                    "currencyCode": "USD", "exponent": 2, "exchangeRate": 1,
                    "taxRate": 0, "isTaxInclusive": False,
                }}}
            return current
        source._graphql = graphql
        result = await source._refresh()
        assert len(result["offers"]) == 1
        offer = result["offers"][0]
        assert offer["name"] == "Discovered Target"
        assert offer["warbond_price"] == 180
        assert offer["standard_price"] == 200
        assert offer["saving"] == 20
        assert offer["cheapest_source"] == {"name": "Source Concept", "price": 5}
        assert offer["flight_ready_source"] == {"name": "Source Flyable", "price": 10}
        assert offer["unlimited_source"] == {"name": "Source Flyable", "price": 10}
        assert offer["image_url"].startswith("https://media.robertsspaceindustries.com/")
        assert offer["store_url"] == "https://robertsspaceindustries.com/pledge/ships/target"
        assert len(calls) == 2
        # A successful check with no active offers must remove previous offers.
        current["s10"]["skus"] = []
        assert (await source._refresh())["offers"] == []
    asyncio.run(scenario())


def test_rsi_graphql_rejects_partial_error_responses() -> None:
    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def json(self):
            return {"data": {"ships": []}, "errors": [{"message": "unavailable"}]}
    class Session:
        def post(self, *args, **kwargs): return Response()
    async def scenario():
        source = WarbondTrackerSource.__new__(WarbondTrackerSource)
        source._session = Session()
        import pytest
        with pytest.raises(ValueError, match="could not be verified"):
            await source._graphql("query { ships { id } }")
    asyncio.run(scenario())


def test_warbond_prices_use_localized_pledge_currency_not_auec() -> None:
    javascript = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    renderer = javascript.split("function renderWarbond", 1)[1].split("function intelGroup", 1)[0]

    assert "pledgeMoney(offer.warbond_price, offer.currency)" in renderer
    assert "pledgeMoney(offer.standard_price, offer.currency)" in renderer
    assert "money(offer.warbond_price)" not in renderer
    assert 'style: "currency"' in javascript


def test_forced_warbond_refresh_bypasses_cache_and_replaces_it() -> None:
    class Cache:
        def __init__(self) -> None:
            self.value = {"offers": [{"name": "Cached"}]}

        async def get(self, _key):
            return self.value

        async def set(self, _key, value, _ttl):
            self.value = value

    async def scenario() -> None:
        source = WarbondTrackerSource.__new__(WarbondTrackerSource)
        source._cache = Cache()
        source._refresh_lock = asyncio.Lock()
        source._last_good = None
        refreshes = 0

        async def refresh():
            nonlocal refreshes
            refreshes += 1
            return {"offers": [{"name": "Live"}], "checked_at": "2026-08-01T00:00:00+00:00"}

        source._refresh = refresh
        assert (await source.active())["offers"][0]["name"] == "Cached"
        assert (await source.active(force_refresh=True))["offers"][0]["name"] == "Live"
        assert refreshes == 1
        assert source._cache.value["offers"][0]["name"] == "Live"

    asyncio.run(scenario())


def test_failed_refresh_preserves_original_timestamp_and_marks_stale() -> None:
    class Cache:
        async def get(self, key):
            return {"offers": [{"name": "Previously verified"}], "checked_at": "original-time"}
        async def set(self, *args):
            raise AssertionError("A failed refresh must not overwrite the cache")
    async def scenario():
        source = WarbondTrackerSource.__new__(WarbondTrackerSource)
        source._cache = Cache()
        source._refresh_lock = asyncio.Lock()
        source._last_good = None
        async def refresh(): raise ValueError("RSI unavailable")
        source._refresh = refresh
        result = await source.active(force_refresh=True)
        assert result["stale"] is True
        assert result["checked_at"] == "original-time"
    asyncio.run(scenario())
