import time
import unittest
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import passi_auth as auth
from app.device_session import DeviceSessionMiddleware, REMEMBER_DEVICE_SECONDS
from tests.test_auth_security import MemorySessionStore


def make_app(store):
    app = FastAPI()
    app.state.auth_session_store = store
    app.add_middleware(auth.PassiAuthMiddleware)
    app.add_middleware(DeviceSessionMiddleware, secret_key='test-only', https_only=True)
    app.include_router(auth.router)

    @app.get('/api/protected')
    def protected():
        return {'ok': True}

    return app


class RememberDeviceTest(unittest.TestCase):
    def setUp(self):
        auth._pending.clear()
        auth._revoked_sessions.clear()
        for key, value in {'AUTH_ENABLED': True, 'PASSI_CLIENT_ID': 'test', 'PASSI_ALLOWED_EMAILS': {'admin@passi.cloud'}}.items():
            p = patch.object(auth, key, value)
            p.start()
            self.addCleanup(p.stop)
        self.now = int(time.time())
        clock = patch('time.time', return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.store = MemorySessionStore()
        self.client = self.new_client()

    def new_client(self):
        client = TestClient(make_app(self.store), base_url='https://testserver', follow_redirects=False)
        self.addCleanup(client.close)
        return client

    def sign_in(self, remember, email='admin@passi.cloud', callback_client=None, token_nonce=None):
        response = self.client.get('/auth/start', params={'remember_device': str(remember).lower(), 'return_url': '/categories'})
        self.assertEqual(response.status_code, 307)
        state = parse_qs(urlsplit(response.headers['location']).query)['state'][0]
        nonce = auth._pending[state][1]
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(return_value=MagicMock(is_error=False, json=lambda: {'id_token': 'mock-token'}))
        claims_nonce = nonce if token_nonce is None else token_nonce
        with patch.object(auth.httpx, 'AsyncClient', return_value=client), patch.object(auth, '_jwks_key', AsyncMock(return_value=object())), patch.object(auth.jwt, 'decode', return_value={'sub': 'test-subject', 'email': email, 'nonce': claims_nonce}):
            response = (callback_client or self.client).get('/auth/callback', params={'state': state, 'code': 'mock-code', 'remember_device': str(not remember).lower()})
        return response, state

    def test_remembered_cookie_and_database_expire_in_30_days(self):
        response, _ = self.sign_in(True)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers['location'], '/categories')
        cookie = SimpleCookie(response.headers['set-cookie'])['session']
        self.assertEqual(int(cookie['max-age']), REMEMBER_DEVICE_SECONDS)
        self.assertTrue(cookie['secure'])
        self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['samesite'].lower(), 'lax')
        self.assertEqual(next(iter(self.store.sessions.values()))['expires_at'], self.now + REMEMBER_DEVICE_SECONDS)

    def test_unchecked_choice_has_no_persistent_cookie(self):
        response, _ = self.sign_in(False)
        cookie = SimpleCookie(response.headers['set-cookie'])['session']
        self.assertFalse(cookie['max-age'])
        self.assertFalse(cookie['expires'])
        self.assertEqual(next(iter(self.store.sessions.values()))['expires_at'], self.now + auth.PASSI_SESSION_MAX_AGE_SECONDS)

    def test_cookie_survives_new_browser_client_and_app_restart_but_not_day_30(self):
        self.sign_in(True)
        restarted = self.new_client()
        restarted.cookies.update(self.client.cookies)
        with patch('time.time', return_value=self.now + REMEMBER_DEVICE_SECONDS - 1):
            response = restarted.get('/api/protected')
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('set-cookie', response.headers)  # No sliding renewal.
        with patch('time.time', return_value=self.now + REMEMBER_DEVICE_SECONDS):
            self.assertEqual(restarted.get('/api/protected').status_code, 401)

    def test_logout_revocation_survives_process_cache_reset(self):
        self.sign_in(True)
        copied = self.new_client()
        copied.cookies.update(self.client.cookies)
        self.assertEqual(self.client.get('/auth/logout').status_code, 307)
        auth._revoked_sessions.clear()
        self.assertEqual(copied.get('/api/protected').status_code, 401)

    def test_remembered_device_does_not_bypass_allowlist(self):
        self.sign_in(True)
        with patch.object(auth, 'PASSI_ALLOWED_EMAILS', {'someone-else@example.com'}):
            self.assertEqual(self.client.get('/api/protected').status_code, 401)

    def test_callback_needs_no_browser_binding_but_state_is_single_use(self):
        second = self.new_client()
        response, state = self.sign_in(True, callback_client=second)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(second.get('/api/protected').status_code, 200)
        self.assertEqual(second.get('/auth/callback', params={'code': 'replay', 'state': state}).status_code, 400)

    def test_disallowed_account_cannot_create_remembered_session(self):
        response, _ = self.sign_in(True, email='unauthorized@example.com')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.store.sessions)

    def test_email_verified_claim_is_not_required(self):
        response, _ = self.sign_in(True)
        self.assertEqual(response.status_code, 307)
        self.assertTrue(self.store.sessions)

    def test_incorrect_nonce_cannot_create_session(self):
        response, _ = self.sign_in(True, token_nonce='wrong-nonce')
        self.assertEqual(response.status_code, 401)
        self.assertFalse(self.store.sessions)

    def test_login_page_offers_unchecked_remember_choice(self):
        response = self.client.get('/auth/login?return_url=/categories')
        self.assertEqual(response.status_code, 200)
        self.assertIn('name="remember_device" value="true"', response.text)
        self.assertNotIn('checked', response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_return_url_cannot_redirect_to_another_site(self):
        for value in ['https://evil.invalid', '//evil.invalid', '/\\evil.invalid', '/\r\nevil.invalid']:
            self.assertEqual(auth._safe_return_url(value), '/')
        self.assertEqual(auth._safe_return_url('/categories?month=2026-11'), '/categories?month=2026-11')


if __name__ == '__main__':
    unittest.main()
