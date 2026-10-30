import asyncio
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from src.broadcast_store import BroadcastStore
from src.broadcasts import deliver_broadcasts, permitted, subscription_permitted


def settings(profile='public', pilot=True):
    return SimpleNamespace(runtime_profile=profile, award_test_guild_id=1,
                           announcement_testing_only=pilot, discord_guild_id=9, bot_admin_user_ids=(7,))


def database():
    return BroadcastStore(sqlite3.connect(':memory:'))


def draft(db, targets=None):
    return db.create(9, 'Inventory updates', 'completed', 'Ready to try.', targets or [(1, 10)], 7)


@pytest.mark.parametrize('profile,guild,user,owner,allowed', [
    ('peep',9,7,8,True), ('peep',9,8,8,True), ('peep',9,6,8,False),
    ('public',9,7,8,False), ('peep',1,7,8,False),
])
def test_only_private_home_admin_can_broadcast(profile,guild,user,owner,allowed):
    interaction = SimpleNamespace(client=SimpleNamespace(settings=settings(profile)), guild_id=guild,
                                  guild=SimpleNamespace(owner_id=owner), user=SimpleNamespace(id=user))
    assert permitted(interaction) is allowed


def test_audience_snapshot_and_rollout():
    db = database()
    db.subscribe(1,10,True,7)
    db.subscribe(2,20,True,8)
    key = draft(db, db.targets(1))
    db.subscribe(1,11,True,7)
    assert json.loads(db.get(9,key)['targets']) == [[1,10]]
    assert db.targets(1) == [(1,11)]
    assert db.targets(1,False) == [(1,11),(2,20)]
    assert db.targets(None) == []
    assert db.get(8,key) is None


def test_repeated_approval_and_restart_claim(tmp_path):
    path = str(tmp_path / 'shared.db')
    db = BroadcastStore(sqlite3.connect(path))
    key = draft(db)
    assert not db.decide(8,key,'approved',7)
    assert db.decide(9,key,'approved',7)
    other = BroadcastStore(sqlite3.connect(path))
    assert not other.decide(9,key,'approved',7)
    assert other.claim(key,1,10)
    assert not db.claim(key,1,10)


@pytest.mark.parametrize('scenario,expected,sends', [
    ('normal','published',1), ('disabled','skipped',0), ('changed','skipped',0),
    ('timeout','uncertain',1), ('permissions','failed',0), ('missing','failed',0),
    ('outside-pilot','skipped',0),
])
def test_delivery_receipts_and_no_resend(scenario,expected,sends):
    async def check():
        db = database()
        guild_id = 2 if scenario == 'outside-pilot' else 1
        db.subscribe(guild_id,10,True,7)
        key = draft(db, [(guild_id,10)])
        channel = Mock(spec=discord.TextChannel)
        channel.permissions_for.return_value = SimpleNamespace(view_channel=True,send_messages=scenario!='permissions',embed_links=True)
        channel.send = AsyncMock(return_value=SimpleNamespace(id=99))
        if scenario == 'timeout':
            channel.send.side_effect = TimeoutError()
        guild = SimpleNamespace(me=object(),get_channel=lambda _: None if scenario=='missing' else channel)
        bot = SimpleNamespace(settings=settings(),cache=SimpleNamespace(_connection=db.db),get_guild=lambda _: guild)
        await deliver_broadcasts(bot)
        channel.send.assert_not_awaited()  # Drafts are never delivered.
        db.decide(9,key,'approved',7)
        if scenario == 'disabled':
            db.subscribe(guild_id,10,False,7)
        if scenario == 'changed':
            db.subscribe(guild_id,11,True,7)
        await deliver_broadcasts(bot)
        await deliver_broadcasts(bot)
        assert channel.send.await_count == sends
        assert db.db.execute('SELECT state FROM development_deliveries').fetchone()[0] == expected
        if scenario == 'normal':
            assert channel.send.await_args.kwargs['allowed_mentions'].everyone is False
            assert channel.send.await_args.kwargs['silent'] is True
    asyncio.run(check())


def test_all_server_delivery_with_explicit_rollout():
    async def check():
        db = database()
        db.subscribe(1,10,True,7)
        db.subscribe(2,20,True,7)
        key = draft(db, db.targets(1,False))
        db.decide(9,key,'approved',7)
        channels = {}
        for number in (10,20):
            channel = Mock(spec=discord.TextChannel)
            channel.permissions_for.return_value = SimpleNamespace(view_channel=True,send_messages=True,embed_links=True)
            channel.send = AsyncMock(return_value=SimpleNamespace(id=number+100))
            channels[number] = channel
        bot = SimpleNamespace(settings=settings('peep',False), cache=SimpleNamespace(_connection=db.db),
                              get_guild=lambda _: SimpleNamespace(me=object(),get_channel=channels.get))
        await deliver_broadcasts(bot)
        assert all(c.send.await_count==0 for c in channels.values())
        bot.settings = settings('public',False)
        await deliver_broadcasts(bot)
        assert all(c.send.await_count==1 for c in channels.values())
    asyncio.run(check())


def test_subscription_permissions_respect_pilot():
    interaction = SimpleNamespace(client=SimpleNamespace(settings=settings()),guild_id=2,
        guild=SimpleNamespace(owner_id=8),user=SimpleNamespace(id=8,guild_permissions=SimpleNamespace(manage_guild=True)))
    assert not subscription_permitted(interaction)
    interaction.client.settings.announcement_testing_only=False
    assert subscription_permitted(interaction)
    interaction.user.id=4
    interaction.user.guild_permissions.manage_guild=False
    assert not subscription_permitted(interaction)


@pytest.mark.parametrize('value,expected', [(None,True), ('false',False), ('true',True), ('typo',True)])
def test_rollout_flag_defaults_closed(monkeypatch,value,expected):
    from src.config import Settings
    if value is None:
        monkeypatch.delenv('ANNOUNCEMENT_TESTING_ONLY',raising=False)
    else:
        monkeypatch.setenv('ANNOUNCEMENT_TESTING_ONLY',value)
    assert Settings.from_env(load_env_file=False,require_discord_token=False).announcement_testing_only is expected

