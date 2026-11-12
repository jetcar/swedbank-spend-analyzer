import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import func, select, text

from app import categorize_demo, database, passi_auth, seed_demo
from app.models import Transaction


class FixtureOwnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_categorizer_rejects_owner_mismatch_before_database_or_mcp(self):
        with (
            patch.object(passi_auth, "AUTH_ENABLED", False),
            patch.dict(os.environ, {"DEMO_OWNER_ID": "another-owner", "MCP_OAUTH_ENABLED": "0"}),
            patch.object(database, "tenant_session", side_effect=AssertionError("database accessed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "must match"):
                await categorize_demo.categorize("2098-06", "http://unused/mcp")

    async def test_categorizer_rejects_oauth_mode(self):
        with patch.object(passi_auth, "AUTH_ENABLED", True):
            with self.assertRaisesRegex(RuntimeError, "local authentication"):
                await categorize_demo.categorize("2098-06", "http://unused/mcp")


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "A disposable PostgreSQL database is required")
class FixtureScriptsTest(unittest.IsolatedAsyncioTestCase):
    async def test_seed_is_repeatable_and_confined_to_selected_tenant(self):
        owners = [f"fixtures-{uuid4()}@example.invalid" for _ in range(2)]
        try:
            for owner in owners:
                with patch.dict(os.environ, {"DEMO_OWNER_ID": owner}):
                    self.assertEqual((await seed_demo.seed("2098-06"))["created"], 150)
                    self.assertEqual((await seed_demo.seed("2098-06"))["created"], 0)
                async with database.tenant_session(owner) as db:
                    self.assertEqual(await db.scalar(select(func.count()).select_from(Transaction)), 150)
        finally:
            async with database.engine.begin() as connection:
                for owner in owners:
                    schema = database.tenant_schema_name(owner)
                    await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
                    database._ready_tenant_schemas.discard(schema)
            await database.engine.dispose()
