import asyncio
import inspect

from src.bot import (
    AWARD_CITATION_LIMIT,
    AWARD_DESCRIPTION_LIMIT,
    AWARD_MAX_REQUIREMENTS,
    AWARD_NAME_LIMIT,
    AWARD_TASK_LIMIT,
    AWARDS_PER_DISCORD_PAGE,
    AwardChoiceSelect,
    AwardCreationModal,
    AwardNominationView,
    AwardPanelView,
    AwardRecommendationModal,
    AwardReportSelect,
    AwardReviewView,
    AwardRoleColorSelect,
    AwardRoleColorView,
    GameAssistBot,
    _award_list_embed,
    _award_requirements,
    _award_text_error,
    award_group,
)
from src.cache import SQLiteCache


def test_award_requirements_are_named_deduplicated_and_limited() -> None:
    assert _award_requirements("Community event; Assist a member | community EVENT\nTraining session") == [
        "Community event", "Assist a member", "Training session",
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


def test_award_panel_is_persistent_and_lists_awards_in_pages() -> None:
    view = AwardPanelView()
    assert view.timeout is None
    assert {item.custom_id for item in view.children} == {
        "sc-companion:awards:browse",
        "sc-companion:awards:submit",
        "sc-companion:awards:create",
        "sc-companion:awards:review",
    }
    awards = [
        {"id": index, "name": f"Award {index}", "description": "Recognition",
         "award_type": "custom", "requirements": []}
        for index in range(1, AWARDS_PER_DISCORD_PAGE + 2)
    ]
    first = _award_list_embed(awards, 0)
    second = _award_list_embed(awards, 1)
    assert len(first.fields) == AWARDS_PER_DISCORD_PAGE
    assert len(second.fields) == 1
    assert "Page 1 of 2" in (first.footer.text or "")
    assert "Page 2 of 2" in (second.footer.text or "")


def test_award_nomination_uses_member_search_award_descriptions_and_pages() -> None:
    awards = [
        {"id": index, "name": f"Award {index}", "description": f"Description {index}"}
        for index in range(1, 27)
    ]
    view = AwardNominationView(awards, user_id=99)
    member_select = next(item for item in view.children if item.__class__.__name__ == "AwardNomineeSelect")
    award_select = next(item for item in view.children if isinstance(item, AwardChoiceSelect))

    assert member_select.placeholder == "Who is the award for? Add up to 25 at a time"
    assert member_select.max_values == 25
    assert award_select.placeholder == "What award do you want to submit?"
    assert award_select.max_values == 1
    assert award_select.options[0].label == "Award 1"
    assert award_select.options[0].description == "Description 1"
    assert len(award_select.options) == 25
    assert view.next.disabled is False

    modal = AwardRecommendationModal(awards[0], [object()])
    assert modal.reason.label == "Why do you recommend this award?"

    colors = AwardRoleColorView(user_id=99)
    color_select = next(item for item in colors.children if isinstance(item, AwardRoleColorSelect))
    assert color_select.placeholder == "Choose the Discord role color"
    assert {option.label for option in color_select.options} >= {"Gold", "Red", "Green", "Blue", "Purple"}
    assert all(getattr(item, "label", None) != "Continue" for item in colors.children)

    creation = AwardCreationModal(0x3498DB)
    assert creation.role_color == 0x3498DB


def test_award_review_notifications_are_role_private_and_announcements_use_description() -> None:
    from src.bot import _announce_award, _ensure_award_review_channel

    private_source = inspect.getsource(_ensure_award_review_channel)
    announcement_source = inspect.getsource(_announce_award)
    assert 'item.name == "award-review"' in private_source
    assert "view_channel=False" in private_source
    assert "manager_role: discord.PermissionOverwrite(view_channel=True" in private_source
    assert 'name="Award description"' in announcement_source


def test_award_review_panel_lists_pending_recommendations_in_pages() -> None:
    reports = [
        {
            "id": index, "award_name": "Service Award", "user_id": 1000 + index,
            "user_name": f"Pilot {index}", "citation": f"Recommendation {index}",
        }
        for index in range(1, 27)
    ]
    view = AwardReviewView(reports, manager_id=99)
    report_select = next(item for item in view.children if isinstance(item, AwardReportSelect))

    assert report_select.max_values == 1
    assert len(report_select.options) == 25
    assert report_select.options[0].label == "#1 · Service Award"
    assert report_select.options[0].description == "Pilot 1: Recommendation 1"
    assert view.next.disabled is False
    assert view.approve.disabled is True
    assert view.reject.disabled is True


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
            guild_id, "Community Contributor", "Complete the community goals.", "tracker",
            ["Community event", "Assist a member"], 1, auto_grant=True,
        )
        custom_id = await cache.create_award_definition(
            guild_id, "Outstanding Service", "Recognizes exceptional service.", "custom", [], 1,
        )
        assert [item["name"] for item in await cache.award_definitions(guild_id)] == ["Community Contributor", "Outstanding Service"]

        first = await cache.submit_award_report(
            guild_id, tracker_id, 99, "Pilot", "Community event", "Completed with the community on 2956-09-16.",
        )
        second = await cache.submit_award_report(
            guild_id, tracker_id, 99, "Pilot", "Assist a member", "Helped another community member.",
        )
        assert [item["id"] for item in await cache.pending_award_reports(guild_id)] == [first, second]

        reviewed = await cache.review_award_report(guild_id, first, "approved", 1)
        assert reviewed is not None and reviewed["award_id"] == tracker_id
        assert reviewed["task_name"] == "Community event"
        assert await cache.approved_award_tasks(guild_id, tracker_id, 99) == {"community event"}
        assert await cache.review_award_report(guild_id, second, "approved", 1)
        assert await cache.approved_award_tasks(guild_id, tracker_id, 99) == {
            "community event", "assist a member",
        }

        assert await cache.grant_award(guild_id, tracker_id, 99, "Pilot", "All contracts approved.", 1)
        assert not await cache.grant_award(guild_id, tracker_id, 99, "Pilot", "Duplicate.", 1)
        assert await cache.grant_award(guild_id, custom_id, 99, "Pilot", "Always helps the crew.", 1)
        earned = await cache.member_awards(guild_id, 99)
        assert {item["name"] for item in earned} == {"Community Contributor", "Outstanding Service"}

        assert await cache.delete_award_definition(guild_id, custom_id)
        assert await cache.award_definition(guild_id, custom_id) is None
        assert {item["name"] for item in await cache.member_awards(guild_id, 99)} == {"Community Contributor"}
        assert not await cache.delete_award_definition(guild_id, custom_id)

        tracker = await cache.award_definition(guild_id, tracker_id)
        assert tracker is not None
        assert tracker["auto_grant"] is False
        assert tracker["role_color"] == 14002510
        assert await cache.update_award_definition(
            guild_id, tracker_id, name="Contract Master", description="Updated description.",
            requirements=tracker["requirements"], active=False, auto_grant=False,
            role_color=0x123456,
        )
        assert await cache.award_definitions(guild_id) == []
        assert (await cache.award_definition(guild_id, tracker_id))["active"] is False
        assert (await cache.award_definition(guild_id, tracker_id))["auto_grant"] is False
        assert (await cache.award_definition(guild_id, tracker_id))["role_color"] == 0x123456

        await cache.purge_guild_data(guild_id)
        assert await cache.award_definitions(guild_id, active_only=False) == []
        assert await cache.member_awards(guild_id, 99) == []
        assert (await cache.award_settings(guild_id))["enabled"] is False
        await cache.close()

    asyncio.run(scenario())


def test_award_nomination_batches_allow_many_recipients_and_twenty_pending_each(tmp_path) -> None:
    async def scenario() -> None:
        cache = await SQLiteCache.create(str(tmp_path / "award-nominations.sqlite3"))
        guild_id = 123
        first_award = await cache.create_award_definition(
            guild_id, "First Award", "First description.", "custom", [], 1,
        )
        second_award = await cache.create_award_definition(
            guild_id, "Second Award", "Second description.", "custom", [], 1,
        )

        first_batch = await cache.submit_award_nominations(
            guild_id, first_award, [(99, "Pilot"), (100, "Second Pilot")], "Shared recommendation.",
        )
        assert first_batch is not None and len(first_batch) == 2
        for index in range(19):
            assert await cache.submit_award_nominations(
                guild_id, second_award, [(99, "Pilot")], f"Recommendation {index}.",
            ) is not None
        assert await cache.submit_award_nominations(
            guild_id, second_award, [(99, "Pilot")], "Twenty-first pending recommendation.",
        ) is None
        assert await cache.submit_award_nominations(
            guild_id, first_award, [(101, "Third Pilot")], "Another member remains eligible.",
        ) is not None
        await cache.close()

    asyncio.run(scenario())
