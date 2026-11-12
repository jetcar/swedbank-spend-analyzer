"""Read-only integration checks against the running Docker Compose stack.

Run: python -m unittest discover -s tests -p test_network_security.py -v
Set CHECK_BANK_EGRESS=1 to include unauthenticated probes of the bank gateway.
No bank consent, categories or transactions are created or modified.
"""
import json
import os
import re
import subprocess
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, timeout=45)


def request(path="/", headers=None, port=8000, data=None):
    req = Request(f"http://127.0.0.1:{port}{path}", headers=headers or {}, data=data)
    try:
        response = urlopen(req, timeout=10)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, response.read().decode()


class NetworkSecurityTests(unittest.TestCase):
    def test_ui_and_database_work_without_login(self):
        status, _, body = request("/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["database"], "ok")
        self.assertEqual(request("/api/accounts")[0], 200)

    def test_unknown_host_is_rejected(self):
        self.assertEqual(request("/api/accounts", {"Host": "attacker.invalid"})[0], 403)

    def test_external_origin_and_cross_site_requests_are_rejected(self):
        for headers in ({"Origin": "https://attacker.invalid"}, {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(headers=headers):
                self.assertEqual(request("/api/accounts", headers)[0], 403)

    def test_bank_callback_navigation_is_not_blocked(self):
        # Deliberately invalid callback: should reach the application (400), not nginx (403).
        status, _, _ = request("/callback?error=security-test", {"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 400)

    def test_demo_posts_reject_foreign_origins_and_cross_site_navigation(self):
        for path in ("/auth/demo/start", "/auth/demo/exit"):
            for headers in (
                {"Origin": "https://attacker.invalid"},
                {"Origin": "null"},
                {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"},
            ):
                with self.subTest(path=path, headers=headers):
                    self.assertEqual(request(path, headers, data=b"")[0], 403)

    def test_cross_site_top_level_navigation_after_bank_callback_is_allowed(self):
        status, _, _ = request(
            "/?bank_connected=1&accounts=6&sync=pending",
            {
                "Sec-Fetch-Site": "cross-site",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Dest": "document",
            },
        )
        self.assertEqual(status, 200)

    def test_browser_policy_blocks_external_connections_and_inline_scripts(self):
        status, headers, html = request()
        self.assertEqual(status, 200)
        policy = headers["Content-Security-Policy"]
        self.assertIn("connect-src 'self'", policy)
        self.assertIn("script-src 'self';", policy)
        self.assertIn("form-action 'self' https://passi.cloud", policy)
        self.assertNotIn("form-action *", policy)
        app_script = re.search(r'src="(/static/(?:assets/)?app(?:-[A-Za-z0-9_-]+)?\.js)"', html)
        self.assertIsNotNone(app_script)
        self.assertNotIn('<script type="module">', html)
        self.assertEqual(request(app_script.group(1))[0], 200)
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_only_gateway_publishes_loopback_ports(self):
        ids = docker("compose", "ps", "-q").split()
        containers = json.loads(docker("inspect", *ids))
        for container in containers:
            service = container["Config"]["Labels"]["com.docker.compose.service"]
            bindings = container["HostConfig"].get("PortBindings") or {}
            if service == "gateway":
                self.assertEqual(set(bindings), {"8000/tcp", "8001/tcp"})
                for entries in bindings.values():
                    self.assertTrue(entries)
                    self.assertTrue(all(entry["HostIp"] == "127.0.0.1" for entry in entries))
            else:
                self.assertFalse(bindings, service)
            if service in ("app", "mcp", "postgres"):
                networks = container["NetworkSettings"]["Networks"]
                self.assertEqual(len(networks), 1)
                for network in networks:
                    self.assertTrue(json.loads(docker("network", "inspect", network))[0]["Internal"])

    def test_application_containers_cannot_bypass_proxy_or_leak_dns(self):
        code = '''import socket
assert socket.gethostbyname("postgres")
for host in ("example.com", "api.enablebanking.com"):
 try:
  socket.gethostbyname(host)
 except socket.gaierror:
  pass
 else:
  raise AssertionError("External DNS allowed: " + host)
for address in (("1.1.1.1", 443), ("1.1.1.1", 53), ("2606:4700:4700::1111", 443)):
 try:
  conn = socket.create_connection(address, timeout=2)
 except OSError:
  pass
 else:
  conn.close()
  raise AssertionError("Direct connection allowed")
print("blocked")'''
        for service in ("app", "mcp"):
            with self.subTest(service=service):
                self.assertIn("blocked", docker("compose", "exec", "-T", service, "python", "-c", code))

    def test_proxy_denies_unlisted_paths_methods_and_forward_proxy_requests(self):
        code = '''import httpx
with httpx.Client(base_url="http://bank-egress:8080", timeout=10) as client:
 for method, path in (("GET", "/"), ("GET", "/auth"), ("DELETE", "/sessions"), ("POST", "/payments"), ("GET", "/https://example.com")):
  response = client.request(method, path)
  assert response.status_code == 403, (method, path, response.status_code)
print("denied")'''
        self.assertIn("denied", docker("compose", "exec", "-T", "app", "python", "-c", code))

    def test_mcp_requires_client_authentication_through_gateway(self):
        data = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "security-check", "version": "1.0"},
        }}).encode()
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        self.assertEqual(request("/mcp", headers, port=8001, data=data)[0], 401)
        headers["Authorization"] = f"Bearer {os.getenv('MCP_CLIENT_TOKEN', 'local-development-mcp-token')}"
        status, _, body = request("/mcp", headers, port=8001, data=data)
        self.assertEqual(status, 200)
        self.assertIn('"serverInfo"', body)

    @unittest.skipUnless(os.getenv("CHECK_BANK_EGRESS") == "1", "Optional live bank connectivity probe")
    def test_allowlisted_requests_reach_real_bank_with_verified_tls(self):
        # No JWT or real account ID: the upstream must reject these without side effects.
        code = '''import httpx
with httpx.Client(base_url="http://bank-egress:8080", timeout=20) as client:
 for method, path in (("POST", "/auth"), ("POST", "/sessions"), ("GET", "/accounts/00000000-0000-0000-0000-000000000000/transactions")):
  response = client.request(method, path, headers={"Host": "example.com"})
  assert response.status_code == 401, (method, path, response.status_code)
print("bank reached")'''
        self.assertIn("bank reached", docker("compose", "exec", "-T", "app", "python", "-c", code))


if __name__ == "__main__":
    unittest.main()
