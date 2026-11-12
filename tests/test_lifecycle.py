import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app import main


class LifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_initialization_precedes_workers_and_shutdown_awaits_them(self):
        started, stopped = set(), set()
        dispose = AsyncMock()

        async def worker(name):
            started.add(name)
            try:
                await asyncio.Event().wait()
            finally:
                stopped.add(name)

        async def close_demo():
            self.assertEqual(stopped, {"sync", "demo"})

        with (
            patch.object(main, "validate_auth_configuration"),
            patch.object(main.startup, "create_tables", new_callable=AsyncMock) as initialize,
            patch.object(main.auto_sync_service, "auto_sync_enabled", return_value=True),
            patch.object(main.auto_sync_service, "auto_sync_loop", new=lambda: worker("sync")),
            patch.object(main.demo.store, "sweep", new=lambda: worker("demo")),
            patch.object(main.demo.store, "close", new_callable=AsyncMock, side_effect=close_demo) as close,
            patch.object(main.database, "engine", SimpleNamespace(dispose=dispose)),
        ):
            async with main.lifespan(main.app):
                initialize.assert_awaited_once()
                await asyncio.sleep(0)
                self.assertEqual(started, {"sync", "demo"})
            close.assert_awaited_once()
            dispose.assert_awaited_once()
