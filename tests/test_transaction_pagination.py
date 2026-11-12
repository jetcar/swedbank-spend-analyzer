from datetime import datetime, timezone
from uuid import uuid4
import unittest

from sqlalchemy import text
import httpx

from app import database, dependencies, main
from app.models import Account, BankSession, Category, ExpenseSplit, Transaction


class TransactionPaginationTest(unittest.IsolatedAsyncioTestCase):
    async def test_cursor_retains_scope_exact_amounts_and_split_metadata(self):
        owners = [f"pagination-{uuid4()}@example.com" for _ in range(2)]
        try:
            ids = []
            for owner in owners:
                async with database.tenant_session(owner) as db:
                    session = BankSession(owner_id=owner, enable_session_id=str(uuid4()), bank_name="TEST", country="EE", raw_data={})
                    db.add(session)
                    await db.flush()
                    account = Account(session_id=session.id, bank_account_id=str(uuid4()), monitored=True, raw_data={})
                    hidden = Account(session_id=session.id, bank_account_id=str(uuid4()), monitored=False, raw_data={})
                    category = Category(owner_id=owner, name="Food", month="2098-06", budget=100)
                    db.add_all([account, hidden, category])
                    await db.flush()
                    rows = []
                    for index, (day, account_id) in enumerate([(20,account.id),(10,account.id),(15,account.id),(25,hidden.id)]):
                        row = Transaction(account_id=account_id, external_id=str(index), booking_date=datetime(2098,6,day,tzinfo=timezone.utc), amount="9999999999999999.99" if index == 0 else "10.25", currency="EUR", direction="DBIT", category_id=category.id, raw_data={"creditor": {"name": "Transport Company"}} if index == 0 else {})
                        db.add(row)
                        rows.append(row)
                    db.add(Transaction(account_id=account.id,external_id="other-month",booking_date=datetime(2098,7,1,tzinfo=timezone.utc),amount=99,currency="EUR",direction="CRDT",raw_data={}))
                    await db.flush()
                    db.add(ExpenseSplit(transaction_id=rows[2].id,category_id=category.id,amount="10.25"))
                    await db.commit()
                    ids.append([row.id for row in rows])
            main.app.dependency_overrides[dependencies.current_owner_id] = lambda: owners[0]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url="http://test") as client:
                params = {"month":"2098-06","limit":2,"page_by_id":"true"}
                first = (await client.get("/api/transactions",params=params)).json()
                self.assertEqual([row["id"] for row in first], [ids[0][2], ids[0][1]])
                self.assertEqual(first[0]["splits"][0]["amount"],10.25)
                self.assertIsInstance(first[0]["account_id"],int)
                second = (await client.get("/api/transactions",params={**params,"before_id":first[-1]["id"]})).json()
                self.assertEqual([row["id"] for row in second],[ids[0][0]])
                self.assertEqual(second[0]["amount_exact"],"9999999999999999.99")
                self.assertEqual(second[0]["counterparty_name"], "Transport Company")
                legacy = (await client.get("/api/transactions",params={"month":"2098-06","limit":2})).json()
                self.assertEqual([row["id"] for row in legacy],[ids[0][0],ids[0][2]])
        finally:
            main.app.dependency_overrides.pop(dependencies.current_owner_id,None)
            for owner in owners:
                schema = database.tenant_schema_name(owner)
                async with database.engine.begin() as connection:
                    await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
                database._ready_tenant_schemas.discard(schema)
            await database.engine.dispose()
