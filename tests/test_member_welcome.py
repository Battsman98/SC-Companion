import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.bot import GameAssistBot


def test_peep_welcomes_screened_human_after_assigning_visitor() -> None:
    bot = SimpleNamespace(
        settings=SimpleNamespace(runtime_profile="peep", discord_guild_id=123),
        _assign_new_visitor=AsyncMock(),
        _send_member_welcome=AsyncMock(),
    )
    member = SimpleNamespace(bot=False, pending=False, guild=SimpleNamespace(id=123))

    asyncio.run(GameAssistBot.on_member_join(bot, member))

    bot._assign_new_visitor.assert_awaited_once_with(member)
    bot._send_member_welcome.assert_awaited_once_with(member)


def test_public_sc_companion_does_not_send_peep_welcome() -> None:
    bot = SimpleNamespace(
        settings=SimpleNamespace(runtime_profile="public", discord_guild_id=123),
        _assign_new_visitor=AsyncMock(),
        _send_member_welcome=AsyncMock(),
    )
    member = SimpleNamespace(bot=False, pending=False, guild=SimpleNamespace(id=123))

    asyncio.run(GameAssistBot.on_member_join(bot, member))

    bot._assign_new_visitor.assert_not_awaited()
    bot._send_member_welcome.assert_not_awaited()


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
