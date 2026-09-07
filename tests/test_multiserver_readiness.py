import asyncio

from src.bot import build_bot_setup_guide_embed, cz_timers_cache_key, exec_override_cache_key
from src.cache import SQLiteCache
from src.guild_config import BOT_MODULES, module_for_command


def test_timer_commands_are_shared_module_commands() -> None:
    assert "timers" in BOT_MODULES
    assert module_for_command("exec") == "timers"
    assert module_for_command("execset") == "timers"
    assert module_for_command("cztimer") == "timers"


def test_mining_tools_are_visible_and_complete() -> None:
    mining = BOT_MODULES["mining_tools"]
    assert mining["label"] == "Mining Tools"
    assert set(mining["commands"]) == {
        "mining", "miningadd", "industry split", "industry refinery", "industry brief"
    }


def test_timer_cache_keys_are_isolated_by_guild() -> None:
    assert exec_override_cache_key(None) != exec_override_cache_key(123)
    assert exec_override_cache_key(123) != exec_override_cache_key(456)
    assert cz_timers_cache_key(123) != cz_timers_cache_key(456)


def test_discord_setup_guide_has_short_ordered_steps() -> None:
    embed = build_bot_setup_guide_embed()
    field_names = [field.name for field in embed.fields]
    assert field_names[:4] == [
        "1. Open the panel", "2. Choose channel setup", "3. Pick the features", "4. Finish and test"
    ]
    assert "/admin panel" in embed.fields[0].value
    assert "other servers" in (embed.footer.text or "")


def test_review_queue_and_server_analytics_are_persistent(tmp_path) -> None:
    asyncio.run(_exercise_review_queue_and_server_analytics(tmp_path))


async def _exercise_review_queue_and_server_analytics(tmp_path) -> None:
    cache = await SQLiteCache.create(str(tmp_path / "readiness.sqlite3"))
    review_id = await cache.submit_review_request(
        "timer", {"phase": "open"}, submitted_by=7, submitted_by_name="Tester", origin_guild_id=42
    )
    pending = await cache.pending_review_requests()
    assert pending[0]["id"] == review_id
    assert pending[0]["origin_guild_id"] == 42
    assert await cache.review_request(review_id, "approved", 1, "Owner") is True
    assert await cache.pending_review_requests() == []

    await cache.record_guild_installation(42, "Test One", 10)
    await cache.record_guild_installation(43, "Test Two", 20)
    stats = await cache.guild_installation_stats()
    assert stats["active_servers"] == 2
    assert stats["visible_members"] == 30
    await cache.record_guild_installation(43, "Test Two", 20, active=False)
    assert (await cache.guild_installation_stats())["active_servers"] == 1
    await cache.close()
