import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock
from src.broadcast_ui import BroadcastPanel, DraftModal, SubmitView, choose_draft
from src.broadcast_store import BroadcastStore


def context():
    db=BroadcastStore(sqlite3.connect(':memory:'))
    db.subscribe(1,10,True,8)
    interaction=SimpleNamespace(guild_id=9,channel_id=33,guild=SimpleNamespace(owner_id=8),user=SimpleNamespace(id=8),
        client=SimpleNamespace(settings=SimpleNamespace(runtime_profile='peep',discord_guild_id=9,award_test_guild_id=1,announcement_testing_only=True),
            cache=SimpleNamespace(_connection=db.db,get=AsyncMock(return_value={'channel_id':33}))),
        response=SimpleNamespace(send_message=AsyncMock(),edit_message=AsyncMock(),send_modal=AsyncMock()))
    return db,interaction


def test_persistent_panel_has_two_controls_and_blocks_nonowner():
    async def check():
        _,i=context()
        panel=BroadcastPanel()
        assert panel.is_persistent()
        assert [item.label for item in panel.children]==['Draft','Approve']
        assert await panel.interaction_check(i)
        i.user.id=7
        assert not await panel.interaction_check(i)
    asyncio.run(check())


def test_single_text_draft_requires_review_and_explicit_submission():
    async def check():
        db,i=context()
        modal=DraftModal()
        assert len(modal.children)==1
        modal.announcement._value='Here is exactly what I want to announce.'
        await modal.on_submit(i)
        assert i.response.send_message.await_args.kwargs["ephemeral"] is True
        key,state,body=db.db.execute('SELECT id,state,body FROM development_broadcasts').fetchone()
        assert state=='draft'
        assert body==str(modal.announcement)
        await choose_draft(i)
        assert i.response.send_message.await_args.kwargs["ephemeral"] is True
        view=i.response.send_message.await_args.kwargs['view']
        assert isinstance(view,SubmitView)
        assert db.get(9,key)['state']=='draft'
        await view.submit.callback(i)
        assert db.get(9,key)['state']=='approved'
        await view.submit.callback(i)
        assert 'already been handled' in i.response.edit_message.await_args.kwargs['content']
    asyncio.run(check())


def test_review_does_not_silently_choose_between_multiple_drafts():
    async def check():
        db,i=context()
        for text in ('First update','Second update'):
            db.create(9,'Development Announcement','completed',text,[(1,10)],8)
        await choose_draft(i)
        selector=i.response.send_message.await_args.kwargs['view'].children[0]
        assert len(selector.options)==2
        assert db.db.execute("SELECT COUNT(*) FROM development_broadcasts WHERE state='draft'").fetchone()[0]==2
    asyncio.run(check())
