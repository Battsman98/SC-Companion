import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.cache import SQLiteCache
from src.bot import GameAssistBot
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
    assert "setBotManagementVisibility(Boolean(currentUser.authenticated && currentUser.can_manage_bot))" in javascript
    assert 'api("/api/bot-management/guilds")' in javascript
    assert "Save Setup" in javascript
    assert "Save Features" in javascript
    assert "Save Channels" in javascript
    assert "Add SC Companion to Discord" in javascript
    assert "Create an award" in javascript
    assert "Enable awards" not in javascript
    assert "Where should awards be announced?" in javascript
    assert "Create or Repair All Feature Channels" in javascript
    assert "updateAllFeatureChannels" in javascript
    assert "All enabled feature channels are being created or repaired" in javascript
    assert "without consuming Discord role slots" in javascript
    assert "all 32 current reputation ladders" in javascript
    assert "stable two-color ribbons" in javascript
    assert "Create reviewer role" in javascript
    assert "Create manager role" in javascript
    assert "SC Companion creates or reuses the Award Manager role." in javascript
    assert "Members submit proof with <code>/rep submit</code>" in javascript
    assert "Only this role, server administrators, and SC Companion can see applications." in javascript
    assert "Private application queue configured." in javascript
    assert "reputation-reviewer-row" in javascript
    assert "Or choose an existing role" in javascript
    assert 'data-enabled="${settings.enabled ? "true" : "false"}"' in javascript
    assert "form.elements.enabled.checked" not in javascript.split("async function saveReputationSettings", 1)[1].split("async function editDashboardAward", 1)[0]
    assert "form.elements.enabled.checked" not in javascript.split("async function saveAwardSettings", 1)[1].split("async function createDashboardAward", 1)[0]
    assert "/awards/channel" in javascript
    assert "data-award-feature-enabled" in javascript
    assert 'enabled: awardFeature.querySelector("[data-award-feature-enabled]").checked' in javascript
    assert "Create an active award first." in javascript
    assert '<span>Award title <b aria-hidden="true">*</b></span><input name="title"' in javascript
    assert 'class="award-create-layout"' in javascript
    assert "data-award-description-count" in javascript
    assert "data-award-create-cancel" in javascript
    assert 'award_type: requirements.length ? "tracker" : "custom"' in javascript
    assert "Automatically grant when complete" not in javascript
    assert "Members submit progress in Discord with <code>/award report</code>." in javascript
    assert "form.elements.auto_grant" not in javascript
    assert 'data-award-review' in javascript
    assert 'data-bot-management-tab="${key}"' in javascript
    assert '["setup", "Setup"]' in javascript
    assert '["features", "Features"]' in javascript
    assert '["channels", "Channel Management"]' in javascript
    assert '[["awards", "Awards"]]' in javascript


def test_reputation_submissions_use_a_private_application_queue() -> None:
    provision_source = inspect.getsource(web.create_reputation_channels)

    assert '("rep-submissions", 0,' in provision_source
    assert '"deny": str(1 << 10)' in provision_source
    assert 'str(reviewer_role_id)' in provision_source
    assert 'settings["submission_channel_id"]' in provision_source
    assert 'settings.pop("submission_forum_id", None)' in provision_source
    assert '"rep-submissions-archive"' in provision_source
    assert '"How to submit reputation progress"' in provision_source
    assert "/rep submit" in provision_source
    assert 'item["name"] == "rep-progress"' in provision_source
    assert '"name": "activity"' in provision_source
    assert 'settings["activity_channel_id"]' in provision_source
    assert '"SC Companion activity and reputation"' in provision_source


def test_bot_repairs_legacy_reputation_forum_on_startup() -> None:
    repair_source = inspect.getsource(GameAssistBot.ensure_reputation_submission_channels)
    activity_source = inspect.getsource(GameAssistBot.ensure_activity_progress_channel)
    ready_source = inspect.getsource(GameAssistBot.on_ready)
    loop_source = inspect.getsource(GameAssistBot._guild_sync_loop)

    assert 'guild.forums if channel.name == "rep-submissions"' in repair_source
    assert 'name="rep-submissions-archive"' in repair_source
    assert 'await guild.create_text_channel(' in repair_source
    assert 'discord.PermissionOverwrite(view_channel=False)' in repair_source
    assert 'title="How to submit reputation progress"' in repair_source
    assert 'item.name == "rep-progress"' in repair_source
    assert 'name="activity"' in repair_source
    assert 'category=main_category' in repair_source
    assert 'title="SC Companion activity and reputation"' in repair_source
    assert '"repair reputation submission channels"' in ready_source
    assert 'needs_reputation_channel_repair' in loop_source
    assert 'needs_activity_channel_repair' in loop_source
    assert 'await self.ensure_activity_progress_channel(guild)' in loop_source
    assert 'await self.ensure_reputation_submission_channels(guild)' in loop_source
    assert 'item.name == "rep-progress"' in activity_source
    assert 'name="activity"' in activity_source
    assert 'settings["activity_channel_id"]' in activity_source


def test_award_channel_creation_associates_the_new_channel(monkeypatch) -> None:
    async def scenario() -> None:
        cache = SimpleNamespace(
            award_settings=AsyncMock(return_value={
                "enabled": True, "manager_role_id": 456, "announcement_channel_id": None,
            }),
            save_award_settings=AsyncMock(),
        )
        monkeypatch.setattr(web, "state", lambda: SimpleNamespace(cache=cache))
        monkeypatch.setattr(web, "_award_dashboard_manager", AsyncMock(return_value={"id": 123}))
        monkeypatch.setattr(web, "_discord_guild_channels", AsyncMock(return_value=[]))

        async def discord_response(method, path, *, bot_token, json_payload):
            del method, path, bot_token
            if json_payload["type"] == 4:
                return {"id": "800", "name": json_payload["name"], "type": 4}
            ids = {
                "award-list-criteria": "902", "award-announcements": "904",
            }
            return {"id": ids[json_payload["name"]], "name": json_payload["name"],
                    "type": 0, "parent_id": "800"}

        discord_api = AsyncMock(side_effect=discord_response)
        monkeypatch.setattr(web, "_discord_api", discord_api)
        monkeypatch.setattr(web, "_public_bot_token", lambda: "public-token")

        result = await web.create_award_announcement_channel(
            123, SimpleNamespace(id=99, username="owner")
        )

        assert result["status"] == "created"
        assert result["category_id"] == "800"
        assert result["channel_id"] == "904"
        assert result["channels"] == {
            "award-list-criteria": "902", "award-announcements": "904",
        }
        assert discord_api.await_count == 3
        assert discord_api.await_args_list[0].kwargs["json_payload"] == {
            "name": "🏆 AWARDS", "type": 4,
        }
        cache.save_award_settings.assert_awaited_once_with(123, True, 456, 99, 904)

    asyncio.run(scenario())


def test_award_channel_repair_deletes_only_retired_channels_in_awards_category(monkeypatch) -> None:
    async def scenario() -> None:
        cache = SimpleNamespace(
            award_settings=AsyncMock(return_value={
                "enabled": True, "manager_role_id": 456, "announcement_channel_id": 904,
            }),
            save_award_settings=AsyncMock(),
        )
        channels = [
            {"id": "800", "name": "🏆 AWARDS", "type": 4},
            {"id": "901", "name": "award-guidelines", "type": 0, "parent_id": "800"},
            {"id": "903", "name": "award-progress-tracker", "type": 0, "parent_id": "800"},
            {"id": "902", "name": "award-list-criteria", "type": 0, "parent_id": "800"},
            {"id": "904", "name": "award-announcements", "type": 0, "parent_id": "800"},
            {"id": "999", "name": "award-guidelines", "type": 0, "parent_id": "700"},
        ]
        monkeypatch.setattr(web, "state", lambda: SimpleNamespace(cache=cache))
        monkeypatch.setattr(web, "_award_dashboard_manager", AsyncMock(return_value={"id": 123}))
        monkeypatch.setattr(web, "_discord_guild_channels", AsyncMock(return_value=channels))
        discord_api = AsyncMock(return_value={})
        monkeypatch.setattr(web, "_discord_api", discord_api)
        monkeypatch.setattr(web, "_public_bot_token", lambda: "public-token")

        result = await web.create_award_announcement_channel(
            123, SimpleNamespace(id=99, username="owner")
        )

        assert result["status"] == "associated"
        deleted_paths = [
            call.args[1] for call in discord_api.await_args_list if call.args[0] == "DELETE"
        ]
        assert deleted_paths == ["/channels/901", "/channels/903"]
        cache.save_award_settings.assert_awaited_once_with(123, True, 456, 99, 904)

    asyncio.run(scenario())


def test_award_settings_can_create_and_assign_a_manager_role(monkeypatch) -> None:
    async def scenario() -> None:
        cache = SimpleNamespace(save_award_settings=AsyncMock())
        monkeypatch.setattr(web, "state", lambda: SimpleNamespace(cache=cache))
        monkeypatch.setattr(web, "_award_dashboard_manager", AsyncMock(return_value={"id": 123}))
        monkeypatch.setattr(web, "_discord_guild_roles", AsyncMock(return_value=[]))
        monkeypatch.setattr(web, "_discord_guild_channels", AsyncMock(return_value=[]))
        discord_api = AsyncMock(return_value={"id": "456", "name": "Award Manager", "managed": False})
        monkeypatch.setattr(web, "_discord_api", discord_api)
        monkeypatch.setattr(web, "_public_bot_token", lambda: "public-token")

        result = await web.save_award_dashboard_settings(
            123,
            web.AwardSettingsRequest(
                enabled=True,
                announcement_channel_id=None,
                auto_create_role=True,
            ),
            SimpleNamespace(id=99, username="owner"),
        )

        assert result["manager_role_id"] == "456"
        discord_api.assert_awaited_once_with(
            "POST", "/guilds/123/roles", bot_token="public-token",
            json_payload={"name": "Award Manager", "mentionable": True},
        )
        cache.save_award_settings.assert_awaited_once_with(123, True, 456, 99, None)

    asyncio.run(scenario())


def test_discord_ids_are_sent_to_browsers_without_number_rounding() -> None:
    discord_id = 1533026212463775754
    assert web._snowflake(discord_id) == "1533026212463775754"


def test_existing_primary_mining_routes_are_discovered() -> None:
    routes = web._discover_existing_routes([
        {"id": 1001, "name": "mining-tools", "type": 0},
        {"id": 1002, "name": "industry-operations", "type": 0},
    ])
    mining = routes["mining_tools"]
    assert {route["channel_name"] for route in mining} == {"mining-tools", "industry-operations"}
    assert next(route for route in mining if route["command"] == "mining")["channel_id"] == "1001"


def test_management_server_list_only_returns_installed_servers(monkeypatch) -> None:
    async def scenario() -> None:
        cache = SimpleNamespace(user_managed_guilds=AsyncMock(return_value=[
            {"id": 101, "name": "Installed", "icon_url": None},
            {"id": 202, "name": "Not Installed", "icon_url": None},
        ]))
        monkeypatch.setattr(web, "state", lambda: SimpleNamespace(cache=cache))
        monkeypatch.setattr(web, "_discord_bot_guild_ids", AsyncMock(return_value={101}))
        result = await web.manageable_bot_guilds(SimpleNamespace(id=99))
        assert [guild["name"] for guild in result] == ["Installed"]
        assert result[0]["bot_installed"] is True

    asyncio.run(scenario())


def test_public_bot_stats_only_exposes_the_active_server_count(monkeypatch) -> None:
    async def scenario() -> None:
        cache = SimpleNamespace(guild_installation_stats=AsyncMock(return_value={
            "active_servers": 7, "configured_servers": 6, "visible_members": 900,
        }))
        monkeypatch.setattr(web, "state", lambda: SimpleNamespace(cache=cache))
        assert await web.public_bot_stats() == {"active_servers": 7}

    asyncio.run(scenario())


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
