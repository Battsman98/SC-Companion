import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.cache import SQLiteCache
from src.guild_config import BOT_MODULES, default_module_settings, module_for_command, normalize_module_settings
from src import web
from src.web import GuildBotSettingsRequest, GuildModuleRequest


def test_requested_commands_map_to_configurable_modules() -> None:
    assert module_for_command("ship") == "ship_search"
    assert module_for_command("mining") == "mining_tools"
    assert module_for_command("blueprint") == "blueprints"
    assert module_for_command("mission") == "missions_wikelo"
    assert module_for_command("wikelo") == "missions_wikelo"
    assert module_for_command("item search") == "item_locator"
    assert module_for_command("inventory search") == "inventory_search"
    assert module_for_command("trade routing") == "trade_tools"


def test_normalization_fills_missing_modules_and_validates_channel_ids() -> None:
    modules = normalize_module_settings({"ship_search": {"enabled": True, "channel_id": "123"}})

    assert set(modules) == set(BOT_MODULES)
    assert modules["ship_search"] == {"enabled": True, "channel_id": 123, "resource_channel_id": None}
    assert modules["trade_tools"] == {"enabled": False, "channel_id": None, "resource_channel_id": None}
    assert all(item["enabled"] for item in default_module_settings().values())


def test_guild_settings_round_trip(tmp_path) -> None:
    async def scenario() -> None:
        cache = await SQLiteCache.create(str(tmp_path / "guild-settings.sqlite3"))
        modules = default_module_settings(False)
        modules["ship_search"] = {"enabled": True, "channel_id": 456}

        await cache.save_guild_bot_settings(123, "Test Server", modules, 99, "automatic")
        saved = await cache.guild_bot_settings(123)

        assert saved is not None
        assert saved["guild_name"] == "Test Server"
        assert saved["configured_by"] == 99
        assert saved["channel_setup_mode"] == "automatic"
        assert saved["modules"]["ship_search"] == {"enabled": True, "channel_id": 456}

        await cache.replace_user_managed_guilds(99, [
            {"id": 123, "name": "Test Server", "icon_url": None, "permissions": 0x20, "owner": False}
        ])
        guilds = await cache.user_managed_guilds(99)
        assert guilds[0]["id"] == 123
        assert guilds[0]["name"] == "Test Server"
        await cache.close()

    asyncio.run(scenario())


def test_management_panel_is_available_to_discord_server_managers() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    html = (root / "web" / "index.html").read_text(encoding="utf-8")
    javascript = (root / "web" / "app.js").read_text(encoding="utf-8")

    assert 'id="bot-management"' in html
    assert 'id="botManagementTabTemplate"' in html
    assert "setBotManagementVisibility(Boolean(currentUser.authenticated && currentUser.can_manage_guilds))" in javascript
    assert 'api("/api/bot-management/guilds")' in javascript
    assert "Save Bot Settings" in javascript


def test_management_api_saves_only_a_users_managed_guild(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        cache = await SQLiteCache.create(str(tmp_path / "management-api.sqlite3"))
        await cache.replace_user_managed_guilds(99, [
            {"id": 123, "name": "Test Server", "icon_url": None, "permissions": 0x20, "owner": False}
        ])
        monkeypatch.setattr(web, "state", lambda: SimpleNamespace(cache=cache))
        verify = AsyncMock()
        monkeypatch.setattr(web, "_verify_live_guild_manager", verify)
        monkeypatch.setattr(web, "_discord_guild_channels", AsyncMock(return_value=[{"id": 456, "name": "ships", "type": 0}]))
        user = SimpleNamespace(id=99, username="pilot")
        payload = GuildBotSettingsRequest(modules={
            "ship_search": GuildModuleRequest(enabled=True, channel_id=456),
        })

        result = await web.save_guild_bot_configuration(123, payload, user)

        assert result["status"] == "saved"
        assert result["modules"]["ship_search"] == {"enabled": True, "channel_id": 456, "resource_channel_id": None}
        verify.assert_awaited_once_with(123, 99)
        saved = await cache.guild_bot_settings(123)
        assert saved is not None and saved["guild_name"] == "Test Server"
        await cache.close()

    asyncio.run(scenario())


def test_trade_module_keeps_marketplace_forum_separate_from_command_channel() -> None:
    modules = normalize_module_settings({
        "trade_tools": {"enabled": True, "channel_id": 100, "resource_channel_id": 200}
    })

    assert modules["trade_tools"] == {
        "enabled": True,
        "channel_id": 100,
        "resource_channel_id": 200,
    }
