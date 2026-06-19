"""Re-run sentiment classification on events where it failed (neutral + api error).
Run after adding a real ANTHROPIC_API_KEY to .env:
  docker cp reclassify.py outreach-backend:/tmp/
  docker exec outreach-backend python /tmp/reclassify.py
"""
import asyncio, logging
logging.basicConfig(level=logging.INFO)
from outreach.db import SessionLocal
from sqlalchemy import text, delete, select
from outreach.models.event import Event, ReplySentiment
from outreach.services.sentiment import classify_and_store

async def main():
    # Delete reply_sentiments where classification failed (api error)
    async with SessionLocal() as s:
        r = await s.execute(
            text("DELETE FROM reply_sentiment WHERE reasoning LIKE 'api error%' RETURNING event_id")
        )
        deleted_ids = [row[0] for row in r]
        await s.commit()
        print(f"Deleted {len(deleted_ids)} failed sentiment rows: {deleted_ids}")

    # Re-classify those events
    for eid in deleted_ids:
        async with SessionLocal() as s:
            result = await classify_and_store(s, eid)
            print(f"Event {eid}: {result}")

asyncio.run(main())
