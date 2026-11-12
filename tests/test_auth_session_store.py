"""Run against a disposable PostgreSQL database with TEST_DATABASE_URL set."""
from secrets import token_urlsafe
import os
import time
import unittest

from sqlalchemy import text


if os.getenv('TEST_DATABASE_URL'):
    os.environ['DATABASE_URL'] = os.environ['TEST_DATABASE_URL']
    from app.auth_sessions import DatabaseAuthSessionStore
    from app.database import engine
    from app.models import ControlBase


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'A disposable PostgreSQL database is required')
class AuthSessionStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_session_survives_new_store_and_connection_pool_then_revokes(self):
        async with engine.begin() as connection:
            await connection.execute(text('CREATE SCHEMA IF NOT EXISTS control'))
            await connection.run_sync(ControlBase.metadata.create_all)
        sid, owner, now = token_urlsafe(32), 'test@example.invalid', int(time.time())
        try:
            await DatabaseAuthSessionStore().create(sid, owner, now + 2592000)
            await engine.dispose()
            restored = DatabaseAuthSessionStore()
            self.assertTrue(await restored.is_active(sid, owner, now + 2591999))
            self.assertFalse(await restored.is_active(sid, owner, now + 2592000))
            self.assertFalse(await restored.is_active(sid, 'other@example.invalid', now))
            await restored.revoke(sid)
            await engine.dispose()
            self.assertFalse(await DatabaseAuthSessionStore().is_active(sid, owner, now))
        finally:
            async with engine.begin() as connection:
                await connection.execute(text('DELETE FROM control.auth_sessions WHERE sid = :sid'), {'sid': sid})
            await engine.dispose()
