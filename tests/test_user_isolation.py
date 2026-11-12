"""PostgreSQL schema-per-user isolation integration tests."""
from datetime import datetime, timezone
from uuid import uuid4
import unittest

from sqlalchemy import select, text
import httpx

from app import database, dependencies, main
from app.models import Account, BankSession, Category, Transaction


class UserIsolationTest(unittest.IsolatedAsyncioTestCase):
    async def test_each_passi_user_is_confined_to_a_distinct_schema(self):
        alice = f"alice-{uuid4()}@example.com"
        bob = f"bob-{uuid4()}@example.com"
        owners = (alice, bob)
        try:
            for owner, amount in ((alice, 10), (bob, 90)):
                async with database.tenant_session(owner) as db:
                    bank_session = BankSession(owner_id=owner, enable_session_id=str(uuid4()), bank_name="TEST", country="EE", raw_data={})
                    db.add(bank_session)
                    await db.flush()
                    account = Account(session_id=bank_session.id, bank_account_id=str(uuid4()), name=owner, monitored=True, raw_data={})
                    category = Category(owner_id=owner, name="Food", month="2097-04", budget=100)
                    db.add_all((account, category))
                    await db.flush()
                    db.add(Transaction(account_id=account.id, external_id=str(uuid4()), booking_date=datetime(2097, 4, 2, tzinfo=timezone.utc), amount=amount, currency="EUR", direction="DBIT", category_id=category.id, raw_data={}))
                    await db.commit()

            self.assertNotEqual(database.tenant_schema_name(alice), database.tenant_schema_name(bob))
            self.assertNotIn("alice", database.tenant_schema_name(alice))
            async with database.tenant_session(alice) as db:
                self.assertEqual([row.name for row in (await db.scalars(select(Account))).all()], [alice])
            async with database.tenant_session(bob) as db:
                self.assertEqual([row.name for row in (await db.scalars(select(Account))).all()], [bob])

            active_owner = {"id": alice}
            main.app.dependency_overrides[dependencies.current_owner_id] = lambda: active_owner["id"]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                self.assertEqual([row["name"] for row in (await client.get("/api/accounts")).json()], [alice])
                self.assertEqual((await client.get("/api/summary")).json()[0]["total"], 10)
                copied = await client.get("/api/categories", params={"month": "2097-05"})
                self.assertEqual(copied.status_code, 200)
                self.assertEqual([row["name"] for row in copied.json()], ["Food"])
                active_owner["id"] = bob
                self.assertEqual([row["name"] for row in (await client.get("/api/accounts")).json()], [bob])
                self.assertEqual((await client.get("/api/summary")).json()[0]["total"], 90)

            async with database.engine.connect() as connection:
                public_tables = await connection.scalar(text("""
                    SELECT count(*) FROM pg_tables WHERE schemaname = 'public'
                    AND tablename IN ('bank_sessions','accounts','categories','transactions','expense_splits')
                """))
                self.assertEqual(public_tables, 0)
        finally:
            main.app.dependency_overrides.pop(dependencies.current_owner_id, None)
            async with database.engine.begin() as connection:
                for owner in owners:
                    schema = database.tenant_schema_name(owner)
                    await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
                    database._ready_tenant_schemas.discard(schema)
            await database.engine.dispose()
if __name__ == "__main__":
    unittest.main()
