"""Website-only CCU persistence using the existing database connection."""
import json
from typing import Any


class CcuStore:
    def __init__(self, cache):
        self._connection = cache._connection

    def _ensure_ccu_plans(self) -> None:
        self._connection.execute("""CREATE TABLE IF NOT EXISTS user_ccu_plans (
            user_id INTEGER PRIMARY KEY, revision INTEGER NOT NULL, value_json TEXT NOT NULL
        )""")
        self._connection.commit()

    async def ccu_plan(self, user_id: int) -> dict[str, Any]:
        self._ensure_ccu_plans()
        row = self._connection.execute(
            "SELECT revision, value_json FROM user_ccu_plans WHERE user_id = ?", (user_id,)
        ).fetchone()
        return {**json.loads(row[1]), "revision": row[0]} if row else {"revision": 0}

    async def save_ccu_plan(self, user_id: int, revision: int, value: dict[str, Any]) -> bool:
        self._ensure_ccu_plans()
        if revision == 0:
            cursor = self._connection.execute("""INSERT INTO user_ccu_plans (user_id, revision, value_json)
                VALUES (?, 1, ?) ON CONFLICT(user_id) DO NOTHING""", (user_id, json.dumps(value)))
        else:
            cursor = self._connection.execute("""UPDATE user_ccu_plans SET revision = ?, value_json = ?
                WHERE user_id = ? AND revision = ?""", (revision + 1, json.dumps(value), user_id, revision))
        self._connection.commit()
        return cursor.rowcount == 1

