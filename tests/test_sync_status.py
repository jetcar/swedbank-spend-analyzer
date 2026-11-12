"""Synchronization timestamp API integration tests against PostgreSQL."""
import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from sqlalchemy import delete, text

from app import config, database, dependencies, main
from app.services import auto_sync as auto_sync_service, bank_sync as bank_sync_service, sync_state
from fastapi import HTTPException
import asyncpg
from app.models import BankSyncState, ControlBase


class SyncStatusTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owners = [f"sync-status-{uuid4()}@example.invalid" for _ in range(2)]
        self.owner = self.owners[0]
        async with database.engine.begin() as connection:
            await connection.execute(text("CREATE SCHEMA IF NOT EXISTS control"))
            await connection.run_sync(ControlBase.metadata.create_all)
        main.app.dependency_overrides[dependencies.current_owner_id] = lambda: self.owner
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        main.app.dependency_overrides.pop(dependencies.current_owner_id, None)
        async with database.control_session_factory() as db:
            await db.execute(delete(BankSyncState).where(BankSyncState.owner_id.in_(self.owners)))
            await db.commit()
        await database.engine.dispose()

    async def status(self):
        response = await self.client.get("/api/sync")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["last_success_at"]

    async def test_manual_success_is_persisted_scoped_and_failure_preserves_date(self):
        self.assertIsNone(await self.status())
        before = datetime.now(timezone.utc)
        with patch.object(bank_sync_service, "sync_owner_transactions", AsyncMock(return_value={"accounts": 1, "transactions_imported": 0})):
            response = await self.client.post("/api/sync")
        self.assertEqual(response.status_code, 200)
        saved = await self.status()
        self.assertGreaterEqual(datetime.fromisoformat(saved), before)
        self.assertLessEqual(datetime.fromisoformat(saved), datetime.now(timezone.utc))
        with patch.object(bank_sync_service, "sync_owner_transactions", AsyncMock(side_effect=HTTPException(502, "Bank unavailable"))):
            response = await self.client.post("/api/sync")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(await self.status(), saved)
        self.owner = self.owners[1]
        self.assertIsNone(await self.status())
        self.owner = self.owners[0]
        self.assertEqual(await self.status(), saved)

    async def test_background_success_and_failure_share_the_displayed_timestamp(self):
        with (
            patch.object(auto_sync_service, "discover_bank_owners", AsyncMock(return_value=[self.owner])),
            patch.object(auto_sync_service, "claim_auto_sync", AsyncMock(return_value=True)),
            patch.object(bank_sync_service, "sync_owner_transactions", AsyncMock(return_value={"accounts": 1, "transactions_imported": 0})),
        ):
            await auto_sync_service.run_due_auto_sync()
        saved = await self.status()
        self.assertIsNotNone(saved)
        await sync_state.record_auto_sync_result(self.owner, error="Bank unavailable")
        self.assertEqual(await self.status(), saved)

    async def test_success_notifies_open_views_after_persisting_the_timestamp(self):
        connection = await asyncpg.connect(config.database_url.replace("postgresql+asyncpg://", "postgresql://"))
        changed = asyncio.Event()

        def on_change(_connection, _pid, _channel, owner):
            if owner == self.owner:
                changed.set()

        try:
            await connection.add_listener("data_changed", on_change)
            await sync_state.record_auto_sync_result(self.owner, error=None)
            await asyncio.wait_for(changed.wait(), timeout=5)
            self.assertIsNotNone(await self.status())
        finally:
            await connection.close()


if __name__ == "__main__":
    unittest.main()
