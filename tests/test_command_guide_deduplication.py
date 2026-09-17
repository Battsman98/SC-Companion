import inspect

from src.bot import GameAssistBot


def test_guild_command_guides_recover_and_remove_duplicates() -> None:
    source = inspect.getsource(GameAssistBot.sync_guild_command_examples)

    assert "find_recent_embed_messages" in source
    assert "min(existing, key=lambda candidate: candidate.id)" in source
    assert "await self.cache.set(cache_key, message.id" in source
    assert "await duplicate.delete()" in source


def test_transient_fetch_failure_does_not_post_a_replacement_guide() -> None:
    source = inspect.getsource(GameAssistBot.sync_guild_command_examples)

    transient_handler = source.split("except (discord.Forbidden, discord.HTTPException):", 1)[1]
    transient_handler = transient_handler.split("embed = build_guild_command_guide_embed", 1)[0]
    assert "continue" in transient_handler
    assert "channel.send" not in transient_handler
