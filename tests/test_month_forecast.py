"""Forecast API contract, using private in-memory demo databases."""
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from decimal import Decimal
import asyncio
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.schema import DropSchema

from app import database, dates, demo, main, schema as schema_ddl
from app.models import Account, BankSession, Category, ExpenseSplit, PlanItem, PlanItemTransaction, Transaction
from app.services import planning
from app.services.transactions import planning_month
from app.services.categories import ensure_month_categories
from app.schemas import NewPlanItem


class MonthForecastTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patches = [
            patch.object(dates, "current_month", return_value="2026-11"),
            patch.object(database, "ensure_tenant_schema", side_effect=AssertionError("Persistent database accessed")),
        ]
        for patcher in self.patches:
            patcher.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://test")
        await self.client.post("/auth/demo/start")
        self.owner = demo.PREFIX + self.client.cookies[demo.COOKIE]
        self.account = await self.add_account("EUR", "1000")

    async def asyncTearDown(self):
        await self.client.aclose()
        await demo.store.close()
        for patcher in reversed(self.patches):
            patcher.stop()

    async def add_account(self, currency, balance="0", *, monitored=True, owner=None):
        async with demo.store.database(self.owner) as db:
            session = BankSession(owner_id=owner or self.owner, enable_session_id=str(uuid4()), bank_name="Test", country="EE", raw_data={})
            db.add(session)
            await db.flush()
            account = Account(session_id=session.id, bank_account_id=str(uuid4()), currency=currency, balance_adjustment=Decimal(balance), monitored=monitored, raw_data={})
            db.add(account)
            await db.commit()
            return account.id

    async def category(self, name="Food", budget="100", **settings):
        response = await self.client.post("/api/categories", json={"name": name, "month": "2026-11", "budget": budget})
        self.assertEqual(response.status_code, 200, response.text)
        category_id = response.json()["id"]
        if settings:
            response = await self.client.patch(f"/api/categories/{category_id}", json=settings)
            self.assertEqual(response.status_code, 200, response.text)
        return category_id

    async def transaction(self, amount="30", category_id=None, *, account=None, currency="EUR", direction="DBIT", month="2026-11", excluded=False, value_only=False):
        async with demo.store.database(self.owner) as db:
            date = datetime.fromisoformat(month + "-05T00:00:00+00:00")
            transaction = Transaction(account_id=account or self.account, external_id=str(uuid4()),
                                      amount=Decimal(amount), currency=currency, direction=direction,
                                      booking_date=None if value_only else date, value_date=date,
                                      category_id=category_id, excluded_from_statistics=excluded, raw_data={})
            db.add(transaction)
            await db.commit()
            return transaction.id

    async def plan(self, month="2026-11"):
        response = await self.client.get("/api/plan", params={"month": month, "language": "en"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def item(self, name="Additional expense", amount="50", direction="DBIT", **settings):
        response = await self.client.post("/api/plan/items", json={
            "name": name, "amount": amount, "direction": direction, "currency": "EUR", "month": "2026-11", **settings,
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_balance_uses_only_remaining_plan_and_preserves_actual_cash(self):
        food = await self.category(budget="100.10")
        income = await self.category("Salary", "-2000")
        await self.transaction("30.05", food)
        await self.transaction("500", income, direction="CRDT")
        await self.transaction("10", excluded=True)
        await self.item("Gift", "40", "CRDT")
        await self.item("Repair", "20")
        plan = await self.plan()
        self.assertTrue(plan["is_current_month"])
        self.assertEqual(plan["totals"], [{"currency": "EUR", "balance": "1459.95", "expected_income": "1540.00", "expected_expense": "90.05", "closing_balance": "2909.90"}])
        row = next(row for row in plan["categories"] if row["id"] == food)
        self.assertEqual((row["budget"], row["actual"], row["remaining"]), ("100.10", "30.05", "70.05"))
        self.assertIsNone((await self.plan("2026-10"))["totals"][0]["closing_balance"])

    async def test_payment_auto_partial_closed_budget_overrun_and_disabled(self):
        electricity = await self.category("Electricity", "160", forecast_mode="payment")
        food = await self.category("Food", "20")
        disabled = await self.category("Disabled", "900", enabled=False)
        await self.transaction("145", electricity)
        await self.transaction("30", food)
        rows = {row["id"]: row for row in (await self.plan())["categories"]}
        self.assertEqual(rows[electricity]["remaining"], "0.00")
        self.assertEqual(rows[food]["remaining"], "0.00")
        self.assertEqual(rows[disabled]["remaining"], "0.00")
        await self.client.patch(f"/api/categories/{electricity}", json={"payment_status": "open"})
        rows = {row["id"]: row for row in (await self.plan())["categories"]}
        self.assertEqual(rows[electricity]["remaining"], "15.00")
        await self.client.patch(f"/api/categories/{electricity}", json={"payment_status": "closed"})
        rows = {row["id"]: row for row in (await self.plan())["categories"]}
        self.assertEqual(rows[electricity]["remaining"], "0.00")

    async def test_month_copy_preserves_settings_and_resets_payment_status(self):
        await self.category("Loan", "150", forecast_mode="payment", currency="USD", payment_status="closed")
        row = (await self.plan("2026-12"))["categories"][0]
        self.assertEqual((row["name"], row["budget"], row["currency"], row["forecast_mode"], row["payment_status"], row["remaining"]),
                         ("Loan", "150.00", "USD", "payment", "auto", "150.00"))
        self.assertEqual((await self.plan("2026-12"))["items"], [])

    async def test_splits_reserved_once_and_delete_releases_allocations(self):
        first = await self.category("Food", "100")
        second = await self.category("Home", "100")
        transaction_id = await self.transaction("60", first)
        async with demo.store.database(self.owner) as db:
            db.add_all([ExpenseSplit(transaction_id=transaction_id, category_id=first, amount=20), ExpenseSplit(transaction_id=transaction_id, category_id=second, amount=40)])
            await db.commit()
        rows = {row["id"]: row for row in (await self.plan())["categories"]}
        self.assertEqual((rows[first]["actual"], rows[second]["actual"]), ("20.00", "40.00"))
        item = await self.item(amount="75", transaction_ids=[transaction_id])
        self.assertEqual((item["actual"], item["remaining"]), ("60.00", "0.00"))
        plan = await self.plan()
        self.assertEqual([row["actual"] for row in plan["categories"]], ["0.00", "0.00"])
        self.assertEqual(plan["totals"][0]["expected_expense"], "200.00")
        response = await self.client.delete(f'/api/plan/items/{item["id"]}')
        self.assertEqual(response.json(), {"id": item["id"], "deleted": True})
        self.assertEqual((await self.plan())["totals"][0]["expected_expense"], "140.00")

    async def test_one_off_partial_links_and_duplicate_rejection(self):
        first = await self.transaction("20")
        second = await self.transaction("30")
        item = await self.item(amount="100", transaction_ids=[first, second], payment_status="open")
        self.assertEqual((item["actual"], item["remaining"]), ("50.00", "50.00"))
        response = await self.client.post("/api/plan/items", json={"name": "Duplicate", "month": "2026-11", "amount": "20", "direction": "DBIT", "currency": "EUR", "transaction_ids": [first]})
        self.assertEqual(response.status_code, 409, response.text)
        response = await self.client.patch(f'/api/plan/items/{item["id"]}', json={"transaction_ids": [second], "payment_status": "closed"})
        self.assertEqual((response.json()["actual"], response.json()["remaining"]), ("30.00", "0.00"))
        released = await self.item(transaction_ids=[first])
        self.assertEqual(released["actual"], "20.00")

    async def test_income_links_and_recurring_income_keep_actual_out_of_remaining(self):
        salary = await self.category("Salary", "-1500", forecast_mode="payment")
        await self.transaction("1400", salary, direction="CRDT")
        gift = await self.transaction("20", direction="CRDT")
        item = await self.item("Gift", "50", "CRDT", transaction_ids=[gift], payment_status="open")
        self.assertEqual((item["actual"], item["remaining"]), ("20.00", "30.00"))
        plan = await self.plan()
        self.assertEqual(plan["categories"][0]["remaining"], "0.00")
        self.assertEqual(plan["totals"][0]["closing_balance"], "2450.00")
        response = await self.client.patch(f'/api/plan/items/{item["id"]}', json={"payment_status": "auto"})
        self.assertEqual(response.json()["remaining"], "0.00")

    async def test_invalid_links_and_invalid_payloads_are_rejected(self):
        untracked = await self.add_account("EUR", monitored=False)
        foreign = await self.add_account("EUR", owner="other-owner")
        invalid_ids = [
            await self.transaction(excluded=True), await self.transaction(account=untracked),
            await self.transaction(account=foreign), await self.transaction(month="2026-10"),
            await self.transaction(currency="USD"), await self.transaction(direction="CRDT"),
            await self.transaction(amount="0"), await self.transaction(amount="-10"), 999999,
        ]
        for transaction_id in invalid_ids:
            with self.subTest(transaction_id=transaction_id):
                response = await self.client.post("/api/plan/items", json={"name": "Invalid", "month": "2026-11", "amount": "10", "direction": "DBIT", "currency": "EUR", "transaction_ids": [transaction_id]})
                self.assertEqual(response.status_code, 400, response.text)
        for invalid in ({"name": " "}, {"amount": "NaN"}, {"amount": "-1"}, {"amount": "0.001"}, {"currency": "EURO"}, {"payment_status": "yes"}, {"transaction_ids": [1, 1]}):
            response = await self.client.post("/api/plan/items", json={"name": "Invalid", "month": "2026-11", "amount": "10", "direction": "DBIT", "currency": "EUR", **invalid})
            self.assertEqual(response.status_code, 422, response.text)
        item = await self.item()
        self.assertEqual((await self.client.patch(f'/api/plan/items/{item["id"]}', json={"amount": None})).status_code, 422)
        self.assertEqual((await self.client.patch(f'/api/plan/items/{item["id"]}', json={"month": "2026-12"})).status_code, 422)
        self.assertEqual((await self.client.get("/api/plan?month=2026-13")).status_code, 400)
        self.assertEqual(len((await self.plan())["items"]), 1)

    async def test_linked_transactions_rechecked_after_exclusion_untracking_move_and_delete(self):
        food = await self.category()
        transaction_id = await self.transaction("30", food)
        item = await self.item(transaction_ids=[transaction_id])
        async with demo.store.database(self.owner) as db:
            transaction = await db.get(Transaction, transaction_id)
            transaction.excluded_from_statistics = True
            await db.commit()
        plan = await self.plan()
        self.assertEqual((plan["items"][0]["actual"], plan["items"][0]["remaining"]), ("0.00", "50.00"))
        self.assertEqual(plan["totals"][0]["balance"], "970.00")
        async with demo.store.database(self.owner) as db:
            transaction = await db.get(Transaction, transaction_id)
            transaction.excluded_from_statistics = False
            account = await db.get(Account, self.account)
            account.monitored = False
            await db.commit()
        self.assertEqual((await self.plan())["items"][0]["actual"], "0.00")
        async with demo.store.database(self.owner) as db:
            account = await db.get(Account, self.account)
            account.monitored = True
            transaction = await db.get(Transaction, transaction_id)
            transaction.booking_date = datetime(2026, 12, 1, tzinfo=timezone.utc)
            await db.commit()
        self.assertEqual((await self.plan())["items"][0]["actual"], "0.00")
        async with demo.store.database(self.owner) as db:
            await db.execute(delete(Transaction).where(Transaction.id == transaction_id))
            await db.commit()
        self.assertEqual((await self.plan())["items"][0]["transaction_ids"], [])

    async def test_currency_direction_value_date_and_tracked_balance_boundaries(self):
        food = await self.category(budget="100")
        usd = await self.add_account("USD", "500")
        await self.add_account(None, "70")
        await self.add_account("EUR", "9999", monitored=False)
        await self.add_account("EUR", "9999", owner="other-owner")
        await self.transaction("20", food, value_only=True)
        await self.transaction("30", food, account=usd, currency="USD")
        await self.transaction("40", food, direction="CRDT")
        await self.item(amount="50", currency="USD")
        plan = await self.plan()
        self.assertEqual(plan["categories"][0]["actual"], "20.00")
        totals = {row["currency"]: row for row in plan["totals"]}
        self.assertEqual((totals["EUR"]["balance"], totals["EUR"]["closing_balance"]), ("1020.00", "940.00"))
        self.assertEqual((totals["USD"]["balance"], totals["USD"]["closing_balance"]), ("470.00", "420.00"))
        self.assertEqual(totals[None]["balance"], "70.00")

    async def test_planning_month_matches_stored_utc_date_without_changing_display_date(self):
        transaction_id = await self.transaction()
        async with demo.store.database(self.owner) as db:
            transaction = await db.get(Transaction, transaction_id)
            transaction.booking_date = datetime(2026, 10, 31, 22, 30, tzinfo=timezone.utc)
            transaction.raw_data = {"booking_date": "2026-11-01T00:30:00+02:00"}
            await db.commit()
        rows = (await self.client.get("/api/transactions")).json()
        self.assertEqual(rows[0]["planning_month"], "2026-10")
        self.assertTrue(rows[0]["date"].startswith("2026-11-01"))
        response = await self.client.post("/api/plan/items", json={"name": "Wrong month", "month": "2026-11", "amount": "30", "direction": "DBIT", "currency": "EUR", "transaction_ids": [transaction_id]})
        self.assertEqual(response.status_code, 400)
        response = await self.client.post("/api/plan/items", json={"name": "Right month", "month": "2026-10", "amount": "30", "direction": "DBIT", "currency": "EUR", "transaction_ids": [transaction_id]})
        self.assertEqual(response.status_code, 200, response.text)
        for booking, value, expected in (
            (datetime.fromisoformat("2026-11-01T00:30:00+02:00"), None, "2026-10"),
            (None, datetime(2026, 11, 1), "2026-11"),
            (None, None, None),
        ):
            self.assertEqual(planning_month(Transaction(booking_date=booking, value_date=value)), expected)

    async def test_complete_month_not_limited_by_transaction_pagination(self):
        food = await self.category(budget="10000")
        async with demo.store.database(self.owner) as db:
            db.add_all([Transaction(account_id=self.account, external_id=str(index), amount=Decimal("0.01"), currency="EUR", direction="DBIT",
                                    booking_date=datetime(2026, 11, 5, tzinfo=timezone.utc), category_id=food, raw_data={}) for index in range(1100)])
            await db.commit()
        plan = await self.plan()
        self.assertEqual((plan["categories"][0]["actual"], plan["categories"][0]["remaining"]), ("11.00", "9989.00"))

    async def test_demo_tenant_separation_and_write_response_compatibility(self):
        category_id = await self.category()
        response = await self.client.patch(f"/api/categories/{category_id}", json={"forecast_mode": "payment", "currency": "USD"})
        self.assertEqual(set(response.json()), {"id", "name", "budget", "enabled", "icon"})
        item = await self.item()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://test") as other:
            await other.post("/auth/demo/start")
            self.assertEqual((await other.get("/api/plan?month=2026-11")).json()["items"], [])
            self.assertEqual((await other.patch(f'/api/plan/items/{item["id"]}', json={"name": "Stolen"})).status_code, 404)
            self.assertEqual((await other.delete(f'/api/plan/items/{item["id"]}')).status_code, 404)


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "A disposable PostgreSQL database is required")
class ForecastMigrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_plan_uses_one_snapshot_during_concurrent_link_and_bank_changes(self):
        owner = f"forecast-snapshot-{uuid4()}@example.invalid"
        schema = database.tenant_schema_name(owner)
        try:
            async with database.tenant_session(owner) as db:
                session = BankSession(owner_id=owner, enable_session_id=str(uuid4()), bank_name="TEST", country="EE", raw_data={})
                category = Category(owner_id=owner, name="Food", month="2026-11", budget=100)
                db.add_all([session, category])
                await db.flush()
                account = Account(session_id=session.id, bank_account_id=str(uuid4()), currency="EUR", balance_adjustment=1000, raw_data={})
                db.add(account)
                await db.flush()
                transaction = Transaction(account_id=account.id, external_id=str(uuid4()), amount=20, direction="DBIT", currency="EUR",
                                          booking_date=datetime(2026, 11, 1, tzinfo=timezone.utc), category_id=category.id, raw_data={})
                db.add(transaction)
                await db.commit()
                transaction_id, account_id, category_id = transaction.id, account.id, category.id

            original_session = database.tenant_session
            changed = False

            @asynccontextmanager
            async def interleave(owner_id, **options):
                nonlocal changed
                async with original_session(owner_id, **options) as db:
                    original_execute = db.execute

                    async def execute(statement, *args, **kwargs):
                        nonlocal changed
                        result = await original_execute(statement, *args, **kwargs)
                        # Commit changes after the items query, before the links and balances queries.
                        descriptions = getattr(statement, "column_descriptions", [])
                        if not changed and descriptions and descriptions[0].get("entity") is PlanItem:
                            changed = True
                            await planning.create_item(owner_id=owner, item_input=NewPlanItem(
                                name="Concurrent item", month="2026-11", amount="20", direction="DBIT", currency="EUR", transaction_ids=[transaction_id],
                            ))
                            async with original_session(owner) as writer:
                                writer.add(Transaction(account_id=account_id, external_id=str(uuid4()), amount=30, direction="DBIT", currency="EUR",
                                                       booking_date=datetime(2026, 11, 2, tzinfo=timezone.utc), category_id=category_id, raw_data={}))
                                await writer.commit()
                        return result

                    db.execute = execute
                    yield db

            with patch.object(database, "tenant_session", interleave), patch.object(dates, "current_month", return_value="2026-11"):
                before = await planning.list_plan(owner_id=owner, month="2026-11", language="en")
            self.assertTrue(changed)
            self.assertEqual(before["items"], [])
            self.assertEqual(before["categories"][0]["actual"], "20.00")
            self.assertEqual((before["totals"][0]["balance"], before["totals"][0]["closing_balance"]), ("980.00", "900.00"))
            after = await planning.list_plan(owner_id=owner, month="2026-11", language="en")
            self.assertEqual(after["items"][0]["name"], "Concurrent item")
            self.assertEqual(after["categories"][0]["actual"], "30.00")
            self.assertEqual(after["totals"][0]["balance"], "950.00")
        finally:
            async with database.engine.begin() as connection:
                await connection.execute(DropSchema(schema, cascade=True, if_exists=True))
            database._ready_tenant_schemas.discard(schema)
            await database.engine.dispose()

    async def test_existing_category_upgrade_and_plan_change_triggers(self):
        owner = f"forecast-migration-{uuid4()}@example.invalid"
        schema = database.tenant_schema_name(owner)
        try:
            async with database.tenant_session(owner) as db:
                db.add(Category(owner_id=owner, name="Existing", month="2026-11", budget=42))
                await db.commit()
            async with database.engine.begin() as connection:
                for name in ("currency", "forecast_mode", "payment_status"):
                    await connection.execute(schema_ddl._tenant_ddl("ALTER TABLE %(schema)s.categories DROP COLUMN %(column)s", schema, column=name))
                await schema_ddl._upgrade_tenant_schema(connection, schema, owner)
                triggers = (await connection.execute(text("""
                    SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                    JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname=:schema AND t.tgname='data_changed'
                """), {"schema": schema})).scalars().all()
                self.assertIn("plan_items", triggers)
                self.assertIn("plan_item_transactions", triggers)
            async with database.tenant_session(owner) as db:
                category = await db.scalar(select(Category))
                self.assertEqual((category.budget, category.currency, category.forecast_mode, category.payment_status), (Decimal("42"), "EUR", "budget", "auto"))
        finally:
            async with database.engine.begin() as connection:
                await connection.execute(DropSchema(schema, cascade=True, if_exists=True))
            database._ready_tenant_schemas.discard(schema)
            await database.engine.dispose()

    async def test_concurrent_month_creation_and_plan_link_owner_boundary(self):
        owner = f"forecast-concurrency-{uuid4()}@example.invalid"
        schema = database.tenant_schema_name(owner)
        try:
            await database.ensure_tenant_schema(owner)

            async def ensure():
                async with database.tenant_session(owner) as db:
                    return await ensure_month_categories(db, owner, "2026-11", "en")

            months = await asyncio.gather(ensure(), ensure())
            self.assertEqual([len(rows) for rows in months], [5, 5])
            self.assertEqual({row.id for row in months[0]}, {row.id for row in months[1]})
            async with database.tenant_session(owner) as db:
                session = BankSession(owner_id=owner, enable_session_id=str(uuid4()), bank_name="TEST", country="EE", raw_data={})
                db.add(session)
                await db.flush()
                account = Account(session_id=session.id, bank_account_id=str(uuid4()), currency="EUR", raw_data={})
                db.add(account)
                await db.flush()
                transaction = Transaction(account_id=account.id, external_id=str(uuid4()), amount=20, direction="DBIT", currency="EUR",
                                          booking_date=datetime(2026, 11, 1, tzinfo=timezone.utc), raw_data={})
                foreign_item = PlanItem(owner_id="another-owner", name="Foreign", amount=20, direction="DBIT", currency="EUR", month="2026-11")
                db.add_all([transaction, foreign_item])
                await db.commit()
                transaction_id, foreign_id = transaction.id, foreign_item.id
            async with database.tenant_session(owner) as db:
                db.add(PlanItemTransaction(item_id=foreign_id, transaction_id=transaction_id))
                with self.assertRaises(DBAPIError) as error:
                    await db.commit()
                self.assertIn("owners must match", str(error.exception))
                await db.rollback()
            item = await planning.create_item(owner_id=owner, item_input=NewPlanItem(name="Own", month="2026-11", amount="25", direction="DBIT", currency="EUR", transaction_ids=[transaction_id]))
            self.assertEqual((item["actual"], item["remaining"]), ("20.00", "0.00"))
            plan = await planning.list_plan(owner_id=owner, month="2026-11", language="en")
            self.assertEqual([row["name"] for row in plan["items"]], ["Own"])
        finally:
            async with database.engine.begin() as connection:
                await connection.execute(DropSchema(schema, cascade=True, if_exists=True))
            database._ready_tenant_schemas.discard(schema)
            await database.engine.dispose()
