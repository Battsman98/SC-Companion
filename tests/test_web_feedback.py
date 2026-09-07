from io import BytesIO
from pathlib import Path
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
from src.web import _discord_message_description, _feedback_embed, _order_feedback_embed_fields, _provided_feedback_images
from src.web import _add_feedback_attachments_to_embed, feedback_ticket_messages, update_feedback_ticket_status
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


def test_resolving_a_website_ticket_applies_completed_tag_and_archives() -> None:
    source = inspect.getsource(update_feedback_ticket_status)

    assert "await _apply_feedback_ticket_state(thread, payload.status)" in source
    assert "feedback_mirror_for_central_thread(thread_id)" in source
    assert "origin_thread, payload.status, bot_token=_public_bot_token()" in source


def test_mirrored_feedback_displays_image_attachments() -> None:
    embed = {"fields": []}
    _add_feedback_attachments_to_embed(embed, [{
        "filename": "broken-screen.png",
        "url": "https://cdn.discordapp.com/attachments/example/broken-screen.png",
        "content_type": "image/png",
    }])

    assert embed["image"]["url"].endswith("broken-screen.png")
    assert embed["fields"][-1]["name"] == "Attachments"
    assert "[broken-screen.png]" in embed["fields"][-1]["value"]


def test_embedded_ticket_fields_become_the_mirrored_description() -> None:
    description = _discord_message_description({"content": "", "embeds": [{
        "title": "Issue",
        "description": "The button did not respond.",
        "fields": [{"name": "Steps", "value": "Open the panel and click Trade."}],
    }]})

    assert "The button did not respond." in description
    assert "**Steps**" in description
    assert "Open the panel and click Trade." in description

    bot_source = inspect.getsource(GameAssistBot.mirror_feedback_thread)
    assert "self.feedback_message_description(report_message)" in bot_source


def test_mirrored_ticket_fields_use_requested_order() -> None:
    embed = {
        "description": "Body first",
        "fields": [
            {"name": "Reported by", "value": "Pilot"},
            {"name": "Attachments", "value": "image.png"},
            {"name": "Origin", "value": "Test server"},
        ],
    }

    _order_feedback_embed_fields(embed, "Body last")

    assert "description" not in embed
    assert [field["name"] for field in embed["fields"]] == [
        "Origin", "Reported by", "Attachments", "Body",
    ]
    assert embed["fields"][-1]["value"] == "Body last"


def test_bot_copies_mirrored_images_into_peep() -> None:
    mirror_source = inspect.getsource(GameAssistBot.mirror_feedback_thread)
    sync_source = inspect.getsource(GameAssistBot.sync_mirrored_feedback_attachments)

    assert "await image_attachment.to_file(use_cached=True)" in mirror_source
    assert 'mirror_embed.set_image(url=f"attachment://{mirror_file.filename}")' in mirror_source
    assert "files=mirror_files" in mirror_source
    assert "await image.to_file(use_cached=True)" in sync_source
    assert "attachments=[mirror_file]" in sync_source
    assert "except discord.HTTPException" in mirror_source
    assert "except discord.HTTPException" in sync_source


def test_reporter_updates_are_forwarded_to_the_main_ticket() -> None:
    source = inspect.getsource(GameAssistBot.on_message)

    assert "feedback_mirror_for_origin_thread(message.channel.id)" in source
    assert 'title=f"Reporter update from {message.author.display_name}"' in source
    assert "self.add_feedback_attachments(embed, message.attachments)" in source
    assert "await image_attachment.to_file(use_cached=True)" in source
    assert "files=files" in source


def test_website_reads_the_original_mirrored_ticket_conversation() -> None:
    source = inspect.getsource(feedback_ticket_messages)

    assert "feedback_mirror_for_central_thread(thread_id)" in source
    assert "conversation_thread_id = origin_thread_id or thread_id" in source
    assert 'central_starter = await _discord_api("GET", f"/channels/{thread_id}/messages/{thread_id}")' in source
    assert 'oldest["attachments"]' in source


def test_edited_ticket_messages_refresh_mirrored_images() -> None:
    source = inspect.getsource(GameAssistBot.on_raw_message_edit)

    assert "feedback_mirror_for_origin_thread(payload.channel_id)" in source
    assert "await self.sync_mirrored_feedback_attachments" in source


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
    configure_source = inspect.getsource(GameAssistBot.configure_feedback_forum)
    assert "await self.sync_feedback_template(forum)" in source
    assert "forum.flags.require_tag" in configure_source
    assert "require_tag=True" in configure_source

    embed = build_feedback_template_embed()
    assert embed.title == "Example: Guide button does not display the selected information"
    assert embed.footer.text == "This is an example. Create a new forum post for your own report."


def test_thread_create_handles_feedback_and_marketplace_events() -> None:
    source = inspect.getsource(GameAssistBot.on_thread_create)

    assert "await asyncio.sleep(1)" in source
    assert "await self.mirror_feedback_thread(thread)" in source
    assert "await self.enrich_trading_post(thread)" in source


def test_website_feedback_sync_reads_origins_as_sc_companion() -> None:
    import src.web as web

    sync_source = inspect.getsource(web._sync_installed_server_feedback)
    message_source = inspect.getsource(web.feedback_ticket_messages)
    status_source = inspect.getsource(web.update_feedback_ticket_status)
    submission_source = inspect.getsource(web.submit_feedback)
    assert "public_token = _public_bot_token()" in sync_source
    assert "bot_token=public_token" in sync_source
    assert 'f"/channels/{thread_id}/messages?limit=50"' in sync_source
    assert "_discord_message_description(starter)" in sync_source
    assert "_order_feedback_embed_fields(embed, _discord_message_description(report_message))" in inspect.getsource(
        web._sync_mirrored_feedback_attachments
    )
    assert "bot_token=_public_bot_token() if origin_thread_id else None" in message_source
    assert "bot_token=_public_bot_token()" in status_source
    assert 'f"Bot {_public_bot_token()}"' in submission_source


def test_discord_bot_requests_message_content_for_feedback_mirroring() -> None:
    source = inspect.getsource(GameAssistBot.__init__)

    assert "intents.message_content = True" in source


def test_forwarded_staff_messages_do_not_repeat_official_response_label() -> None:
    import src.web as web

    bot_source = inspect.getsource(GameAssistBot.on_message)
    website_source = inspect.getsource(web._send_discord_channel_message)
    assert "Official SC Companion response" not in bot_source
    assert "Official SC Companion response" not in website_source


def test_discord_inbox_refreshes_every_minute_while_open() -> None:
    javascript = (Path(__file__).parents[1] / "web" / "app.js").read_text(encoding="utf-8")

    assert "window.setInterval(refreshDiscordConsole, 60_000)" in javascript
    assert "window.clearInterval(ticketRefreshTimer)" in javascript


def test_public_sc_companion_can_publish_examples_in_the_support_guild() -> None:
    ready_source = inspect.getsource(GameAssistBot.on_ready)
    join_source = inspect.getsource(GameAssistBot.on_guild_join)
    loop_source = inspect.getsource(GameAssistBot._guild_sync_loop)
    feedback_source = inspect.getsource(GameAssistBot.ensure_guild_feedback_forum)
    primary_source = inspect.getsource(GameAssistBot.primary_feedback_forum)

    for source in (ready_source, join_source, loop_source, feedback_source):
        assert 'self.settings.runtime_profile != "public"' in source
    assert primary_source.index("configured = guild.get_channel") < primary_source.index(
        'tracked = guild.get_channel'
    )
    about_source = inspect.getsource(GameAssistBot._ensure_about_panel)
    category_source = inspect.getsource(GameAssistBot.sync_sc_companion_category_examples)
    assert 'self.settings.runtime_profile == "public"' in about_source
    assert "category.text_channels" in category_source
    assert "build_visitor_command_example_embeds" in category_source
    assert "sc-companion-example" in category_source


def test_visitor_hub_includes_public_bot_and_social_channels() -> None:
    assert VISITOR_CATEGORY_NAME == "SC Companion Hub"
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


def test_replaced_hub_channels_are_deleted_and_manual_order_is_not_restored() -> None:
    assert VISITOR_ARCHIVE_CHANNEL_NAMES.isdisjoint(VISITOR_CHANNEL_SPECS)
    cleanup_source = inspect.getsource(GameAssistBot._delete_replaced_visitor_channels)
    assert "Permanently remove Bot Hub Archive" in cleanup_source
    assert "create_category" not in cleanup_source
    assert ".edit(" not in cleanup_source
    source = inspect.getsource(GameAssistBot.on_guild_channel_update)
    assert 'for attribute in ("name", "category_id", "topic", "overwrites")' in source
    assert "position" not in source.casefold()


def test_membership_reviews_are_kept_in_the_audit_log_category() -> None:
    source = inspect.getsource(GameAssistBot._ensure_membership_applications)
    assert "AUDIT_LOG_CATEGORY_ID" in source
    assert "category=audit_category" in source
    assert "review_channel.category_id != audit_category.id" in source
    protection_source = inspect.getsource(GameAssistBot._is_discord_bot_hub_channel)
    assert "APPLICATION_REVIEW_CHANNEL_NAME" in protection_source


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
