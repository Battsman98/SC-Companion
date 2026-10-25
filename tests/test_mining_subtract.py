import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from src import bot, web
from src.cache import SQLiteCache


ENTRY = {"material": "Borase", "system": "Stanton", "location_type": "moons", "location": "Aberdeen"}


def interaction_for(cache, *, authority=False, owner=True):
    client = Mock(spec=bot.GameAssistBot)
    client.cache = cache
    client.settings = SimpleNamespace(
        approval_authority=authority, bot_admin_user_ids=(42,) if owner else (), discord_guild_id=123,
    )
    client.sources = SimpleNamespace(lookup_mining_material=AsyncMock(return_value=SimpleNamespace(material_name="Borase")))
    client.log_audit_event = AsyncMock()
    return SimpleNamespace(
        client=client, user=SimpleNamespace(id=42), guild_id=123, channel_id=456,
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


async def subtract(interaction, location="Aberdeen"):
    await bot.miningsubtract_command.callback(
        interaction, "BORA", "Stanton", discord.app_commands.Choice(name="Moon", value="moons"), location,
    )


def test_removal_matches_all_fields_and_persists(tmp_path):
    async def run():
        path = str(tmp_path / "cache.sqlite")
        cache = await SQLiteCache.create(path)
        other_entries = [
            {**ENTRY, "material": "Gold"}, {**ENTRY, "system": "Pyro"},
            {**ENTRY, "location_type": "points_of_interest"}, {**ENTRY, "location": "Daymar"},
        ]
        for entry in [ENTRY, *other_entries]:
            await bot.add_community_mining_location(cache, entry)
        assert not await bot.remove_community_mining_location(cache, {**ENTRY, "location": "Aber"})
        assert await bot.remove_community_mining_location(cache, {**ENTRY, "material": " borase ", "location": " ABERDEEN "})
        assert not await bot.remove_community_mining_location(cache, ENTRY)
        await cache.close()
        cache = await SQLiteCache.create(path)
        try:
            saved = await bot._community_mining_entries(cache)
            assert sum(len(entries) for entries in saved.values()) == 4
            for entry in other_entries:
                assert any(bot._same_community_mining_location(item, entry) for item in saved[entry["material"].lower()])
        finally:
            await cache.close()
    asyncio.run(run())


@pytest.mark.parametrize("authority,owner,direct", [(False, True, False), (True, False, False), (True, True, True)])
def test_command_respects_central_review(tmp_path, monkeypatch, authority, owner, direct):
    monkeypatch.setattr(bot, "_can_manage_change_commands", lambda *_: True)
    async def run():
        cache = await SQLiteCache.create(str(tmp_path / "cache.sqlite"))
        try:
            await bot.add_community_mining_location(cache, ENTRY)
            interaction = interaction_for(cache, authority=authority, owner=owner)
            await subtract(interaction)
            pending = await cache.pending_review_requests()
            saved = await bot._community_mining_entries(cache)
            if direct:
                assert saved == {}
                assert pending == []
                assert interaction.client.log_audit_event.call_args.args[0] == "Mining Location Removed"
            else:
                assert len(saved["borase"]) == 1
                assert pending[0]["review_type"] == "mining_subtract"
                assert pending[0]["origin_guild_id"] == 123
                assert pending[0]["payload"]["location"] == "Aberdeen"
            assert interaction.followup.send.call_args.kwargs["ephemeral"] is True
        finally:
            await cache.close()
    asyncio.run(run())


def test_denied_and_missing_removals_do_not_change_data(tmp_path, monkeypatch):
    async def run():
        cache = await SQLiteCache.create(str(tmp_path / "cache.sqlite"))
        try:
            await bot.add_community_mining_location(cache, ENTRY)
            interaction = interaction_for(cache)
            monkeypatch.setattr(bot, "_can_manage_change_commands", lambda *_: False)
            await subtract(interaction)
            interaction.client.sources.lookup_mining_material.assert_not_awaited()
            assert "permission" in interaction.response.send_message.call_args.args[0]
            monkeypatch.setattr(bot, "_can_manage_change_commands", lambda *_: True)
            await subtract(interaction, "Missing")
            assert "No matching" in interaction.followup.send.call_args.args[0]
            assert await cache.pending_review_requests() == []
            assert len((await bot._community_mining_entries(cache))["borase"]) == 1
        finally:
            await cache.close()
    asyncio.run(run())


@pytest.mark.parametrize("interface", ["discord", "website"])
@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_review_removes_only_on_approval(tmp_path, monkeypatch, interface, decision):
    async def run():
        cache = await SQLiteCache.create(str(tmp_path / "cache.sqlite"))
        try:
            await bot.add_community_mining_location(cache, ENTRY)
            review_id = await cache.submit_review_request("mining_subtract", ENTRY, submitted_by=42, submitted_by_name="Pilot")
            interaction = interaction_for(cache, authority=True)
            if interface == "discord":
                await bot.admin_review_command.callback(
                    interaction, review_id, discord.app_commands.Choice(name=decision.title(), value=decision),
                )
            else:
                monkeypatch.setattr(web, "state", lambda: SimpleNamespace(cache=cache, settings=interaction.client.settings))
                monkeypatch.setattr(web, "current_user_from_request", lambda *_: SimpleNamespace(id=42, username="Owner", display_name="Owner"))
                await web.decide_global_review(review_id, web.ReviewDecisionRequest(decision=decision), Mock())
            saved = await bot._community_mining_entries(cache)
            assert bool(saved) is (decision == "rejected")
            assert await cache.pending_review_requests() == []
        finally:
            await cache.close()
    asyncio.run(run())
