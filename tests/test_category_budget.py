"""Category budget aggregation against a tenant-bound PostgreSQL schema."""
from datetime import datetime, timezone
from uuid import uuid4
import unittest

from pydantic import ValidationError
from sqlalchemy import insert, select, text
import httpx

from app import database, dependencies, main, schemas
from app.models import Account, BankSession, Category, ExpenseSplit, Transaction
from app.services import categories as categories_service


class CategoryBudgetTest(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_default_set_renames_survive_localization_and_month_copy(self):
        owner = f"rename-defaults-{uuid4()}@example.com"
        try:
            main.app.dependency_overrides[dependencies.current_owner_id] = lambda: owner
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                initial = await client.get("/api/categories", params={"month": "2098-06", "language": "ru"})
                self.assertEqual(initial.status_code, 200, initial.text)
                by_icon = {row["icon"]: row for row in initial.json()}
                english = categories_service.category_defaults("en")
                for name, icon in english:
                    response = await client.patch(f'/api/categories/{by_icon[icon]["id"]}', json={"name": name})
                    self.assertEqual(response.status_code, 200, response.text)

                current = await client.get("/api/categories", params={"month": "2098-06", "language": "ru"})
                self.assertEqual({(row["name"], row["icon"]) for row in current.json()}, set(english))
                copied = await client.get("/api/categories", params={"month": "2098-07", "language": "ru"})
                self.assertEqual({(row["name"], row["icon"]) for row in copied.json()}, set(english))
                async with database.tenant_session(owner) as db:
                    flags = (await db.scalars(select(Category.name_customized).where(Category.month.in_(("2098-06", "2098-07"))))).all()
                    self.assertEqual(flags, [True] * 10)
        finally:
            main.app.dependency_overrides.pop(dependencies.current_owner_id, None)
            schema = database.tenant_schema_name(owner)
            async with database.engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            database._ready_tenant_schemas.discard(schema)
            await database.engine.dispose()

    async def test_category_rename_preserves_settings_and_assignments(self):
        owner = f"rename-{uuid4()}@example.com"
        try:
            main.app.dependency_overrides[dependencies.current_owner_id] = lambda: owner
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                created = await client.post("/api/categories", json={"name": "Food", "month": "2098-06", "budget": 125, "icon": "🍽️"})
                self.assertEqual(created.status_code, 200, created.text)
                category_id = created.json()["id"]

                async with database.tenant_session(owner) as db:
                    session = BankSession(owner_id=owner, enable_session_id=str(uuid4()), bank_name="TEST", country="EE", raw_data={})
                    db.add(session)
                    await db.flush()
                    account = Account(session_id=session.id, bank_account_id=str(uuid4()), monitored=True, raw_data={})
                    db.add(account)
                    await db.flush()
                    direct = Transaction(account_id=account.id, external_id="direct", amount=10, currency="EUR", direction="DBIT", category_id=category_id, raw_data={})
                    split_transaction = Transaction(account_id=account.id, external_id="split", amount=20, currency="EUR", direction="DBIT", raw_data={})
                    db.add_all((direct, split_transaction))
                    await db.flush()
                    db.add(ExpenseSplit(transaction_id=split_transaction.id, category_id=category_id, amount=20))
                    await db.commit()
                    direct_id, split_id = direct.id, split_transaction.id

                renamed = await client.patch(f"/api/categories/{category_id}", json={"name": "  Groceries  "})
                self.assertEqual(renamed.status_code, 200, renamed.text)
                self.assertEqual(renamed.json(), {"id": category_id, "name": "Groceries", "budget": 125, "enabled": True, "icon": "🍽️"})
                rows = (await client.get("/api/categories", params={"month": "2098-06"})).json()
                self.assertEqual([(row["id"], row["name"]) for row in rows], [(category_id, "Groceries")])

                async with database.tenant_session(owner) as db:
                    self.assertEqual((await db.scalar(select(Transaction.category_id).where(Transaction.id == direct_id))), category_id)
                    self.assertEqual((await db.scalar(select(ExpenseSplit.category_id).where(ExpenseSplit.transaction_id == split_id))), category_id)
        finally:
            main.app.dependency_overrides.pop(dependencies.current_owner_id, None)
            schema = database.tenant_schema_name(owner)
            async with database.engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            database._ready_tenant_schemas.discard(schema)
            await database.engine.dispose()

    async def test_category_rename_validation_and_month_tenant_scope(self):
        alice = f"rename-alice-{uuid4()}@example.com"
        bob = f"rename-bob-{uuid4()}@example.com"
        active_owner = {"id": alice}
        try:
            main.app.dependency_overrides[dependencies.current_owner_id] = lambda: active_owner["id"]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                async def create(name: str, month: str) -> int:
                    response = await client.post("/api/categories", json={"name": name, "month": month})
                    self.assertEqual(response.status_code, 200, response.text)
                    return response.json()["id"]

                first = await create("Food", "2098-06")
                second = await create("Travel", "2098-06")
                await create("Groceries", "2098-07")
                self.assertEqual((await client.patch(f"/api/categories/{first}", json={"name": "Food"})).status_code, 200)
                self.assertEqual((await client.patch(f"/api/categories/{first}", json={"name": "  Travel  "})).status_code, 409)
                for name in ("", "   ", "x" * 101):
                    response = await client.patch(f"/api/categories/{first}", json={"name": name})
                    self.assertEqual(response.status_code, 422, response.text)
                renamed = await client.patch(f"/api/categories/{first}", json={"name": " Groceries "})
                self.assertEqual(renamed.status_code, 200, renamed.text)
                self.assertEqual(renamed.json()["name"], "Groceries")

                active_owner["id"] = bob
                await create("Groceries", "2098-06")
                inaccessible = await client.patch(f"/api/categories/{second}", json={"name": "Other"})
                self.assertEqual(inaccessible.status_code, 404)
                active_owner["id"] = alice
                self.assertEqual((await client.get("/api/categories", params={"month": "2098-06"})).json()[0]["name"], "Groceries")
        finally:
            main.app.dependency_overrides.pop(dependencies.current_owner_id, None)
            async with database.engine.begin() as connection:
                for owner in (alice, bob):
                    schema = database.tenant_schema_name(owner)
                    await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
                    database._ready_tenant_schemas.discard(schema)
            await database.engine.dispose()

    def test_default_categories_follow_language_without_overwriting_customizations(self):
        categories = [
            Category(owner_id="owner", name=name, icon=icon, month="2098-06")
            for name, icon in categories_service.CATEGORY_DEFAULTS["ru"]
        ]

        self.assertTrue(categories_service.localize_untouched_defaults(categories, "en"))
        self.assertEqual([category.name for category in categories], ["Food", "Transport", "Home", "Subscriptions", "Other"])

        categories[0].name = "Groceries"
        self.assertFalse(categories_service.localize_untouched_defaults(categories, "et"))
        self.assertEqual(categories[0].name, "Groceries")

    async def test_category_api_uses_selected_language_for_initial_defaults(self):
        owner = f"localized-{uuid4()}@example.com"
        try:
            main.app.dependency_overrides[dependencies.current_owner_id] = lambda: owner
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                response = await client.get("/api/categories", params={"month": "2098-07", "language": "et"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual({row["name"] for row in response.json()}, {"Toit", "Transport", "Kodu", "Tellimused", "Muu"})

                response = await client.get("/api/categories", params={"month": "2098-07", "language": "en"})
                self.assertEqual({row["name"] for row in response.json()}, {"Food", "Transport", "Home", "Subscriptions", "Other"})
        finally:
            main.app.dependency_overrides.pop(dependencies.current_owner_id, None)
            schema = database.tenant_schema_name(owner)
            async with database.engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            database._ready_tenant_schemas.discard(schema)
            await database.engine.dispose()

    def test_budget_accepts_both_signs_but_rejects_nonfinite_values(self):
        for budget in (-1500.25, 0, 100):
            self.assertEqual(schemas.NewCategory(name="Salary", month="2098-06", budget=budget).budget, budget)
            self.assertEqual(schemas.CategorySettings(budget=budget).budget, budget)
        for budget in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValidationError):
                schemas.NewCategory(name="Salary", month="2098-06", budget=budget)
            with self.assertRaises(ValidationError):
                schemas.CategorySettings(budget=budget)

    async def test_monthly_totals_include_splits_without_list_limits(self):
        owner = f"budget-{uuid4()}@example.com"
        try:
            async with database.tenant_session(owner) as db:
                session = BankSession(owner_id=owner, enable_session_id=str(uuid4()), bank_name="TEST", country="EE", raw_data={})
                db.add(session)
                await db.flush()
                accounts = [Account(session_id=session.id, bank_account_id=str(uuid4()), monitored=active, raw_data={}) for active in (True, False)]
                categories = [Category(owner_id=owner, name=str(uuid4()), month="2098-06", budget=100, enabled=active) for active in (True, False)]
                db.add_all(accounts + categories)
                await db.flush()
                base = dict(account_id=accounts[0].id, booking_date=datetime(2098, 6, 1, tzinfo=timezone.utc), amount=0.01, currency="EUR", direction="DBIT", category_id=categories[0].id, raw_data={})
                await db.execute(insert(Transaction), [{**base, "external_id": str(i)} for i in range(5001)])
                split = Transaction(**{**base, "external_id": "split", "amount": 10})
                db.add(split)
                await db.flush()
                db.add_all([ExpenseSplit(transaction_id=split.id, category_id=categories[0].id, amount=3), ExpenseSplit(transaction_id=split.id, category_id=categories[1].id, amount=7)])
                income_split = Transaction(**{**base, "external_id": "income-split", "amount": 100, "direction": "CRDT"})
                db.add(income_split)
                await db.flush()
                db.add_all([ExpenseSplit(transaction_id=income_split.id, category_id=categories[0].id, amount=30), ExpenseSplit(transaction_id=income_split.id, category_id=categories[1].id, amount=70)])
                for index, overrides in enumerate([
                    {"amount": 10}, {"amount": 100, "account_id": accounts[1].id},
                    {"amount": 100, "direction": "CRDT"},
                    {"amount": 100, "booking_date": datetime(2098, 7, 1, tzinfo=timezone.utc)},
                    {"amount": 20, "booking_date": None, "value_date": datetime(2098, 6, 2, tzinfo=timezone.utc)},
                    {"amount": 100, "booking_date": None, "value_date": datetime(2098, 7, 2, tzinfo=timezone.utc)},
                    {"amount": 100, "category_id": None},
                    {"amount": 100, "direction": "CRDT", "account_id": accounts[1].id},
                    {"amount": 100, "direction": "CRDT", "booking_date": datetime(2098, 7, 1, tzinfo=timezone.utc)},
                    {"amount": 100, "direction": "CRDT", "category_id": None},
                ]):
                    db.add(Transaction(**{**base, **overrides, "external_id": f"extra-{index}"}))
                await db.commit()

            main.app.dependency_overrides[dependencies.current_owner_id] = lambda: owner
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                response = await client.get("/api/categories", params={"month": "2098-06"})
                self.assertEqual(response.status_code, 200)
                result = {row["id"]: row for row in response.json()}
                self.assertEqual(result[categories[0].id]["spent"], 83.01)
                self.assertEqual(result[categories[1].id]["spent"], 7)
                self.assertEqual(result[categories[0].id]["earned"], 130)
                self.assertEqual(result[categories[1].id]["earned"], 70)

                created = await client.post("/api/categories", json={"name": "Salary", "month": "2098-06", "budget": -1500.25})
                self.assertEqual(created.status_code, 200, created.text)
                self.assertEqual(created.json()["budget"], -1500.25)
                updated = await client.patch(f'/api/categories/{created.json()["id"]}', json={"budget": -2000.50})
                self.assertEqual(updated.status_code, 200, updated.text)
                self.assertEqual(updated.json()["budget"], -2000.50)
                current = (await client.get("/api/categories", params={"month": "2098-06"})).json()
                salary = next(row for row in current if row["name"] == "Salary")
                self.assertEqual((salary["budget"], salary["earned"]), (-2000.50, 0))
                copied = (await client.get("/api/categories", params={"month": "2098-07"})).json()
                self.assertEqual(next(row for row in copied if row["name"] == "Salary")["budget"], -2000.50)
        finally:
            main.app.dependency_overrides.pop(dependencies.current_owner_id, None)
            schema = database.tenant_schema_name(owner)
            async with database.engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            database._ready_tenant_schemas.discard(schema)
            await database.engine.dispose()
if __name__ == "__main__":
    unittest.main()
