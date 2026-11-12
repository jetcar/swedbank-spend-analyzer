from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import unittest

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.requests import Request

from app import database, passi_auth
from app.models import PendingBankAuthorization
from app.routers import banking as banking_router


def request_with_session(session):
    request = Request({"type": "http", "method": "GET", "path": "/callback", "headers": []})
    request.scope["session"] = session
    return request


class BankCallbackSecurityTest(unittest.IsolatedAsyncioTestCase):
    async def test_callback_state_is_persistent_single_use_and_bound_to_browser(self):
        async with database.engine.connect() as connection:
            outer = await connection.begin()
            factory = async_sessionmaker(connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
            try:
                async with factory() as db:
                    db.add(PendingBankAuthorization(
                        state="bank-state",
                        owner_id="alice@example.com",
                        browser_binding="browser-binding",
                        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                    ))
                    await db.commit()
                with patch.object(passi_auth, "AUTH_ENABLED", True), patch.object(database, "control_session_factory", factory):
                    with self.assertRaises(HTTPException) as context:
                        await banking_router.consume_bank_authorization_state(request_with_session({}), "bank-state")
                    self.assertEqual(context.exception.status_code, 400)

                    owner = await banking_router.consume_bank_authorization_state(
                        request_with_session({"bank_auth_flow": {"state": "bank-state", "binding": "browser-binding"}}),
                        "bank-state",
                    )
                    self.assertEqual(owner, "alice@example.com")

                    with self.assertRaises(HTTPException):
                        await banking_router.consume_bank_authorization_state(
                            request_with_session({"bank_auth_flow": {"state": "bank-state", "binding": "browser-binding"}}),
                            "bank-state",
                        )
            finally:
                await outer.rollback()
        await database.engine.dispose()

    async def test_production_rejects_unbound_state_and_error_consumes_bound_state(self):
        async with database.engine.connect() as connection:
            outer = await connection.begin()
            factory = async_sessionmaker(connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
            try:
                async with factory() as db:
                    db.add_all([
                        PendingBankAuthorization(
                            state="unbound-state",
                            owner_id="alice@example.com",
                            browser_binding="",
                            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                        ),
                        PendingBankAuthorization(
                            state="denied-state",
                            owner_id="alice@example.com",
                            browser_binding="denied-binding",
                            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                        ),
                    ])
                    await db.commit()
                with patch.object(passi_auth, "AUTH_ENABLED", True), patch.object(database, "control_session_factory", factory):
                    with self.assertRaises(HTTPException):
                        await banking_router.consume_bank_authorization_state(
                            request_with_session({"bank_auth_flow": {"state": "unbound-state", "binding": ""}}),
                            "unbound-state",
                        )
                    request = request_with_session({"bank_auth_flow": {"state": "denied-state", "binding": "denied-binding"}})
                    with self.assertRaises(HTTPException) as context:
                        await banking_router.authorization_callback(
                            request,
                            BackgroundTasks(),
                            state="denied-state",
                            error="access_denied",
                        )
                    self.assertEqual(context.exception.status_code, 400)
                    with self.assertRaises(HTTPException):
                        await banking_router.consume_bank_authorization_state(request, "denied-state")
            finally:
                await outer.rollback()
        await database.engine.dispose()

    async def test_multiple_bank_authorizations_from_one_browser_remain_valid(self):
        async with database.engine.connect() as connection:
            outer = await connection.begin()
            factory = async_sessionmaker(connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
            try:
                async with factory() as db:
                    db.add_all([
                        PendingBankAuthorization(
                            state="first-state",
                            owner_id="alice@example.com",
                            browser_binding="first-binding",
                            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                        ),
                        PendingBankAuthorization(
                            state="second-state",
                            owner_id="alice@example.com",
                            browser_binding="second-binding",
                            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                        ),
                    ])
                    await db.commit()
                request = request_with_session({
                    "bank_auth_flows": {
                        "first-state": "first-binding",
                        "second-state": "second-binding",
                    }
                })
                with patch.object(passi_auth, "AUTH_ENABLED", True), patch.object(database, "control_session_factory", factory):
                    self.assertEqual(
                        await banking_router.consume_bank_authorization_state(request, "first-state"),
                        "alice@example.com",
                    )
                    self.assertIn("second-state", request.session["bank_auth_flows"])
                    self.assertEqual(
                        await banking_router.consume_bank_authorization_state(request, "second-state"),
                        "alice@example.com",
                    )
                    self.assertNotIn("bank_auth_flows", request.session)
            finally:
                await outer.rollback()
        await database.engine.dispose()
if __name__ == "__main__":
    unittest.main()
