import asyncio
import inspect

from src.bot import (
    AWARD_CITATION_LIMIT,
    AWARD_DESCRIPTION_LIMIT,
    AWARD_MAX_REQUIREMENTS,
    AWARD_NAME_LIMIT,
    AWARD_TASK_LIMIT,
    GameAssistBot,
    _award_requirements,
    _award_text_error,
    award_group,
)
from src.cache import SQLiteCache


def test_award_requirements_are_named_deduplicated_and_limited() -> None:
    assert _award_requirements("Bounty contract; Cargo run | bounty CONTRACT\nMedical rescue") == [
        "Bounty contract", "Cargo run", "Medical rescue",
    ]
    assert _award_text_error("A" * (AWARD_NAME_LIMIT + 1), "Description", [])
    assert _award_text_error("Award", "D" * (AWARD_DESCRIPTION_LIMIT + 1), [])
    assert _award_text_error("Award", "Description", ["T"] * (AWARD_MAX_REQUIREMENTS + 1))
    assert _award_text_error("Award", "Description", ["T" * (AWARD_TASK_LIMIT + 1)])
    assert AWARD_CITATION_LIMIT == 280


def test_award_commands_are_only_registered_to_the_configured_testing_guild() -> None:
    setup_source = inspect.getsource(GameAssistBot.setup_hook)
    assert "self.tree.add_command(award_group, guild=guild)" in setup_source
    assert "award_group" not in setup_source.split("public_commands = (", 1)[1].split(")", 1)[0]
    assert {command.name for command in award_group.commands} == {
        "configure", "create", "edit", "report", "queue", "review", "grant", "list", "profile",
    }


def test_tracked_award_report_review_and_custom_grant_round_trip(tmp_path) -> None:
    async def scenario() -> None:
        cache = await SQLiteCache.create(str(tmp_path / "awards.sqlite3"))
        guild_id = 123

        assert await cache.award_settings(guild_id) == {
            "enabled": False, "manager_role_id": None, "announcement_channel_id": None,
            "configured_by": None, "updated_at": None,
        }
        await cache.save_award_settings(guild_id, True, 456, 1, 789)
        settings = await cache.award_settings(guild_id)
        assert settings["enabled"] is True
        assert settings["manager_role_id"] == 456
        assert settings["announcement_channel_id"] == 789

        tracker_id = await cache.create_award_definition(
            guild_id, "Contract Ace", "Complete the contract set.", "tracker",
            ["Bounty contract", "Cargo contract"], 1, auto_grant=True,
        )
        custom_id = await cache.create_award_definition(
            guild_id, "Bro Award", "Recognizes a great crewmate.", "custom", [], 1,
        )
        assert [item["name"] for item in await cache.award_definitions(guild_id)] == ["Bro Award", "Contract Ace"]

        first = await cache.submit_award_report(
            guild_id, tracker_id, 99, "Pilot", "Bounty contract", "Completed with the org on 2956-09-16.",
        )
        second = await cache.submit_award_report(
            guild_id, tracker_id, 99, "Pilot", "Cargo contract", "Delivered the full shipment.",
        )
        assert [item["id"] for item in await cache.pending_award_reports(guild_id)] == [first, second]

        reviewed = await cache.review_award_report(guild_id, first, "approved", 1)
        assert reviewed is not None and reviewed["award_id"] == tracker_id
        assert await cache.approved_award_tasks(guild_id, tracker_id, 99) == {"bounty contract"}
        assert await cache.review_award_report(guild_id, second, "approved", 1)
        assert await cache.approved_award_tasks(guild_id, tracker_id, 99) == {
            "bounty contract", "cargo contract",
        }

        assert await cache.grant_award(guild_id, tracker_id, 99, "Pilot", "All contracts approved.", 1)
        assert not await cache.grant_award(guild_id, tracker_id, 99, "Pilot", "Duplicate.", 1)
        assert await cache.grant_award(guild_id, custom_id, 99, "Pilot", "Always helps the crew.", 1)
        earned = await cache.member_awards(guild_id, 99)
        assert {item["name"] for item in earned} == {"Contract Ace", "Bro Award"}

        tracker = await cache.award_definition(guild_id, tracker_id)
        assert tracker is not None
        assert tracker["auto_grant"] is True
        assert await cache.update_award_definition(
            guild_id, tracker_id, name="Contract Master", description="Updated description.",
            requirements=tracker["requirements"], active=False, auto_grant=False,
        )
        assert [item["name"] for item in await cache.award_definitions(guild_id)] == ["Bro Award"]
        assert (await cache.award_definition(guild_id, tracker_id))["active"] is False
        assert (await cache.award_definition(guild_id, tracker_id))["auto_grant"] is False

        await cache.purge_guild_data(guild_id)
        assert await cache.award_definitions(guild_id, active_only=False) == []
        assert await cache.member_awards(guild_id, 99) == []
        assert (await cache.award_settings(guild_id))["enabled"] is False
        await cache.close()

    asyncio.run(scenario())
