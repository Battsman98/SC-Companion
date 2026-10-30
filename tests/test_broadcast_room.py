import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import discord
import pytest
from src.broadcast_room import room_overwrites, ensure_broadcast_room
from src.broadcasts import broadcast_group


def fixture_guild():
    everyone, reader, blocked, owner, me = [Mock(spec=discord.Role) for _ in range(5)]
    category=Mock(spec=discord.CategoryChannel)
    category.id=22
    category.overwrites={everyone:discord.PermissionOverwrite(view_channel=True),
        reader:discord.PermissionOverwrite(view_channel=True,send_messages=True),
        blocked:discord.PermissionOverwrite(view_channel=False)}
    guild=SimpleNamespace(id=9,default_role=everyone,owner=owner,me=me,get_channel=lambda _:category)
    return guild,category,reader,blocked


def test_private_room_preserves_audit_readers_but_only_owner_can_write():
    guild,category,reader,blocked=fixture_guild()
    actual,permissions=room_overwrites(guild)
    assert actual is category
    assert permissions[guild.default_role].view_channel is False
    assert permissions[reader].view_channel is True
    assert permissions[reader].send_messages is False
    assert permissions[reader].create_public_threads is False
    assert permissions[blocked].view_channel is False
    assert permissions[guild.owner].send_messages is True
    assert permissions[guild.me].view_channel is True


def test_missing_category_fails_closed():
    guild,_,_,_=fixture_guild()
    guild.get_channel=lambda _:None
    guild.categories=[]
    with pytest.raises(ValueError):
        room_overwrites(guild)


def test_room_repair_reuses_channel_and_panel():
    async def check():
        guild,category,_,_=fixture_guild()
        channel=Mock(spec=discord.TextChannel)
        channel.id=33
        channel.category_id=0
        channel.overwrites={}
        channel.edit=AsyncMock()
        message=SimpleNamespace(id=44,author=SimpleNamespace(id=55),edit=AsyncMock())
        channel.fetch_message=AsyncMock(return_value=message)
        channel.send=AsyncMock()
        guild.get_channel=lambda key:channel if key==33 else category
        bot=SimpleNamespace(settings=SimpleNamespace(runtime_profile='peep',discord_guild_id=9),
            get_guild=lambda _:guild,user=SimpleNamespace(id=55),
            cache=SimpleNamespace(get=AsyncMock(return_value={'channel_id':33,'panel_message_id':44}),set=AsyncMock()))
        assert await ensure_broadcast_room(bot) is channel
        assert channel.edit.await_args.kwargs['category'] is category
        channel.send.assert_not_awaited()
        message.edit.assert_awaited_once()
    asyncio.run(check())


@pytest.mark.parametrize('user_id,channel_id,command,allowed', [(7,33,'draft',False),(8,34,'draft',False),(8,33,'draft',True),(8,34,'channel',True)])
def test_broadcasts_require_owner_and_private_workspace(user_id,channel_id,command,allowed):
    interaction=SimpleNamespace(client=SimpleNamespace(
        settings=SimpleNamespace(runtime_profile='peep',discord_guild_id=9,bot_admin_user_ids=(7,)),
        cache=SimpleNamespace(get=AsyncMock(return_value={'channel_id':33}))),
        user=SimpleNamespace(id=user_id),guild=SimpleNamespace(owner_id=8),guild_id=9,
        channel_id=channel_id,command=SimpleNamespace(name=command),
        response=SimpleNamespace(send_message=AsyncMock()))
    assert asyncio.run(broadcast_group.interaction_check(interaction)) is allowed
