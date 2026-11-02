import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.sources.uex import UEXSource


CATEGORIES = [
    {"id": 28, "section": "Utility", "name": "Gadgets", "is_game_related": 1},
    {"id": 30, "section": "Utility", "name": "Mining Modules", "is_game_related": 1},
]
ITEMS = {
    28: [{"id": 105, "name": "Sabir", "section": "Utility", "category": "Gadgets"}],
    30: [{"id": 106, "name": "Brandt Module", "section": "Utility", "category": "Mining Modules"}],
}
PRICES = [{"id_item": item_id, "price_buy": 13815} for item_id in (105, 106)]


def make_source():
    cache = AsyncMock()
    cache.get.return_value = None
    source = UEXSource(SimpleNamespace(cache_ttl_seconds=300), cache, None)
    source._ordnance_categories = ()

    async def fetch(url):
        if "/categories?" in url:
            return {"data": CATEGORIES}
        if "/items_prices_all" in url:
            return {"data": PRICES}
        return {"data": ITEMS[int(url.rsplit("=", 1)[1])]}

    source._fetch_json = AsyncMock(side_effect=fetch)
    return source


@pytest.mark.parametrize("failed_endpoint", ["categories?", "items_prices_all", "items?id_category=28", "items?id_category=30"])
@pytest.mark.parametrize("failure", [None, {"data": None}, {"data": "unavailable"}])
def test_item_catalog_recovers_after_one_failed_request(failed_endpoint, failure):
    async def run():
        source = make_source()
        fetch = source._fetch_json.side_effect
        failed = False

        async def fail_once(url):
            nonlocal failed
            if failed_endpoint in url and not failed:
                failed = True
                return failure
            return await fetch(url)

        source._fetch_json.side_effect = fail_once
        await source.autocomplete_items("sabir")

        assert await source.autocomplete_items("sabir") == ["Sabir"]
        assert await source.autocomplete_items("brandt") == ["Brandt Module"]
        assert await source.autocomplete_item_filter("category", "gad") == ["Gadgets"]
        assert await source.autocomplete_item_filter("category", "mining") == ["Mining Modules"]
        assert all(call.args[1] for call in source._cache.set.await_args_list)

    asyncio.run(run())


def test_item_catalog_ignores_empty_persistent_cache_from_failed_load():
    async def run():
        source = make_source()
        source._cache.get.return_value = []

        assert await source.autocomplete_items("sabir") == ["Sabir"]
        assert await source.autocomplete_items("brandt") == ["Brandt Module"]

    asyncio.run(run())


def test_item_catalog_refreshes_a_long_running_worker(monkeypatch):
    async def run():
        now = [1000.0]
        monkeypatch.setattr("src.sources.uex.time.monotonic", lambda: now[0])
        source = make_source()
        original_fetch = source._fetch_json.side_effect
        recovered = False

        async def fetch(url):
            if "items?id_category=28" in url and not recovered:
                return {"data": []}
            return await original_fetch(url)

        source._fetch_json.side_effect = fetch
        assert await source.autocomplete_items("sabir") == []
        recovered = True
        now[0] += 301

        assert await source.autocomplete_items("sabir") == ["Sabir"]
        assert await source.autocomplete_items("brandt") == ["Brandt Module"]

    asyncio.run(run())


def test_item_catalog_keeps_previous_results_when_refresh_fails(monkeypatch):
    async def run():
        now = [1000.0]
        monkeypatch.setattr("src.sources.uex.time.monotonic", lambda: now[0])
        source = make_source()
        assert await source.autocomplete_items("sabir") == ["Sabir"]
        fetch = source._fetch_json.side_effect
        now[0] += 301

        async def fail_gadgets(url):
            if "items?id_category=28" in url:
                return None
            return await fetch(url)

        source._fetch_json.side_effect = fail_gadgets
        assert await source.autocomplete_items("sabir") == ["Sabir"]
        assert await source.autocomplete_items("brandt") == ["Brandt Module"]

        source._fetch_json.side_effect = fetch
        assert await source.autocomplete_items("sabir") == ["Sabir"]

    asyncio.run(run())


def test_discord_item_search_and_autocomplete_include_mining_items():
    from src.bot import (
        GameAssistBot,
        item_locator_filter_autocomplete,
        item_locator_name_autocomplete,
        item_search_command,
    )
    from src.sources.registry import SourceRegistry

    async def run():
        source = make_source()
        source._fetch_terminals_by_id = AsyncMock(return_value={})
        source._fetch_item_prices = AsyncMock(return_value=PRICES[:1])
        bot = GameAssistBot.__new__(GameAssistBot)
        bot.sources = SourceRegistry([source], None)
        interaction = SimpleNamespace(
            client=bot,
            response=AsyncMock(),
            followup=AsyncMock(),
            data={"options": [{"name": "search", "options": [{"name": "category", "focused": True}]}]},
        )

        choices = await item_locator_name_autocomplete(interaction, "sabi")
        assert [choice.value for choice in choices] == ["Sabir"]
        choices = await item_locator_filter_autocomplete(interaction, "mining")
        assert [choice.value for choice in choices] == ["Mining Modules"]
        choices = await item_locator_filter_autocomplete(interaction, "gad")
        assert [choice.value for choice in choices] == ["Gadgets"]

        await item_search_command.callback(interaction, name="Sabir")
        embed = interaction.followup.send.await_args.kwargs["embed"]
        assert embed.title == "Sabir"
        assert "Category: Gadgets" in embed.description
        assert all(len(field.value) <= 1024 for field in embed.fields)

        for category, name in (("Mining Modules", "Brandt Module"), ("Gadgets", "Sabir")):
            await item_search_command.callback(interaction, category=category)
            embed = interaction.followup.send.await_args.kwargs["embed"]
            assert embed.title == "Item Locator Results"
            assert name in embed.fields[0].value
            view = interaction.followup.send.await_args.kwargs["view"]
            view.stop()

    asyncio.run(run())
