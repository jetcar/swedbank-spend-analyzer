"""Statistics exclusions are reversible and do not change account balances."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4
import unittest

from sqlalchemy import select, text
import httpx

from app import bank_client, database, dependencies, main, schema as schema_ddl
from app.models import Account, BankSession, Category, ExpenseSplit, Transaction
from app.services import bank_sync as bank_sync_service


class TransactionStatisticsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner = f"statistics-{uuid4()}@example.com"
        self.other = f"statistics-other-{uuid4()}@example.com"
        main.app.dependency_overrides[dependencies.current_owner_id] = lambda: self.owner
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")
        async with database.tenant_session(self.owner) as db:
            session = BankSession(owner_id=self.owner, enable_session_id=str(uuid4()), bank_name="TEST", country="EE", raw_data={})
            db.add(session)
            await db.flush()
            account = Account(session_id=session.id, bank_account_id="test", currency="EUR", raw_data={})
            categories = [Category(owner_id=self.owner, name=name, month="2098-06") for name in ("One", "Two")]
            db.add_all([account, *categories])
            await db.flush()
            self.rows = [Transaction(account_id=account.id, external_id=str(index), amount=amount, direction=direction,
                                     booking_date=datetime(2098, 6, 1, tzinfo=timezone.utc), currency="EUR",
                                     category_id=categories[0].id, raw_data={})
                         for index, (amount, direction) in enumerate([(100, "CRDT"), (40, "DBIT"), (10, "DBIT")])]
            db.add_all(self.rows)
            await db.flush()
            db.add_all([ExpenseSplit(transaction_id=self.rows[1].id, category_id=category.id, amount=20) for category in categories])
            await db.commit()

    async def asyncTearDown(self):
        await self.client.aclose()
        main.app.dependency_overrides.pop(dependencies.current_owner_id, None)
        async with database.engine.begin() as connection:
            for owner in (self.owner, self.other):
                schema = database.tenant_schema_name(owner)
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
                database._ready_tenant_schemas.discard(schema)
        await database.engine.dispose()

    async def toggle(self, transaction, excluded):
        response = await self.client.patch(f"/api/transactions/{transaction.id}/statistics", json={"excluded_from_statistics": excluded})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["excluded_from_statistics"], excluded)

    async def test_exclude_restore_splits_totals_and_balance(self):
        for excluded in (True, False):
            for transaction in self.rows[:2]:
                await self.toggle(transaction, excluded)
            rows = (await self.client.get("/api/transactions?month=2098-06")).json()
            self.assertEqual(len(rows), 3)
            self.assertEqual(sum(row["excluded_from_statistics"] for row in rows), 2 if excluded else 0)
            self.assertEqual(len(next(row for row in rows if row["id"] == self.rows[1].id)["splits"]), 2)
            self.assertEqual((await self.client.get("/api/accounts")).json()[0]["balance"], "50.00")
            categories = (await self.client.get("/api/categories?month=2098-06")).json()
            self.assertEqual(sum(row["spent"] for row in categories), 10 if excluded else 50)
            self.assertEqual(sum(row["earned"] for row in categories), 0 if excluded else 100)
            for path in ("/api/summary", "/api/daily-summary", "/api/daily-breakdown?month=2098-06"):
                response = await self.client.get(path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(sum(row["total"] for row in response.json()), 10 if excluded else 50, path)

    async def test_validation_and_owner_scope(self):
        path = f"/api/transactions/{self.rows[0].id}/statistics"
        for body in ({}, {"excluded_from_statistics": "false"}, {"excluded_from_statistics": None}):
            self.assertEqual((await self.client.patch(path, json=body)).status_code, 422)
        main.app.dependency_overrides[dependencies.current_owner_id] = lambda: self.other
        self.assertEqual((await self.client.patch(path, json={"excluded_from_statistics": True})).status_code, 404)

    async def test_bank_reimport_keeps_exclusion_and_new_transactions_are_included(self):
        await self.toggle(self.rows[0], True)
        async with database.tenant_session(self.owner) as db:
            account = await db.scalar(select(Account))
        raw = {"booking_date": "2098-06-01", "transaction_amount": {"amount": "100.00", "currency": "EUR"},
               "credit_debit_indicator": "CRDT"}
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"transactions": [
            {**raw, "entry_reference": "0"}, {**raw, "entry_reference": "new"},
        ]}))
        client_class = httpx.AsyncClient
        with (patch.object(bank_client, "bank_api_headers", AsyncMock(return_value={})),
              patch.object(bank_client.httpx, "AsyncClient", side_effect=lambda **kwargs: client_class(transport=transport))):
            self.assertEqual(await bank_sync_service.fetch_transactions(account, self.owner), 1)
        rows = (await self.client.get("/api/transactions")).json()
        self.assertEqual(len(rows), 4)
        self.assertEqual([row["id"] for row in rows if row["excluded_from_statistics"]], [self.rows[0].id])

    async def test_upgrade_existing_schema_preserves_rows_and_defaults(self):
        schema = database.tenant_schema_name(self.owner)
        async with database.engine.begin() as connection:
            await connection.execute(text(f'ALTER TABLE "{schema}".transactions DROP COLUMN excluded_from_statistics'))
            await schema_ddl._upgrade_tenant_schema(connection, schema, self.owner)
            await schema_ddl._upgrade_tenant_schema(connection, schema, self.owner)
        rows = (await self.client.get("/api/transactions")).json()
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["excluded_from_statistics"] is False for row in rows))
if __name__ == "__main__":
    unittest.main()
