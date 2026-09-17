import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from src.bot import GameAssistBot, build_guild_command_guide_embed


def test_guild_command_guide_recovers_oldest_message_and_removes_duplicates() -> None:
    oldest = SimpleNamespace(id=10, edit=AsyncMock(), delete=AsyncMock())
    duplicate = SimpleNamespace(id=20, edit=AsyncMock(), delete=AsyncMock())
    channel = SimpleNamespace(id=99, send=AsyncMock())
    cache = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock())
    embed = build_guild_command_guide_embed(["trade_tools"])
    bot = SimpleNamespace(
        cache=cache,
        find_recent_embed_messages=AsyncMock(
            return_value={embed.title: [duplicate, oldest]}
        ),
    )

    asyncio.run(
        GameAssistBot._sync_singleton_embed(bot, channel, "guide-key", embed)
    )

    oldest.edit.assert_awaited_once_with(embed=embed)
    duplicate.delete.assert_awaited_once_with()
    channel.send.assert_not_awaited()
    cache.set.assert_awaited_once_with("guide-key", oldest.id, 315360000)


def test_guild_command_guide_creates_one_when_none_exists() -> None:
    created = SimpleNamespace(id=30)
    channel = SimpleNamespace(id=99, send=AsyncMock(return_value=created))
    cache = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock())
    embed = build_guild_command_guide_embed(["trade_tools"])
    bot = SimpleNamespace(
        cache=cache,
        find_recent_embed_messages=AsyncMock(return_value={embed.title: []}),
    )

    asyncio.run(
        GameAssistBot._sync_singleton_embed(bot, channel, "guide-key", embed)
    )

    channel.send.assert_awaited_once_with(embed=embed, silent=False)
    cache.set.assert_awaited_once_with("guide-key", created.id, 315360000)


def test_transient_fetch_failure_does_not_post_a_replacement_guide() -> None:
    response = SimpleNamespace(status=503, reason="Unavailable")
    channel = SimpleNamespace(
        id=99,
        fetch_message=AsyncMock(side_effect=discord.HTTPException(response, "temporary")),
        send=AsyncMock(),
    )
    cache = SimpleNamespace(get=AsyncMock(return_value=123), set=AsyncMock())
    embed = build_guild_command_guide_embed(["trade_tools"])
    bot = SimpleNamespace(
        cache=cache,
        find_recent_embed_messages=AsyncMock(),
    )

    asyncio.run(
        GameAssistBot._sync_singleton_embed(bot, channel, "guide-key", embed)
    )

    channel.send.assert_not_awaited()
    bot.find_recent_embed_messages.assert_not_awaited()
    cache.set.assert_not_awaited()


def test_singleton_embed_preserves_loot_send_and_edit_options() -> None:
    existing = SimpleNamespace(
        id=40,
        author=SimpleNamespace(id=7),
        embeds=[SimpleNamespace(title="Loot Command Example")],
        edit=AsyncMock(),
        delete=AsyncMock(),
    )
    channel = SimpleNamespace(id=99, fetch_message=AsyncMock(return_value=existing), send=AsyncMock())
    cache = SimpleNamespace(get=AsyncMock(return_value=40), set=AsyncMock())
    embed = discord.Embed(title="Loot Command Example")
    bot = SimpleNamespace(
        user=SimpleNamespace(id=7),
        cache=cache,
        find_recent_embed_messages=AsyncMock(return_value={embed.title: [existing]}),
    )

    asyncio.run(
        GameAssistBot._sync_singleton_embed(
            bot,
            channel,
            "loot-key",
            embed,
            silent=True,
            clear_content=True,
            history_limit=50,
        )
    )

    existing.edit.assert_awaited_once_with(content=None, embed=embed)
    channel.send.assert_not_awaited()
    bot.find_recent_embed_messages.assert_awaited_once_with(
        channel, {embed.title}, limit=50, raise_on_error=True
    )


def test_transient_history_failure_does_not_create_singleton_embed() -> None:
    response = SimpleNamespace(status=503, reason="Unavailable")
    channel = SimpleNamespace(id=99, send=AsyncMock())
    cache = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock())
    embed = discord.Embed(title="Loot Command Example")
    bot = SimpleNamespace(
        user=SimpleNamespace(id=7),
        cache=cache,
        find_recent_embed_messages=AsyncMock(
            side_effect=discord.HTTPException(response, "temporary")
        ),
    )

    asyncio.run(
        GameAssistBot._sync_singleton_embed(bot, channel, "loot-key", embed)
    )

    channel.send.assert_not_awaited()
    cache.set.assert_not_awaited()


def test_loot_example_uses_singleton_embed_helper() -> None:
    source = inspect.getsource(GameAssistBot.sync_loot_command_example)

    assert "_sync_singleton_embed" in source
    assert "silent=True" in source
    assert "clear_content=True" in source


def test_visitor_welcome_uses_singleton_embed_helper() -> None:
    source = inspect.getsource(GameAssistBot.sync_visitor_welcome)

    assert "_sync_singleton_embed" in source
    assert "history_limit=250" in source
    assert "silent=True" not in source
    assert "delete_recent_duplicate_embed_messages" not in source


def test_ordinary_visitor_examples_use_singleton_helper_but_timers_stay_separate() -> None:
    source = inspect.getsource(GameAssistBot.sync_visitor_command_examples)
    timer_source = inspect.getsource(GameAssistBot._sync_timer_command_example)

    assert 'if channel_name == "timers"' in source
    assert "_sync_timer_command_example" in source
    assert "_sync_singleton_embed" in source
    assert "silent=True" in source
    assert "clear_content=True" in source
    assert "_ensure_timer_dashboard_below_example" in source
    assert "_sync_singleton_embed" not in timer_source
