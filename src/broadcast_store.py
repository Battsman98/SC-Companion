"""Shared durable broadcast records, independent of Discord clients."""
import json
import time
import uuid

class BroadcastStore:
    def __init__(self, connection):
        self.db = connection
        self.db.execute('''CREATE TABLE IF NOT EXISTS development_subscriptions (
            guild_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL,
            enabled INTEGER NOT NULL, updated_by INTEGER NOT NULL)''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS development_broadcasts (
            id TEXT PRIMARY KEY, source_guild_id INTEGER NOT NULL, project TEXT NOT NULL,
            phase TEXT NOT NULL, body TEXT NOT NULL, targets TEXT NOT NULL,
            state TEXT NOT NULL, author_id INTEGER NOT NULL, approver_id INTEGER,
            created_at INTEGER NOT NULL)''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS development_deliveries (
            broadcast_id TEXT NOT NULL, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
            state TEXT NOT NULL, message_id INTEGER, error TEXT,
            PRIMARY KEY (broadcast_id, guild_id))''')
        self.db.commit()

    def subscribe(self, guild_id, channel_id, enabled, actor):
        self.db.execute('''INSERT INTO development_subscriptions VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id,
            enabled=excluded.enabled, updated_by=excluded.updated_by''',
            (guild_id, channel_id, int(enabled), actor))
        self.db.commit()

    def targets(self, pilot, testing_only=True):
        if not testing_only:
            return self.db.execute("SELECT guild_id, channel_id FROM development_subscriptions WHERE enabled=1 ORDER BY guild_id").fetchall()
        # Fail closed until the wider rollout is explicitly enabled.
        return self.db.execute('''SELECT guild_id, channel_id FROM development_subscriptions
            WHERE enabled=1 AND guild_id=?''', (pilot or 0,)).fetchall()

    def create(self, source, project, phase, body, targets, actor):
        project, body = project.strip(), body.strip()
        if not project or len(project) > 150 or not body or len(body) > 3500:
            raise ValueError('Use a project name of 1–150 characters and an update of 1–3500 characters.')
        if phase not in {'started', 'completed'}:
            raise ValueError('Choose started or completed.')
        if not targets:
            raise ValueError('No eligible opted-in channels. Enable /development subscription with SC Companion first.')
        key = uuid.uuid4().hex
        self.db.execute('''INSERT INTO development_broadcasts
            (id,source_guild_id,project,phase,body,targets,state,author_id,created_at)
            VALUES (?,?,?,?,?,?,'draft',?,?)''',
            (key, source, project, phase, body, json.dumps(targets), actor, int(time.time())))
        self.db.commit()
        return key

    def get(self, source, key):
        row = self.db.execute('''SELECT id,project,phase,body,targets,state
            FROM development_broadcasts WHERE source_guild_id=? AND id=?''', (source, key)).fetchone()
        return dict(zip(('id','project','phase','body','targets','state'), row)) if row else None

    def decide(self, source, key, state, actor):
        changed = self.db.execute('''UPDATE development_broadcasts SET state=?,approver_id=?
            WHERE source_guild_id=? AND id=? AND state='draft' ''', (state, actor, source, key)).rowcount
        self.db.commit()
        return changed == 1

    def claim(self, key, guild_id, channel_id):
        # The durable claim happens before sending. A crash/timeout never causes an automatic resend.
        changed = self.db.execute('''INSERT INTO development_deliveries
            (broadcast_id,guild_id,channel_id,state) VALUES (?,?,?,'sending')
            ON CONFLICT(broadcast_id,guild_id) DO NOTHING''', (key,guild_id,channel_id)).rowcount
        self.db.commit()
        return changed == 1

    def receipt(self, key, guild_id, state, message_id=None, error=None):
        self.db.execute('''UPDATE development_deliveries SET state=?,message_id=?,error=?
            WHERE broadcast_id=? AND guild_id=?''', (state,message_id,error,key,guild_id))
        self.db.commit()

