import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.bot import GameAssistBot


def test_peep_welcomes_screened_human_after_assigning_visitor() -> None:
    bot = SimpleNamespace(
        settings=SimpleNamespace(runtime_profile="peep", discord_guild_id=123),
        _assign_new_visitor=AsyncMock(),
        _send_member_welcome=AsyncMock(),
        sync_total_members_channel=AsyncMock(),
    )
    member = SimpleNamespace(bot=False, pending=False, guild=SimpleNamespace(id=123))

    asyncio.run(GameAssistBot.on_member_join(bot, member))

    bot._assign_new_visitor.assert_awaited_once_with(member)
    bot._send_member_welcome.assert_awaited_once_with(member)
    bot.sync_total_members_channel.assert_awaited_once_with(member.guild)


def test_public_sc_companion_does_not_send_peep_welcome() -> None:
    bot = SimpleNamespace(
        settings=SimpleNamespace(runtime_profile="public", discord_guild_id=123),
        _assign_new_visitor=AsyncMock(),
        _send_member_welcome=AsyncMock(),
        sync_total_members_channel=AsyncMock(),
    )
    member = SimpleNamespace(bot=False, pending=False, guild=SimpleNamespace(id=123))

    asyncio.run(GameAssistBot.on_member_join(bot, member))

    bot._assign_new_visitor.assert_not_awaited()
    bot._send_member_welcome.assert_not_awaited()
    bot.sync_total_members_channel.assert_not_awaited()


def test_peep_welcome_mentions_member_and_includes_avatar_and_member_number() -> None:
    channel = SimpleNamespace(name="welcome", id=456, send=AsyncMock())
    guild = SimpleNamespace(text_channels=[channel], member_count=75, members=[])
    member = SimpleNamespace(
        id=789,
        mention="<@789>",
        display_name="New Pilot",
        display_avatar=SimpleNamespace(url="https://cdn.discordapp.com/avatar.png"),
        guild=guild,
    )

    asyncio.run(GameAssistBot._send_member_welcome(SimpleNamespace(), member))

    channel.send.assert_awaited_once()
    message = channel.send.await_args.kwargs
    assert message["content"] == "Welcome aboard, <@789>!"
    assert message["embed"].title == "New Pilot just joined the crew"
    assert message["embed"].thumbnail.url == "https://cdn.discordapp.com/avatar.png"
    assert message["embed"].footer.text == "Crew member #75"
    assert message["allowed_mentions"].users is True


def test_peep_updates_existing_total_members_voice_channel() -> None:
    channel = SimpleNamespace(name="Total Members: 74", id=456, edit=AsyncMock())
    guild = SimpleNamespace(id=123, voice_channels=[channel], member_count=75, members=[])
    bot = SimpleNamespace(settings=SimpleNamespace(runtime_profile="peep", discord_guild_id=123))

    asyncio.run(GameAssistBot.sync_total_members_channel(bot, guild))

    channel.edit.assert_awaited_once_with(
        name="Total Members: 75",
        reason="Keep Peep's total member count current",
    )


def test_sc_companion_does_not_update_peep_member_counter() -> None:
    channel = SimpleNamespace(name="Total Members: 74", id=456, edit=AsyncMock())
    guild = SimpleNamespace(id=123, voice_channels=[channel], member_count=75, members=[])
    bot = SimpleNamespace(settings=SimpleNamespace(runtime_profile="public", discord_guild_id=123))

    asyncio.run(GameAssistBot.sync_total_members_channel(bot, guild))

    channel.edit.assert_not_awaited()


def test_peep_recreates_missing_total_members_voice_channel() -> None:
    default_role = object()
    guild = SimpleNamespace(
        id=123,
        voice_channels=[],
        member_count=75,
        members=[],
        default_role=default_role,
        create_voice_channel=AsyncMock(),
    )
    bot = SimpleNamespace(settings=SimpleNamespace(runtime_profile="peep", discord_guild_id=123))

    asyncio.run(GameAssistBot.sync_total_members_channel(bot, guild))

    guild.create_voice_channel.assert_awaited_once()
    creation = guild.create_voice_channel.await_args.kwargs
    assert creation["name"] == "Total Members: 75"
    assert creation["position"] == 2
    assert creation["overwrites"][default_role].connect is False
