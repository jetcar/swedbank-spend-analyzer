from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app import bank_client, database
from app.services import bank_sync


class BankImportCleanupTest(unittest.IsolatedAsyncioTestCase):
    async def test_failed_persistence_closes_bank_page_stream(self):
        closed = []

        async def pages(*_args):
            try:
                yield {"transactions": []}
            finally:
                closed.append(True)

        account = SimpleNamespace(raw_data={}, bank_account_id="bank-account")
        with (
            patch.object(bank_client, "bank_api_headers", AsyncMock(return_value={})),
            patch.object(bank_client, "transaction_pages", pages),
            patch.object(database, "tenant_session", side_effect=RuntimeError("database unavailable")),
        ):
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                await bank_sync.fetch_transactions(account, "owner@example.com")
        self.assertEqual(closed, [True])
