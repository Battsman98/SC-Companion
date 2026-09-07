import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.bot import (
    _automatic_module_channel_id,
    FirstRunSetupView,
    ConfirmBotUninstallView,
    GameAssistBot,
    NativeAdminView,
    ManualChannelWizardView,
    build_bot_setup_guide_embed,
    build_first_run_setup_embed,
    build_uninstall_warning_embed,
    cz_timers_cache_key,
    exec_override_cache_key,
    manual_channel_steps,
    timer_dashboard_channel_id,
    automatic_cleanup_channel_ids,
)
from src.cache import SQLiteCache
from src.guild_config import BOT_MODULES, module_for_command


def test_timer_commands_are_shared_module_commands() -> None:
    assert "timers" in BOT_MODULES
    assert module_for_command("exec") == "timers"
    assert module_for_command("execset") == "timers"
    assert module_for_command("cztimer") == "timers"


def test_automatic_setup_resolves_each_feature_to_its_own_channel() -> None:
    category = SimpleNamespace(id=50, name="SC Companion")
    ship = SimpleNamespace(id=101, name="ship-search", category_id=50)
    mining = SimpleNamespace(id=102, name="mining-tools", category_id=50)
    unrelated = SimpleNamespace(id=999, name="ship-search", category_id=60)
    guild = SimpleNamespace(categories=[category], text_channels=[unrelated, ship, mining])

    assert _automatic_module_channel_id(guild, "ship_search") == 101
    assert _automatic_module_channel_id(guild, "mining_tools") == 102
    assert _automatic_module_channel_id(guild, "timers") is None


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


def test_first_run_notice_points_to_admin_panel_and_has_persistent_button() -> None:
    embed = build_first_run_setup_embed()
    assert "/admin panel" in (embed.description or "")
    button = FirstRunSetupView().children[0]
    assert button.label == "Open Admin Panel"
    assert button.custom_id == "sc-companion:first-run-admin-panel"
    missing_permission = build_first_run_setup_embed(can_manage_channels=False)
    assert any(field.name == "One permission is still needed" for field in missing_permission.fields)


def test_manual_setup_has_an_explicit_next_step_for_each_enabled_feature() -> None:
    modules = {
        key: {"enabled": key in {"ship_search", "trade_tools"}, "channel_id": None, "resource_channel_id": None}
        for key in BOT_MODULES
    }
    assert manual_channel_steps(modules) == [
        ("ship_search", "channel_id"),
        ("trade_tools", "channel_id"),
        ("trade_tools", "resource_channel_id"),
    ]
    labels = [getattr(item, "label", None) for item in NativeAdminView(modules, "manual").children]
    assert "Next: Assign Channels" in labels
    automatic_labels = [getattr(item, "label", None) for item in NativeAdminView(modules, "automatic").children]
    assert "Next: Assign Channels" not in automatic_labels

    timer_modules = {
        key: {"enabled": key == "timers", "channel_id": None, "resource_channel_id": None}
        for key in BOT_MODULES
    }
    wizard = ManualChannelWizardView(timer_modules)
    channel_select = wizard.children[0]
    assert channel_select.placeholder == "Choose a channel for Timers"
    assert "channel for Timers" in (wizard.embed().description or "")


def test_timer_dashboard_uses_the_enabled_servers_timer_channel() -> None:
    modules = {
        key: {"enabled": key == "timers", "channel_id": 1234 if key == "timers" else None,
              "resource_channel_id": None}
        for key in BOT_MODULES
    }
    assert timer_dashboard_channel_id(modules) == 1234
    modules["timers"]["enabled"] = False
    assert timer_dashboard_channel_id(modules) is None


def test_shared_recovery_only_protects_bot_owned_or_automatic_channels() -> None:
    async def scenario() -> None:
        modules = {
            key: {"enabled": key == "ship_search", "channel_id": 101 if key == "ship_search" else None,
                  "resource_channel_id": None}
            for key in BOT_MODULES
        }
        configured = {"channel_setup_mode": "automatic", "modules": modules}
        bot = GameAssistBot.__new__(GameAssistBot)
        bot.settings = SimpleNamespace(discord_guild_id=1)
        bot.cache = SimpleNamespace(
            guild_bot_settings=AsyncMock(return_value=configured),
            get=AsyncMock(return_value=None),
        )
        guild = SimpleNamespace(id=2)

        assert await bot._is_shared_setup_channel(SimpleNamespace(guild=guild, id=101, name="renamed"))
        assert await bot._is_shared_setup_channel(SimpleNamespace(guild=guild, id=500, name="SC Companion"))
        assert not await bot._is_shared_setup_channel(SimpleNamespace(guild=guild, id=999, name="general"))

        configured["channel_setup_mode"] = "manual"
        assert not await bot._is_shared_setup_channel(SimpleNamespace(guild=guild, id=101, name="renamed"))
        assert await bot._is_shared_setup_channel(SimpleNamespace(guild=guild, id=700, name="about-the-bot"))

    asyncio.run(scenario())


def test_uninstall_only_targets_channels_recorded_in_automatic_settings() -> None:
    modules = {
        key: {"enabled": True, "channel_id": index + 100, "resource_channel_id": None}
        for index, key in enumerate(BOT_MODULES)
    }
    modules["trade_tools"]["resource_channel_id"] = 999
    targets = automatic_cleanup_channel_ids(modules)
    assert 999 in targets
    assert len(targets) == len(BOT_MODULES) + 1
    labels = [getattr(item, "label", None) for item in ConfirmBotUninstallView().children]
    assert labels == ["Delete Bot Setup and Uninstall", "Cancel"]
    warning = build_uninstall_warning_embed().description or ""
    assert "about-the-bot" in warning
    assert "feedback-and-issues" in warning


def test_review_queue_and_server_analytics_are_persistent(tmp_path) -> None:
    asyncio.run(_exercise_review_queue_and_server_analytics(tmp_path))


def test_peep_approval_sync_runs_once_per_minute() -> None:
    source = inspect.getsource(GameAssistBot._loot_review_sync_loop)
    assert "_publish_pending_global_review_notifications" in source
    assert "asyncio.sleep(60)" in source


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


def test_uninstall_purges_server_settings_without_removing_install_history(tmp_path) -> None:
    asyncio.run(_exercise_guild_uninstall_purge(tmp_path))


async def _exercise_guild_uninstall_purge(tmp_path) -> None:
    cache = await SQLiteCache.create(str(tmp_path / "uninstall.sqlite3"))
    modules = {
        key: {"enabled": True, "channel_id": 900, "resource_channel_id": None}
        for key in BOT_MODULES
    }
    await cache.save_guild_bot_settings(42, "Removed Server", modules, 7, "manual")
    await cache.record_guild_installation(42, "Removed Server", 25)
    await cache.replace_user_managed_guilds(7, [{
        "id": 42, "name": "Removed Server", "permissions": 32, "owner": True,
    }])
    await cache.set("guild:42:about-panel-message", 123, 3600)
    await cache.set("exec:cycle-start-override:guild:42", {"phase": "open"}, 3600)
    await cache.submit_review_request(
        "timer", {"phase": "open"}, submitted_by=7, submitted_by_name="Tester", origin_guild_id=42,
    )

    await cache.record_guild_installation(42, "Removed Server", 25, active=False)
    await cache.purge_guild_data(42)

    assert await cache.guild_bot_settings(42) is None
    assert await cache.user_managed_guilds(7) == []
    assert await cache.get("guild:42:about-panel-message") is None
    assert await cache.get("exec:cycle-start-override:guild:42") is None
    assert await cache.pending_review_requests() == []
    stats = await cache.guild_installation_stats()
    assert stats["active_servers"] == 0
    assert stats["configured_servers"] == 0
    await cache.close()
