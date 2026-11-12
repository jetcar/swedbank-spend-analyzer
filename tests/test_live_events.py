"""Integration check against the running app and its PostgreSQL database.

Run after app.seed_demo in local authentication mode, inside the app container.
"""
from contextlib import suppress
import asyncio
import unittest

from sqlalchemy import text
import httpx

from app.passi_auth import PASSI_LOCAL_OWNER_ID
from app.database import engine, tenant_schema_name, tenant_session


class LiveEventsTest(unittest.IsolatedAsyncioTestCase):
    async def test_commit_broadcast_and_rollback(self):
        async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=None) as client:
            queues = [asyncio.Queue(), asyncio.Queue()]

            async def listen(queue):
                async with client.stream("GET", "/api/events") as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line == "event: changed":
                            queue.put_nowait(line)

            listeners = [asyncio.create_task(listen(queue)) for queue in queues]
            try:
                for queue in queues:
                    await asyncio.wait_for(queue.get(), 5)
                async with tenant_session(PASSI_LOCAL_OWNER_ID) as connection:
                    schema = tenant_schema_name(PASSI_LOCAL_OWNER_ID)
                    demo_id = await connection.scalar(text(f"""
                        SELECT id FROM "{schema}".transactions
                        WHERE raw_data->>'test_data' = 'true' LIMIT 1
                    """))
                    self.assertIsNotNone(demo_id, "Run app.seed_demo first")
                    statement = text(f'UPDATE "{schema}".transactions SET amount = amount WHERE id = :id')
                    await connection.execute(statement, {"id": demo_id})
                    # Neither uncommitted changes nor rollbacks may refresh FE.
                    with self.assertRaises(TimeoutError):
                        await asyncio.wait_for(queues[0].get(), 0.3)
                    await connection.rollback()
                    with self.assertRaises(TimeoutError):
                        await asyncio.wait_for(queues[0].get(), 0.3)
                    await connection.execute(statement, {"id": demo_id})
                    await connection.commit()
                    for queue in queues:
                        await asyncio.wait_for(queue.get(), 5)
            finally:
                for listener in listeners:
                    listener.cancel()
                for listener in listeners:
                    with suppress(asyncio.CancelledError):
                        await listener
                await engine.dispose()
if __name__ == "__main__":
    unittest.main()
