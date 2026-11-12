"""Account balance calculations and manual resets against PostgreSQL."""
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4
import asyncio
import unittest

from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
import httpx

from app import bank_client, database, dependencies, main
from app.models import Account, BankSession, BankSyncState, Category, ExpenseSplit, Transaction
from app.services import banking


class AccountBalanceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner = f"balance-{uuid4()}@example.com"
        self.owners = [self.owner]
        self.account_id = await self.create_account(self.owner)
        main.app.dependency_overrides[dependencies.current_owner_id] = lambda: self.owner
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        main.app.dependency_overrides.pop(dependencies.current_owner_id, None)
        async with database.engine.begin() as connection:
            await connection.execute(delete(BankSyncState).where(BankSyncState.owner_id.in_(self.owners)))
            for owner in self.owners:
                schema = database.tenant_schema_name(owner)
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
                database._ready_tenant_schemas.discard(schema)
        await database.engine.dispose()

    async def create_account(self, owner, currency="EUR"):
        async with database.tenant_session(owner) as db:
            session = BankSession(owner_id=owner, enable_session_id=str(uuid4()), bank_name="TEST", country="EE", raw_data={})
            db.add(session)
            await db.flush()
            account = Account(session_id=session.id, bank_account_id=str(uuid4()), currency=currency, raw_data={})
            db.add(account)
            await db.commit()
            return account.id

    async def add_transaction(self, amount, direction="DBIT", **overrides):
        values = dict(account_id=self.account_id, external_id=str(uuid4()), amount=amount,
                      direction=direction, currency="EUR", raw_data={})
        values.update(overrides)
        async with database.tenant_session(self.owner) as db:
            transaction = Transaction(**values)
            db.add(transaction)
            await db.commit()
            return transaction.id

    async def balance(self, account_id=None):
        response = await self.client.get("/api/accounts")
        self.assertEqual(response.status_code, 200)
        return next(row["balance"] for row in response.json() if row["id"] == (account_id or self.account_id))

    async def set_balance(self, balance):
        response = await self.client.patch(f"/api/accounts/{self.account_id}/balance", json={"balance": balance})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(Decimal(response.json()["balance"]), Decimal(str(balance)))

    async def test_all_operations_and_accounts_without_month_or_list_limits(self):
        self.assertEqual(await self.balance(), "0.00")
        other_account = await self.create_account(self.owner)
        await self.add_transaction("200.00", "CRDT", booking_date=datetime(2020, 1, 1, tzinfo=timezone.utc))
        await self.add_transaction("20.00", booking_date=datetime(2026, 11, 1, tzinfo=timezone.utc))
        await self.add_transaction("-5.00")
        await self.add_transaction("-10.00", "CRDT")
        await self.add_transaction(None)
        await self.add_transaction("100.00", "????")
        await self.add_transaction("500.00", "CRDT", currency="USD")
        await self.add_transaction("2.00", currency=None)
        await self.add_transaction("7.00", account_id=other_account)
        async with database.tenant_session(self.owner) as db:
            await db.execute(insert(Transaction), [dict(account_id=self.account_id, external_id=f"cent-{i}",
                             amount=Decimal("0.01"), direction="DBIT", currency="EUR", raw_data={}) for i in range(1001)])
            await db.execute(update(Account).where(Account.id == self.account_id).values(monitored=False))
            await db.commit()
        self.assertEqual(await self.balance(), "172.99")
        self.assertEqual(await self.balance(other_account), "-7.00")

    async def test_assigning_account_currency_includes_matching_transactions(self):
        async with database.tenant_session(self.owner) as db:
            await db.execute(update(Account).where(Account.id == self.account_id).values(
                currency="XXX",
                balance_adjustment=Decimal("52.00"),
            ))
            await db.commit()
        await self.add_transaction("17.00", description="Krediitkaardi 547025******4492 kuutasu")
        self.assertEqual(await self.balance(), "52.00")
        response = await self.client.patch(
            f"/api/accounts/{self.account_id}/currency",
            json={"currency": "EUR"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"id": self.account_id, "currency": "EUR"})
        self.assertEqual(await self.balance(), "35.00")

    async def test_account_currency_validation_and_owner_scope(self):
        for currency in ("eur", "EU", "EURO", "12!", ""):
            with self.subTest(currency=currency):
                response = await self.client.patch(
                    f"/api/accounts/{self.account_id}/currency",
                    json={"currency": currency},
                )
                self.assertEqual(response.status_code, 422, response.text)
        response = await self.client.patch("/api/accounts/999999999/currency", json={"currency": "EUR"})
        self.assertEqual(response.status_code, 404)

    async def test_assigned_currency_survives_bank_reconnection(self):
        iban = "EE000000000000001234"
        async with database.tenant_session(self.owner) as db:
            await db.execute(update(Account).where(Account.id == self.account_id).values(iban=iban, currency="EUR"))
            await db.commit()
        session_data = {
            "session_id": str(uuid4()),
            "access": {},
            "accounts": [{
                "uid": "reconnected",
                "account_id": {"iban": iban},
                "name": "Card account",
                "currency": "XXX",
            }],
        }
        with (
            patch.object(bank_client, "create_session", AsyncMock(return_value=session_data)),
            patch.object(bank_client, "bank_aspsp", return_value={"name": "TEST", "country": "EE"}),
        ):
            self.assertEqual(await banking.connect_accounts(self.owner, "code"), [self.account_id])
        async with database.tenant_session(self.owner) as db:
            self.assertEqual(await db.scalar(select(Account.currency).where(Account.id == self.account_id)), "EUR")

    async def test_resets_followed_by_new_corrected_deleted_and_duplicate_transactions(self):
        transaction_id = await self.add_transaction("20.00", external_id="stable")
        await self.set_balance("100.00")
        self.assertEqual(await self.balance(), "100.00")
        await self.add_transaction("10.00")
        await self.add_transaction("5.25", "CRDT")
        self.assertEqual(await self.balance(), "95.25")
        async with database.tenant_session(self.owner) as db:
            await db.execute(pg_insert(Transaction).values(account_id=self.account_id, external_id="stable",
                             amount=20, direction="DBIT", currency="EUR", raw_data={}).on_conflict_do_nothing())
            await db.commit()
        self.assertEqual(await self.balance(), "95.25")
        async with database.tenant_session(self.owner) as db:
            await db.execute(update(Transaction).where(Transaction.id == transaction_id).values(amount=25))
            await db.commit()
        self.assertEqual(await self.balance(), "90.25")
        async with database.tenant_session(self.owner) as db:
            await db.execute(delete(Transaction).where(Transaction.id == transaction_id))
            await db.commit()
        self.assertEqual(await self.balance(), "115.25")
        await self.set_balance(0)
        self.assertEqual(await self.balance(), "0.00")
        await self.set_balance("-12.34")
        await self.add_transaction("0.01")
        self.assertEqual(await self.balance(), "-12.35")

    async def test_bank_sync_changes_saved_balance_once_per_new_operation(self):
        bank_transactions = [{
            "entry_reference": "before-reset",
            "transaction_amount": {"amount": "20.00", "currency": "EUR"},
            "credit_debit_indicator": "DBIT",
        }]
        bank_http = AsyncMock()
        bank_http.__aenter__.return_value = bank_http

        async def sync(expected_imported, expected_balance):
            bank_http.get.return_value = httpx.Response(200, json={"transactions": bank_transactions})
            response = await self.client.post("/api/sync")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {"accounts": 1, "transactions_imported": expected_imported})
            self.assertEqual(await self.balance(), expected_balance)

        with (
            patch.object(bank_client.httpx, "AsyncClient", return_value=bank_http),
            patch.object(bank_client, "bank_api_headers", AsyncMock(return_value={})),
        ):
            await sync(1, "-20.00")
            for index, (saved, after_debit, after_credit) in enumerate([
                ("100.00", "90.00", "95.25"),
                ("0.00", "-10.00", "-4.75"),
                ("-12.34", "-22.34", "-17.09"),
            ]):
                with self.subTest(saved=saved):
                    await self.set_balance(saved)
                    await sync(0, saved)
                    bank_transactions.append({
                        "entry_reference": f"debit-{index}",
                        "transaction_amount": {"amount": "10.00", "currency": "EUR"},
                        "credit_debit_indicator": "DBIT",
                    })
                    await sync(1, after_debit)
                    bank_transactions.append({
                        "entry_reference": f"credit-{index}",
                        "transaction_amount": {"amount": "5.25", "currency": "EUR"},
                        "credit_debit_indicator": "CRDT",
                    })
                    await sync(1, after_credit)
                    await sync(0, after_credit)

    async def test_categories_and_splits_do_not_change_the_balance(self):
        transaction_id = await self.add_transaction("12.00")
        await self.set_balance("500.00")
        async with database.tenant_session(self.owner) as db:
            categories = [Category(owner_id=self.owner, name=name, month="2026-11") for name in ("Food", "Other")]
            db.add_all(categories)
            await db.flush()
            await db.execute(update(Transaction).where(Transaction.id == transaction_id).values(category_id=categories[0].id))
            db.add_all([ExpenseSplit(transaction_id=transaction_id, category_id=category.id, amount=6) for category in categories])
            await db.commit()
        self.assertEqual(await self.balance(), "500.00")

    async def test_concurrent_manual_resets_do_not_accumulate_adjustments(self):
        await self.add_transaction("25.00")
        responses = await asyncio.gather(*[
            self.client.patch(f"/api/accounts/{self.account_id}/balance", json={"balance": amount})
            for amount in ("100.00", "200.00", "300.00")
        ])
        self.assertTrue(all(response.status_code == 200 for response in responses))
        saved = Decimal(await self.balance())
        self.assertIn(saved, (Decimal("100"), Decimal("200"), Decimal("300")))
        await self.add_transaction("5.00")
        self.assertEqual(Decimal(await self.balance()), saved - Decimal("5"))

    async def test_invalid_values_and_missing_account(self):
        for value in ("NaN", "Infinity", "-Infinity", "1.234", "10000000000000000", "", None):
            with self.subTest(value=value):
                response = await self.client.patch(f"/api/accounts/{self.account_id}/balance", json={"balance": value})
                self.assertEqual(response.status_code, 422, response.text)
        response = await self.client.patch("/api/accounts/999999999/balance", json={"balance": "1.00"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(await self.balance(), "0.00")
        await self.set_balance("9999999999999999.99")
        self.assertEqual(await self.balance(), "9999999999999999.99")

    async def test_tenants_with_same_account_id_cannot_change_each_others_balance(self):
        alice = self.owner
        bob = f"balance-{uuid4()}@example.com"
        self.owners.append(bob)
        bob_account = await self.create_account(bob)
        self.assertEqual(bob_account, self.account_id)
        await self.set_balance("123.45")
        self.owner = bob
        self.assertEqual(await self.balance(), "0.00")
        await self.set_balance("7.89")
        bob_only_id = await self.create_account(bob)
        self.owner = alice
        self.assertEqual(await self.balance(), "123.45")
        response = await self.client.patch(f"/api/accounts/{bob_only_id}/balance", json={"balance": "1000.00"})
        self.assertEqual(response.status_code, 404)
        self.owner = bob
        self.assertEqual(await self.balance(), "7.89")
        self.assertEqual(await self.balance(bob_only_id), "0.00")

    async def test_existing_schema_upgrade_is_idempotent_and_preserves_adjustment(self):
        schema = database.tenant_schema_name(self.owner)
        await self.add_transaction("10.50")
        async with database.engine.begin() as connection:
            # This disposable test schema simulates a table from before the feature.
            await connection.execute(text(f'ALTER TABLE "{schema}".accounts DROP COLUMN balance_adjustment'))
        database._ready_tenant_schemas.discard(schema)
        self.assertEqual(await self.balance(), "-10.50")
        await self.set_balance("50.00")
        database._ready_tenant_schemas.discard(schema)
        self.assertEqual(await self.balance(), "50.00")
        async with database.tenant_session(self.owner) as db:
            self.assertEqual(await db.scalar(select(Transaction.amount).where(Transaction.account_id == self.account_id)), Decimal("10.50"))
if __name__ == "__main__":
    unittest.main()
