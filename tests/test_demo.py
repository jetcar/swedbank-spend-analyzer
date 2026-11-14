"""Demo requests must never reach persistent storage or another visitor."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import asyncio
import base64
import json
import unittest

from itsdangerous import TimestampSigner
from starlette.middleware.sessions import SessionMiddleware
import httpx
from sqlalchemy import select

from app import bank_client, database, demo, main, passi_auth
from app.routers import events as events_router


class DemoTest(unittest.IsolatedAsyncioTestCase):
    def bank_mock(self, environment="SANDBOX"):
        self.bank_calls = []

        def respond(request):
            self.bank_calls.append(request)
            path = request.url.path
            if path == "/application":
                return httpx.Response(200, json={"environment": environment})
            if path == "/auth":
                self.bank_state = json.loads(request.content)["state"]
                return httpx.Response(200, json={"url": "https://sandbox.example/consent"})
            if path == "/sessions":
                return httpx.Response(200, json={"session_id": "sandbox-session", "accounts": [
                    {"uid": "sandbox-account", "name": "Sandbox", "currency": "EUR", "account_id": {"iban": "EE-TEST"}},
                ]})
            if path == "/accounts/sandbox-account/transactions":
                second = "continuation_key" in request.url.params
                return httpx.Response(200, json={"transactions": [{
                    "entry_reference": "second" if second else "first",
                    "booking_date": "2026-11-02" if second else "2026-11-01",
                    "transaction_amount": {"amount": "7.50" if second else "12.50", "currency": "EUR"},
                    "credit_debit_indicator": "DBIT", "remittance_information": ["Sandbox purchase"],
                }], **({} if second else {"continuation_key": "next"})})
            raise AssertionError(f"Unexpected bank request: {request.method} {path}")

        client_class = httpx.AsyncClient
        return patch.object(bank_client.httpx, "AsyncClient", side_effect=lambda *args, **kwargs: client_class(
            transport=httpx.MockTransport(respond), timeout=5,
        ))

    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://test")
        self.other = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://test")
        self.patches = [
            patch.object(passi_auth, "AUTH_ENABLED", True),
            patch.object(passi_auth, "PASSI_ALLOWED_EMAILS", {"alice@example.com"}),
            patch.object(database, "ensure_tenant_schema", side_effect=AssertionError("PostgreSQL schema accessed")),
            patch.object(database, "control_session_factory", side_effect=AssertionError("Control database accessed")),
            patch.object(database, "tenant_session_factory", side_effect=AssertionError("Tenant database accessed")),
        ]
        for patcher in self.patches:
            patcher.start()
        self.assertEqual((await self.client.post("/auth/demo/start")).status_code, 303)
        self.owner = demo.PREFIX + self.client.cookies[demo.COOKIE]

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.other.aclose()
        await demo.store.close()
        for patcher in reversed(self.patches):
            patcher.stop()

    async def test_demo_uses_only_memory_and_is_private_to_browser(self):
        response = await self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual((await self.client.get("/auth/status")).json()["demo"], True)
        await self.client.post("/api/categories", json={"name": "Private demo", "month": "2026-11"})
        rows = (await self.client.get("/api/categories?month=2026-11")).json()
        self.assertIn("Private demo", [row["name"] for row in rows])
        await self.other.post("/auth/demo/start")
        rows = (await self.other.get("/api/categories?month=2026-11")).json()
        self.assertNotIn("Private demo", [row["name"] for row in rows])
        async with demo.store.database(self.owner) as db:
            from sqlalchemy import text
            files = (await db.execute(text("PRAGMA database_list"))).all()
            self.assertTrue(all(row[2] == "" for row in files))

    async def test_reset_exit_and_expiration_never_fall_back_to_real_data(self):
        await self.client.post("/api/categories", json={"name": "Gone", "month": "2026-11"})
        await self.client.post("/auth/demo/start")
        self.assertNotIn(self.owner, demo.store.sessions)
        owner = demo.PREFIX + self.client.cookies[demo.COOKIE]
        self.assertNotEqual(owner, self.owner)
        rows = (await self.client.get("/api/categories?month=2026-11")).json()
        self.assertNotIn("Gone", [row["name"] for row in rows])
        demo.store.get(owner).expires_at = demo.now() - timedelta(seconds=1)
        self.assertEqual((await self.client.get("/api/accounts")).status_code, 401)
        self.assertEqual((await self.client.get("/")).headers["location"], "/auth/demo")
        await demo.store.cleanup()
        self.assertNotIn(owner, demo.store.sessions)
        await self.client.post("/auth/demo/exit")
        self.assertNotIn(demo.COOKIE, self.client.cookies)

    async def test_parallel_dashboard_reads_share_one_serialized_memory_store(self):
        responses = await asyncio.gather(*[
            self.client.get("/api/categories?month=2026-11") for _ in range(6)
        ])
        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertTrue(all(len(response.json()) == 5 for response in responses))

    async def connect_and_import(self):
        response = await self.client.post("/api/demo/connect")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["url"], "https://sandbox.example/consent")
        response = await self.client.get("/callback", params={"state": self.bank_state, "code": "sandbox-code"})
        self.assertEqual(response.status_code, 303, response.text)

    async def test_bank_import_edits_pagination_and_reimport_after_reset(self):
        with self.bank_mock(), patch.object(bank_client, "make_jwt", return_value="sandbox-jwt"):
            self.assertIsNone((await self.client.get("/api/sync")).json()["last_success_at"])
            await self.connect_and_import()
            self.assertIsNotNone((await self.client.get("/api/sync")).json()["last_success_at"])
            accounts = (await self.client.get("/api/accounts")).json()
            self.assertEqual(accounts[0]["balance"], "-20.00")
            transactions = (await self.client.get("/api/transactions")).json()
            self.assertEqual(len(transactions), 2)
            categories = (await self.client.get("/api/categories?month=2026-11")).json()
            first_id, second_id = (row["id"] for row in categories[:2])
            response = await self.client.put(f'/api/expenses/{transactions[0]["id"]}/splits', json={"splits": [
                {"category_id": first_id, "amount": 3}, {"category_id": second_id, "amount": 4.5},
            ]})
            self.assertEqual(response.status_code, 200, response.text)
            response = await self.client.patch(f'/api/expenses/{transactions[1]["id"]}/category', json={"category_id": first_id})
            self.assertEqual(response.status_code, 200, response.text)
            categories = (await self.client.get("/api/categories?month=2026-11")).json()
            self.assertEqual(sum(row["spent"] for row in categories), 20)
            for path in ("/api/expenses", "/api/daily-summary", "/api/daily-breakdown", "/api/summary"):
                response = await self.client.get(path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json())
            response = await self.client.patch(f'/api/accounts/{accounts[0]["id"]}/balance', json={"balance": "123.45"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual((await self.client.get("/api/accounts")).json()[0]["balance"], "123.45")
            self.assertEqual((await self.client.post("/api/sync")).json()["transactions_imported"], 0)
            self.assertIsNotNone((await self.client.get("/api/sync")).json()["last_success_at"])
            await self.client.post("/auth/demo/start")
            self.assertIsNone((await self.client.get("/api/sync")).json()["last_success_at"])
            self.assertEqual((await self.client.get("/api/accounts")).json(), [])
            await self.connect_and_import()
            self.assertEqual(len((await self.client.get("/api/transactions")).json()), 2)
            self.assertEqual((await self.client.get("/api/accounts")).json()[0]["balance"], "-20.00")

    async def test_manual_category_is_remembered_for_matching_expenses(self):
        with self.bank_mock(), patch.object(bank_client, "make_jwt", return_value="sandbox-jwt"):
            await self.connect_and_import()
        transactions = (await self.client.get("/api/transactions")).json()
        category_id = (await self.client.get("/api/categories?month=2026-11")).json()[0]["id"]
        response = await self.client.patch(
            f'/api/expenses/{transactions[0]["id"]}/category', json={"category_id": category_id},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["auto_assigned"], 1)
        assigned = (await self.client.get("/api/transactions")).json()
        self.assertEqual({row["category_id"] for row in assigned}, {category_id})

        from app.models import Account, Transaction
        async with demo.store.database(self.owner) as db:
            account_id = await db.scalar(select(Account.id))
            db.add(Transaction(
                account_id=account_id, external_id="future", booking_date=datetime(2026, 11, 3, tzinfo=timezone.utc),
                amount=5, currency="EUR", direction="DBIT", description="Sandbox purchase", raw_data={},
            ))
            await db.commit()
        await self.client.get("/api/categories?month=2026-11")
        future = next(row for row in (await self.client.get("/api/transactions")).json() if row["description"] == "Sandbox purchase" and row["id"] not in {item["id"] for item in transactions})
        self.assertEqual(future["category_id"], category_id)

    async def test_live_bank_credentials_cannot_be_used_in_demo(self):
        with self.bank_mock(environment="PRODUCTION"), patch.object(bank_client, "make_jwt", return_value="test-jwt"):
            response = await self.client.post("/api/demo/connect")
        self.assertEqual(response.status_code, 503)
        self.assertEqual([request.url.path for request in self.bank_calls], ["/application"])
        self.assertEqual(demo.store.get(self.owner).pending, {})

    async def test_callback_is_browser_bound_one_use_and_invalid_after_reset(self):
        with self.bank_mock(), patch.object(bank_client, "make_jwt", return_value="sandbox-jwt"):
            await self.client.post("/api/demo/connect")
            params = {"state": self.bank_state, "code": "sandbox-code"}
            await self.other.post("/auth/demo/start")
            self.assertEqual((await self.other.get("/callback", params=params)).status_code, 400)
            self.assertEqual((await self.client.get("/callback", params=params)).status_code, 303)
            self.assertEqual((await self.client.get("/callback", params=params)).status_code, 400)
            await self.client.post("/api/demo/connect")
            params["state"] = self.bank_state
            await self.client.post("/auth/demo/start")
            self.assertEqual((await self.client.get("/callback", params=params)).status_code, 400)

    async def test_demo_events_use_memory_and_stop_after_expiration(self):
        with patch.object(events_router.asyncpg, "connect", side_effect=AssertionError("Persistent event connection")):
            response = await events_router.data_events(self.owner)
            self.assertIn("event: changed", await anext(response.body_iterator))
            demo.store.get(self.owner).expires_at = demo.now() - timedelta(seconds=1)
            self.assertIn("event: expired", await anext(response.body_iterator))
            await response.body_iterator.aclose()

    async def test_demo_preserves_existing_passi_login_without_reading_auth_store(self):
        signer = TimestampSigner("test-only-secret")
        login = {"user": {"email": "alice@example.com"}, "sid": "existing-login"}
        cookie = signer.sign(base64.b64encode(json.dumps(login).encode())).decode()
        wrapped = SessionMiddleware(main.app, secret_key="test-only-secret")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=wrapped), base_url="https://test") as client:
            client.cookies.set("session", cookie, domain="test.local", path="/")
            self.assertEqual((await client.post("/auth/demo/start")).status_code, 303)
            self.assertEqual((await client.get("/api/accounts")).json(), [])
            self.assertEqual((await client.post("/auth/demo/exit")).status_code, 303)
            restored = json.loads(base64.b64decode(signer.unsign(client.cookies["session"])))
            self.assertEqual(restored, login)
