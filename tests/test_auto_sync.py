from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4
import os
import unittest

from sqlalchemy import delete, text

from app import database
from app.models import BankSession, BankSyncState, ControlBase
from app.services import sync_state
from app.services import auto_sync as auto_sync_service, bank_sync as bank_sync_service


class AutoSyncConfigurationTests(unittest.TestCase):
    def test_interval_cannot_be_shorter_than_bank_background_limit(self):
        with patch.dict(os.environ, {"AUTO_SYNC_INTERVAL_SECONDS": "3600"}):
            self.assertEqual(auto_sync_service.auto_sync_interval_seconds(), 21_600)

    def test_default_interval_is_six_hours(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AUTO_SYNC_INTERVAL_SECONDS", None)
            self.assertEqual(auto_sync_service.auto_sync_interval_seconds(), 21_600)


class AutoSyncCycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_syncs_due_owner_and_records_success(self):
        with (
            patch.object(auto_sync_service, "discover_bank_owners", AsyncMock(return_value=["owner@example.com"])),
            patch.object(auto_sync_service, "claim_auto_sync", AsyncMock(return_value=True)),
            patch.object(
                bank_sync_service,
                "sync_owner_transactions",
                AsyncMock(return_value={"accounts": 2, "transactions_imported": 3}),
            ) as sync_owner,
            patch.object(sync_state, "record_auto_sync_result", AsyncMock()) as record_result,
        ):
            result = await auto_sync_service.run_due_auto_sync()

        self.assertEqual(result, {"owners": 1, "synced": 1, "failed": 0})
        sync_owner.assert_awaited_once_with("owner@example.com")
        record_result.assert_awaited_once_with("owner@example.com", error=None)

    async def test_failure_is_recorded_and_does_not_stop_other_owners(self):
        sync_owner = AsyncMock(side_effect=[RuntimeError("bank unavailable"), {"accounts": 1}])
        with (
            patch.object(
                auto_sync_service,
                "discover_bank_owners",
                AsyncMock(return_value=["first@example.com", "second@example.com"]),
            ),
            patch.object(auto_sync_service, "claim_auto_sync", AsyncMock(return_value=True)),
            patch.object(bank_sync_service, "sync_owner_transactions", sync_owner),
            patch.object(sync_state, "record_auto_sync_result", AsyncMock()) as record_result,
        ):
            result = await auto_sync_service.run_due_auto_sync()

        self.assertEqual(result, {"owners": 2, "synced": 1, "failed": 1})
        self.assertEqual(sync_owner.await_count, 2)
        self.assertEqual(record_result.await_count, 2)
        self.assertEqual(record_result.await_args_list[0].args, ("first@example.com",))
        self.assertIn("bank unavailable", record_result.await_args_list[0].kwargs["error"])
        self.assertEqual(record_result.await_args_list[1].kwargs, {"error": None})


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "A disposable PostgreSQL database is required")
class AutoSyncDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovers_owner_and_persists_six_hour_claim(self):
        owner_id = f"autosync-{uuid4()}@example.invalid"
        schema = database.tenant_schema_name(owner_id)
        async with database.engine.begin() as connection:
            await connection.execute(text("CREATE SCHEMA IF NOT EXISTS control"))
            await connection.run_sync(ControlBase.metadata.create_all)
        try:
            async with database.tenant_session(owner_id) as db:
                db.add(
                    BankSession(
                        owner_id=owner_id,
                        enable_session_id=str(uuid4()),
                        bank_name="TEST",
                        country="EE",
                        raw_data={},
                    )
                )
                await db.commit()

            self.assertIn(owner_id, await auto_sync_service.discover_bank_owners())
            first_attempt = datetime(2098, 1, 1, tzinfo=timezone.utc)
            self.assertTrue(await auto_sync_service.claim_auto_sync(owner_id, first_attempt))
            self.assertFalse(await auto_sync_service.claim_auto_sync(owner_id, first_attempt + timedelta(hours=1)))
            self.assertTrue(await auto_sync_service.claim_auto_sync(owner_id, first_attempt + timedelta(hours=6)))
        finally:
            async with database.control_session_factory() as db:
                await db.execute(delete(BankSyncState).where(BankSyncState.owner_id == owner_id))
                await db.commit()
            async with database.engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            database._ready_tenant_schemas.discard(schema)
            await database.engine.dispose()
if __name__ == "__main__":
    unittest.main()
