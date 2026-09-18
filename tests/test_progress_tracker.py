import asyncio
from pathlib import Path

from src.cache import SQLiteCache


def test_monthly_activity_and_reputation_progress(tmp_path: Path) -> None:
    async def scenario() -> None:
        cache = await SQLiteCache.create(str(tmp_path / "progress.sqlite3"))
        try:
            await cache.record_discord_message_activity(1, 2, 1_725_235_200)
            await cache.record_discord_message_activity(1, 2, 1_725_235_260)
            await cache.start_discord_voice_session(1, 2, 1_725_235_200)
            await cache.finish_discord_voice_session(1, 2, 1_725_238_800)
            activity = await cache.discord_monthly_activity(1, 2, "2024-09")
            assert activity == {
                "month": "2024-09", "message_count": 2, "voice_seconds": 3600, "active_days": 1
            }

            await cache.save_reputation_progress(1, 2, "Covalex", "Senior", 9, 100)
            await cache.save_reputation_progress(1, 2, "Covalex", "Master", 9, 200)
            await cache.save_reputation_progress(1, 2, "Red Wind Linehaul", "Junior", 9, 300)
            await cache.save_reputation_progress(1, 2, "Miles Eckhart", "Security Contractor", 9, 400)
            progress = await cache.reputation_progress(1, 2)
            assert [(item["giver"], item["level"]) for item in progress] == [
                ("Covalex", "Master"), ("Eckhart Security", "Contractor"),
                ("Red Wind Linehaul", "Junior"),
            ]
        finally:
            await cache.close()

    asyncio.run(scenario())
