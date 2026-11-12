"""Cookie persistence follows the user's choice; expiry never slides on activity."""
import time
from http.cookies import SimpleCookie

from starlette.middleware.sessions import SessionMiddleware

REMEMBER_DEVICE_SECONDS = 30 * 24 * 60 * 60


class DeviceSessionMiddleware(SessionMiddleware):
    def __init__(self, app, **kwargs):
        kwargs['max_age'] = max(kwargs.get('max_age') or 0, REMEMBER_DEVICE_SECONDS)
        super().__init__(app, **kwargs)

    async def __call__(self, scope, receive, send):
        async def send_with_persistence(message):
            if message['type'] == 'http.response.start' and scope.get('session'):
                session = scope['session']
                updated = []
                for name, value in message.get('headers', []):
                    if name.lower() == b'set-cookie':
                        cookie = SimpleCookie()
                        cookie.load(value.decode('latin-1'))
                        if self.session_cookie in cookie:
                            morsel = cookie[self.session_cookie]
                            if session.get('remember_device') is True and session.get('user'):
                                remaining = int(session['authenticated_at']) + REMEMBER_DEVICE_SECONDS - int(time.time())
                                morsel['max-age'] = str(max(0, remaining))
                            else:
                                # No expiry attribute means a browser-session cookie.
                                morsel['max-age'] = ''
                                morsel['expires'] = ''
                            value = cookie.output(header='').strip().encode('latin-1')
                    updated.append((name, value))
                message['headers'] = updated
            await send(message)

        await super().__call__(scope, receive, send_with_persistence)
