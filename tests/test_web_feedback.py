from io import BytesIO
import inspect

from fastapi import UploadFile

from src.bot import (
    BOT_MANAGER_ROLE_NAME,
    FEEDBACK_FORUM_DEFAULT_REACTION,
    FEEDBACK_FORUM_TAGS,
    GameAssistBot,
    VISITOR_CATEGORY_NAME,
    VISITOR_CHANNEL_SPECS,
    VISITOR_ARCHIVE_CHANNEL_NAMES,
    VISITOR_COMMAND_CHANNELS,
    VISITOR_REMOVED_CHANNEL_NAMES,
    build_feedback_template_embed,
    build_visitor_channel_directory_embed,
    build_visitor_command_example_embeds,
)
from src.web import _feedback_embed, _provided_feedback_images
from src.web_auth import WebUser


def test_feedback_embed_has_structured_report_fields() -> None:
    user = WebUser(
        id=42,
        username="pilot",
        display_name="Pilot",
        avatar_url="https://cdn.example/avatar.png",
        roles=(),
        guild_permissions=0,
        can_manage_changes=False,
        can_manage_admin=False,
    )

    embed = _feedback_embed(
        report_type="issue",
        details="The button did not respond.",
        expected_action="Open the selected panel.",
        steps="Open Overview and select Trade.",
        recommendations="Add a visible selected state.",
        user=user,
    )

    fields = {field["name"]: field["value"] for field in embed["fields"]}
    assert fields["Report type"] == "Issue / Bug"
    assert fields["Reported by"] == "Pilot (`42`)"
    assert fields["Issue / Feedback"] == "The button did not respond."
    assert fields["Expected action or result"] == "Open the selected panel."
    assert fields["Steps to reproduce"] == "Open Overview and select Trade."
    assert fields["Improvement recommendations"] == "Add a visible selected state."
    assert embed["author"]["icon_url"] == "https://cdn.example/avatar.png"


def test_feedback_submission_ignores_empty_optional_file_placeholder() -> None:
    empty_upload = UploadFile(filename="", file=BytesIO())

    # Keep this regression focused on the browser's empty multipart placeholder:
    # it must not count as an attachment or fail MIME validation.
    provided = _provided_feedback_images([empty_upload])

    assert provided == []


def test_bot_feedback_template_gives_users_a_complete_example() -> None:
    embed = build_feedback_template_embed()
    fields = {field.name: field.value for field in embed.fields}

    assert embed.title == "Example: Guide button does not display the selected information"
    assert fields["Report type"] == "Issue / Bug"
    assert "Getting Started" in fields["Issue / Feedback"]
    assert "Trade guide" in fields["Expected action or result"]
    assert "screenshots" in fields["Helpful attachments"]


def test_shared_feedback_forums_copy_the_main_discord_tags() -> None:
    assert FEEDBACK_FORUM_TAGS == (
        ("completed", True),
        ("in-progress", True),
        ("bug", False),
        ("request", False),
    )
    assert FEEDBACK_FORUM_DEFAULT_REACTION == "👍"


def test_main_about_channel_only_keeps_welcome_and_directory() -> None:
    source = inspect.getsource(GameAssistBot._sync_commands_reference_channel)
    assert "directory_only" in source
    assert 'getattr(channel, "name", None) == "about-the-bot"' in source
    assert 'title == "Example /lookup Response"' in source
    assert 'title.startswith("Discord Bot Commands - ")' in source

    directory = build_visitor_channel_directory_embed({"general-chat": 123, "ship-search": 456})
    assert "/lookup" not in directory.description
    assert "/status" not in directory.description
    assert "<#456>: /ship" in directory.description


def test_shared_feedback_forums_publish_the_main_example_post() -> None:
    source = inspect.getsource(GameAssistBot.ensure_guild_feedback_forum)
    assert "await self.sync_feedback_template(forum)" in source

    embed = build_feedback_template_embed()
    assert embed.title == "Example: Guide button does not display the selected information"
    assert embed.footer.text == "This is an example. Create a new forum post for your own report."


def test_visitor_hub_includes_public_bot_and_social_channels() -> None:
    assert VISITOR_CATEGORY_NAME == "Discord Bot Hub"
    assert "bot-commands" not in VISITOR_CHANNEL_SPECS
    assert VISITOR_REMOVED_CHANNEL_NAMES == {"bot-start-here", "bot-status"}
    assert VISITOR_REMOVED_CHANNEL_NAMES.isdisjoint(VISITOR_CHANNEL_SPECS)
    assert {"blueprints", "missions-wikelo", "timers"} <= set(VISITOR_CHANNEL_SPECS)
    assert {"bot-commands", "industry-operations", "blueprints-and-missions",
            "executive-hangar-status", "contested-zone-timers"} == VISITOR_ARCHIVE_CHANNEL_NAMES
    assert VISITOR_CHANNEL_SPECS["general-chat"] == "text"
    assert VISITOR_CHANNEL_SPECS["visitor-lounge"] == "voice"
    assert VISITOR_COMMAND_CHANNELS["ship"] == "ship-search"
    assert "lookup" not in VISITOR_COMMAND_CHANNELS
    assert "status" not in VISITOR_COMMAND_CHANNELS
    assert VISITOR_COMMAND_CHANNELS["trade routing"] == "trade-tools"
    assert VISITOR_COMMAND_CHANNELS["miningadd"] == "mining-tools"
    assert not any(name.startswith("admin") or name.startswith("audit") for name in VISITOR_COMMAND_CHANNELS)


def test_archived_hub_channels_and_manual_order_are_not_restored() -> None:
    assert VISITOR_ARCHIVE_CHANNEL_NAMES.isdisjoint(VISITOR_CHANNEL_SPECS)
    source = inspect.getsource(GameAssistBot.on_guild_channel_update)
    assert 'for attribute in ("name", "category_id", "topic", "overwrites")' in source
    assert "position" not in source.casefold()


def test_membership_reviews_are_kept_in_the_audit_log_category() -> None:
    source = inspect.getsource(GameAssistBot._ensure_membership_applications)
    assert "AUDIT_LOG_CATEGORY_ID" in source
    assert "category=audit_category" in source
    assert "review_channel.category_id != audit_category.id" in source


def test_every_visitor_command_channel_has_a_response_example() -> None:
    examples = build_visitor_command_example_embeds()

    assert set(VISITOR_COMMAND_CHANNELS.values()) <= set(examples)
    assert all(embed.title and "Example" in embed.title for embed in examples.values())

    blueprint = examples["blueprints"]
    assert "/blueprint name: NDB-28 Repeater" in blueprint.description
    assert "select `name`" in blueprint.description
    assert "Titanium=750, Gold=820, Lindinium=910" in blueprint.description
    assert "qualities" in blueprint.description
    assert "/blueprint query:" not in blueprint.description
    missions = examples["missions-wikelo"]
    assert "/wikelo" in "\n".join(field.value for field in missions.fields)
    assert "reputation awarded" in "\n".join(field.value for field in missions.fields)

    trade = examples["trade-tools"]
    assert "investment: 500000" in trade.description
    assert "budget:" not in trade.description

    item = examples["item-locator"]
    assert "/item search name: FS-9 LMG" in item.description


def test_bot_manager_role_name_is_stable_for_discord_and_website_access() -> None:
    assert BOT_MANAGER_ROLE_NAME == "Bot Manager"
