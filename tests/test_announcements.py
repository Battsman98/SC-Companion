import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from src.announcements import AnnouncementStore, announcement_group, approve_command, permitted


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
