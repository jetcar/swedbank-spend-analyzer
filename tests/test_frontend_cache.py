"""Run after npm run build, or against the built application image."""
import re
import unittest
from unittest.mock import patch

import httpx
from starlette.requests import Request

from app import main, passi_auth
from app.frontend import FRONTEND_DIR, IMMUTABLE, MANIFEST


class FrontendCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertTrue(MANIFEST, "Build the frontend before running these tests")
        self.auth = patch.object(passi_auth, "AUTH_ENABLED", False)
        self.auth.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.auth.stop()

    async def test_html_never_caches_or_returns_not_modified(self):
        for path in ("/", "/static/index.html", "/static/login.html", "/privacy", "/terms"):
            with self.subTest(path=path):
                response = await self.client.get(path, headers={
                    "If-None-Match": "*", "If-Modified-Since": "Wed, 01 Jan 2099 00:00:00 GMT",
                })
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertNotIn("etag", response.headers)
                self.assertNotIn("last-modified", response.headers)
                for url in re.findall(r'(?:src|href)="(/static/[^\"]+)"', response.text):
                    self.assertIn(url, MANIFEST.values())
        with patch.object(passi_auth, "AUTH_ENABLED", True):
            login = await passi_auth.login(Request({"type": "http", "session": {}}))
        self.assertEqual(login.path, FRONTEND_DIR / "login.html")
        self.assertEqual(login.headers["cache-control"], "no-store")

    async def test_fingerprinted_assets_are_immutable_including_revalidation(self):
        for url in MANIFEST.values():
            response = await self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertEqual(response.headers["cache-control"], IMMUTABLE)
            cached = await self.client.get(url, headers={"If-None-Match": response.headers["etag"]})
            self.assertEqual(cached.status_code, 304)
            self.assertEqual(cached.headers["cache-control"], IMMUTABLE)
        missing = await self.client.get("/static/assets/missing-12345678.js")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.headers["cache-control"], "no-store")
        self.assertEqual((await self.client.get("/static/app.js")).status_code, 404)


if __name__ == "__main__":
    unittest.main()
