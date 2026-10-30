import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest


def test_setup_button_only_in_testing_admin_panel():
    from src.bot import NativeAdminView
    from src.guild_config import default_module_settings
    async def check():
        assert 'Development Announcements' in [getattr(item, 'label', None) for item in NativeAdminView(default_module_settings(), awards_available=True).children]
        assert 'Reputation Setup' in [getattr(item, 'label', None) for item in NativeAdminView(default_module_settings(), awards_available=True).children]
        assert 'Reputation Setup' not in [getattr(item, 'label', None) for item in NativeAdminView(default_module_settings()).children]
        assert 'Development Announcements' not in [getattr(item, 'label', None) for item in NativeAdminView(default_module_settings()).children]
    asyncio.run(check())


def test_reputation_enable_requires_reviewer_before_saving():
    from src.bot import ReputationAdminView
    async def check():
        cache = SimpleNamespace(get=AsyncMock(return_value={}), set=AsyncMock())
        interaction = SimpleNamespace(client=SimpleNamespace(cache=cache), guild_id=1,
                                      response=SimpleNamespace(send_message=AsyncMock()))
        view = ReputationAdminView()
        await view.enable.callback(interaction)
        cache.set.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()
    asyncio.run(check())


def test_reputation_setup_rejects_non_testing_server():
    from src.bot import ReputationAdminView, GameAssistBot
    async def check():
        bot = Mock(spec=GameAssistBot)
        bot.settings = SimpleNamespace(runtime_profile='public', award_test_guild_id=1)
        interaction = SimpleNamespace(client=bot, guild=SimpleNamespace(id=2), guild_id=2,
                                      response=SimpleNamespace(send_message=AsyncMock()))
        assert not await ReputationAdminView().interaction_check(interaction)
    asyncio.run(check())


@pytest.mark.parametrize('guild_id,owner,allowed', [(1, 4, True), (1, 5, False), (2, 4, False)])
def test_website_setup_requires_testing_owner(monkeypatch, guild_id, owner, allowed):
    from src import web
    from fastapi import HTTPException
    cache = SimpleNamespace(get=AsyncMock(return_value={}), set=AsyncMock())
    monkeypatch.setattr(web, 'state', lambda: SimpleNamespace(cache=cache, settings=SimpleNamespace(award_test_guild_id=1, bot_admin_user_ids=(), announcement_testing_only=True)))
    monkeypatch.setattr(web, '_managed_guild', AsyncMock(return_value={}))
    monkeypatch.setattr(web, '_discord_bot_guild', AsyncMock(return_value={'owner_id': str(owner)}))
    if allowed:
        assert asyncio.run(web.setup_development_dashboard(guild_id, SimpleNamespace(id=4))) == {'status': 'queued'}
        assert cache.set.await_args.args[1]['requested'] is True
    else:
        with pytest.raises(HTTPException):
            asyncio.run(web.setup_development_dashboard(guild_id, SimpleNamespace(id=4)))
        cache.set.assert_not_awaited()


def test_panel_setup_reuses_channel_and_saved_message():
    from src.announcements import ensure_development_panel
    from src.cache import SQLiteCache
    async def check():
        cache = await SQLiteCache.create(':memory:')
        message = SimpleNamespace(id=6, author=SimpleNamespace(id=7), edit=AsyncMock())
        channel = Mock(spec=discord.TextChannel)
        channel.id = 2
        channel.category_id = None
        channel.edit = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=message)
        channel.send = AsyncMock()
        guild = SimpleNamespace(id=1, get_channel=lambda _: channel, categories=[SimpleNamespace(id=8, name="🚀 SC COMPANION")])
        bot = SimpleNamespace(settings=SimpleNamespace(runtime_profile='public', award_test_guild_id=1), cache=cache, user=SimpleNamespace(id=7))
        await cache.set('guild:1:development-announcements', {'channel_id': 2, 'panel_message_id': 6, 'requested': True}, 300)
        await ensure_development_panel(bot, guild)
        await ensure_development_panel(bot, guild)
        assert message.edit.await_count == 2
        description = message.edit.await_args.kwargs['embed'].description
        assert '/broadcast' not in description and '/announcement' not in description
        assert 'upcoming features' in description
        from src.broadcast_store import BroadcastStore
        db = BroadcastStore(cache._connection)
        assert db.targets(1) == [(1, 2)]
        db.subscribe(1, 2, False, 7)
        await ensure_development_panel(bot, guild)
        assert db.targets(1) == []
        assert channel.edit.await_args.kwargs["category"].id == 8
        assert channel.edit.await_args.kwargs["sync_permissions"] is False
        channel.send.assert_not_awaited()
        assert not (await cache.get('guild:1:development-announcements'))['requested']
    asyncio.run(check())

from src.announcements import AnnouncementStore, announcement_group, approve_command, permitted, denial_reason


@pytest.mark.parametrize('profile,guild_id,owner_id,expected', [
    ('public', 1, 4, True), ('public', 1, 5, False),
    ('public', 2, 4, False), ('peep', 1, 4, False),
])
def test_testing_owner_with_empty_admin_configuration(profile, guild_id, owner_id, expected):
    interaction = SimpleNamespace(
        client=SimpleNamespace(settings=SimpleNamespace(runtime_profile=profile, award_test_guild_id=1, bot_admin_user_ids=())),
        guild_id=guild_id, guild=SimpleNamespace(owner_id=owner_id),
        user=SimpleNamespace(id=4), response=SimpleNamespace(send_message=AsyncMock()))
    assert permitted(interaction) is expected
    assert asyncio.run(announcement_group.interaction_check(interaction)) is expected
    if not expected:
        assert denial_reason(interaction)
        interaction.response.send_message.assert_awaited_once_with(denial_reason(interaction), ephemeral=True)


def test_drafts_survive_restart_and_claim_once(tmp_path):
    path = tmp_path / 'announcements.sqlite'
    connection = sqlite3.connect(path)
    db = AnnouncementStore(connection)
    draft_id = db.create(1, 2, 'Inventory', 'started', 'New project', 3)
    connection.close()
    db = AnnouncementStore(sqlite3.connect(path))
    assert db.get(1, draft_id)['state'] == 'draft'
    assert db.get(9, draft_id) is None
    assert not db.transition(9, draft_id, 'sending', 3)
    assert db.transition(1, draft_id, 'sending', 3)
    assert not db.transition(1, draft_id, 'sending', 3)
    db.finish(1, draft_id, 123)
    assert db.get(1, draft_id)['message_id'] == 123
    with pytest.raises(ValueError):
        db.create(1, 2, 'Inventory', 'started', 'Duplicate', 3)
    db.create(1, 2, 'Inventory', 'completed', 'Finished', 3)


@pytest.mark.parametrize('profile,guild,user,expected', [('public', 1, 3, True), ('peep', 1, 3, False), ('public', 2, 3, False), ('public', 1, 4, False)])
def test_test_server_and_owner_gate(profile, guild, user, expected):
    interaction = SimpleNamespace(client=SimpleNamespace(settings=SimpleNamespace(runtime_profile=profile, award_test_guild_id=1, bot_admin_user_ids=(3,))), guild_id=guild, user=SimpleNamespace(id=user))
    assert permitted(interaction) is expected
    interaction.response = SimpleNamespace(send_message=AsyncMock())
    assert asyncio.run(announcement_group.interaction_check(interaction)) is expected


@pytest.mark.parametrize('fails', [False, True])
def test_approval_publishes_once_and_holds_uncertain_delivery(fails):
    connection = sqlite3.connect(':memory:')
    db = AnnouncementStore(connection)
    draft_id = db.create(1, 2, 'Inventory', 'completed', 'Ready', 3)
    channel = Mock(spec=discord.TextChannel)
    channel.permissions_for.return_value = SimpleNamespace(view_channel=True, send_messages=True, embed_links=True)
    channel.send = AsyncMock(return_value=SimpleNamespace(id=42, jump_url='https://discord.com/channels/1/2/42'))
    if fails:
        channel.send.side_effect = TimeoutError('ambiguous response')
    interaction = SimpleNamespace(guild_id=1, user=SimpleNamespace(id=3),
        client=SimpleNamespace(cache=SimpleNamespace(_connection=connection)),
        guild=SimpleNamespace(get_channel=lambda _: channel, me=object()),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()))
    asyncio.run(approve_command.callback(interaction, draft_id))
    asyncio.run(approve_command.callback(interaction, draft_id))
    channel.send.assert_awaited_once()
    assert db.get(1, draft_id)['state'] == ('sending' if fails else 'published')
    assert channel.send.await_args.kwargs['allowed_mentions'].everyone is False


def test_revision_during_approval_requires_review_again():
    db = AnnouncementStore(sqlite3.connect(':memory:'))
    draft_id = db.create(1, 2, 'Inventory', 'started', 'Original', 3)
    snapshot = db.get(1, draft_id)
    db.db.execute('UPDATE project_announcements SET body = ? WHERE id = ?', ('Changed', draft_id))
    db.db.commit()
    assert not db.transition(1, draft_id, 'sending', 3, snapshot=snapshot)
    assert db.get(1, draft_id)['state'] == 'draft'
