"""MCP interface for the local Swedbank Spend Analyzer."""

import os
import secrets
from typing import Any

import httpx
from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings

from .passi_auth import (
    PASSI_ACCESS_TOKEN_VERIFIER,
    PASSI_ALLOWED_EMAILS,
    PASSI_ISSUER,
    PASSI_RESOURCE,
)


APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
APP_PUBLIC_URL = os.getenv("APP_PUBLIC_URL", "http://localhost:8000").rstrip("/")
MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8001"))
MCP_OAUTH_ENABLED = os.getenv("MCP_OAUTH_ENABLED", os.getenv("PASSI_AUTH_ENABLED", "0")).lower() in {
    "1", "true", "yes", "on",
}


def oauth_configuration(enabled: bool = MCP_OAUTH_ENABLED) -> dict[str, Any]:
    if not enabled:
        return {}
    if not PASSI_ALLOWED_EMAILS:
        raise RuntimeError("PASSI_ALLOWED_EMAILS is required when MCP OAuth is enabled")
    return {
        "auth": AuthSettings(
            issuer_url=PASSI_ISSUER,
            resource_server_url=PASSI_RESOURCE,
            required_scopes=["email"],
            validate_token_resource=True,
        ),
        "token_verifier": PASSI_ACCESS_TOKEN_VERIFIER,
    }


class DuplicateAuthorizationHeaderMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            authorization_count = sum(1 for key, _ in scope["headers"] if key.lower() == b"authorization")
            if authorization_count > 1:
                await MCPIngressMiddleware._reject(send, 401, "Duplicate Authorization headers are not allowed")
                return
        await self.app(scope, receive, send)


class MCPIngressMiddleware:
    def __init__(
        self,
        app,
        *,
        client_token: str,
        allowed_hosts: set[str],
        allowed_origins: set[str],
    ):
        if not client_token:
            raise RuntimeError("MCP_CLIENT_TOKEN is required")
        if not allowed_hosts:
            raise RuntimeError("MCP_ALLOWED_HOSTS must contain at least one host")
        self.app = app
        self.client_token = client_token
        self.allowed_hosts = {value.lower() for value in allowed_hosts}
        self.allowed_origins = allowed_origins

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers: dict[str, list[str]] = {}
        for key, value in scope["headers"]:
            headers.setdefault(key.decode("latin-1").lower(), []).append(value.decode("latin-1"))
        hosts = headers.get("host", [])
        origins = headers.get("origin", [])
        if (
            len(hosts) != 1
            or hosts[0].lower() not in self.allowed_hosts
            or len(origins) > 1
            or any(origin not in self.allowed_origins for origin in origins)
        ):
            await self._reject(send, 403, "MCP Host or Origin is not allowed")
            return
        authorizations = headers.get("authorization", [])
        expected = f"Bearer {self.client_token}"
        if len(authorizations) != 1 or not secrets.compare_digest(authorizations[0].encode(), expected.encode()):
            await self._reject(send, 401, "MCP client authentication is required")
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send, status: int, detail: str) -> None:
        body = ('{"detail":"' + detail + '"}').encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})


def secured_mcp_app():
    allowed_hosts = {value.strip().lower() for value in os.getenv("MCP_ALLOWED_HOSTS", "").split(",") if value.strip()}
    allowed_origins = {value.strip() for value in os.getenv("MCP_ALLOWED_ORIGINS", "").split(",") if value.strip()}
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=sorted(allowed_hosts),
        allowed_origins=sorted(allowed_origins),
    )
    app = mcp.streamable_http_app(host=MCP_HOST, transport_security=transport_security)
    if not MCP_OAUTH_ENABLED:
        app = MCPIngressMiddleware(
            app,
            client_token=os.getenv("MCP_CLIENT_TOKEN", ""),
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
        )
    return DuplicateAuthorizationHeaderMiddleware(app)

mcp = MCPServer(
    "Swedbank Spend Analyzer",
    instructions="Use these tools to manage the local Swedbank expense analyzer. Bank authorization opens a browser URL once; all other operations are local API calls.",
    **oauth_configuration(),
)


async def api(method: str, path: str, **kwargs: Any) -> Any:
    access_token = get_access_token()
    if MCP_OAUTH_ENABLED and access_token is None:
        raise RuntimeError("Authenticated Passi access token is unavailable")
    headers = {"Authorization": f"Bearer {access_token.token}"} if access_token is not None else {}
    async with httpx.AsyncClient(base_url=APP_BASE_URL, timeout=60, headers=headers) as client:
        response = await client.request(method, path, **kwargs)
    if response.is_error:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise RuntimeError(f"Application API error {response.status_code}: {detail}")
    return response.json()


@mcp.tool()
async def get_connection_status() -> dict[str, Any]:
    """Return application health and connected bank account count."""
    health = await api("GET", "/health")
    accounts = await api("GET", "/api/accounts")
    return {"health": health, "connected": bool(accounts), "accounts": len(accounts)}


@mcp.tool()
async def start_bank_authorization() -> dict[str, str]:
    """Return the browser-authenticated endpoint that starts Swedbank authorization."""
    return {
        "authorization_url": f"{APP_PUBLIC_URL}/connect",
        "next_step": "Open the URL, finish bank authorization, then call get_connection_status and sync_transactions.",
    }


@mcp.tool()
async def sync_transactions() -> dict[str, int]:
    """Fetch new transactions for all monitored accounts."""
    return await api("POST", "/api/sync")


@mcp.tool()
async def get_accounts() -> list[dict[str, Any]]:
    """List bank accounts and whether each account is monitored."""
    return await api("GET", "/api/accounts")


@mcp.tool()
async def set_account_monitoring(account_id: int, monitored: bool) -> dict[str, Any]:
    """Enable or disable an account in all expense and summary calculations."""
    return await api("PATCH", f"/api/accounts/{account_id}/monitor", params={"monitored": monitored})


@mcp.tool()
async def get_transactions(limit: int = 1000, month: str | None = None) -> list[dict[str, Any]]:
    """List transactions, optionally limited to a YYYY-MM booking month."""
    return await api("GET", "/api/transactions", params={"limit": limit, **({"month": month} if month else {})})


@mcp.tool()
async def get_categories(month: str | None = None) -> list[dict[str, Any]]:
    """List monthly categories; missing months are copied from the previous month by the application."""
    return await api("GET", "/api/categories", params={"month": month} if month else {})


@mcp.tool()
async def create_category(name: str, month: str, icon: str = "🏷️", budget: float = 0) -> dict[str, Any]:
    """Create a category for a YYYY-MM month. Zero budget means no limit; negative budget means expected income."""
    return await api("POST", "/api/categories", json={"name": name, "month": month, "icon": icon, "budget": budget})


@mcp.tool()
async def update_category(
    category_id: int,
    budget: float | None = None,
    enabled: bool | None = None,
    icon: str | None = None,
) -> dict[str, Any]:
    """Update any supplied category fields. Disabled categories stop receiving assignments."""
    payload = {key: value for key, value in {"budget": budget, "enabled": enabled, "icon": icon}.items() if value is not None}
    return await api("PATCH", f"/api/categories/{category_id}", json=payload)


@mcp.tool()
async def delete_category(category_id: int) -> dict[str, Any]:
    """Delete a category; its transactions become uncategorized for that month."""
    return await api("DELETE", f"/api/categories/{category_id}")


@mcp.tool()
async def get_expenses(limit: int = 5000, month: str | None = None) -> list[dict[str, Any]]:
    """List debit operations with category and split information."""
    return await api("GET", "/api/expenses", params={"limit": limit, **({"month": month} if month else {})})


@mcp.tool()
async def assign_expense(expense_id: int, category_id: int | None = None) -> dict[str, Any]:
    """Assign an expense to one category, or pass null to move it to uncategorized."""
    return await api("PATCH", f"/api/expenses/{expense_id}/category", json={"category_id": category_id})


@mcp.tool()
async def split_expense(expense_id: int, splits: list[dict[str, Any]]) -> dict[str, Any]:
    """Split one expense into category allocations. Amounts must sum to the original expense."""
    return await api("PUT", f"/api/expenses/{expense_id}/splits", json={"splits": splits})


@mcp.tool()
async def get_summary(group_by: str = "description") -> list[dict[str, Any]]:
    """Summarize monitored expenses by description, month, or category."""
    return await api("GET", "/api/summary", params={"group_by": group_by})


@mcp.tool()
async def get_daily_summary() -> list[dict[str, Any]]:
    """Return expense totals grouped by booking date."""
    return await api("GET", "/api/daily-summary")


@mcp.tool()
async def get_daily_breakdown(month: str | None = None) -> list[dict[str, Any]]:
    """Return all booking days with category totals and operations; omit month for the full period."""
    return await api("GET", "/api/daily-breakdown", params={"month": month} if month else {})


if __name__ == "__main__":
    import uvicorn
    from .logging_config import logging_config

    # Own Uvicorn's configuration: MCPServer.run() installs a text formatter.
    uvicorn.run(
        secured_mcp_app(), host=MCP_HOST, port=MCP_PORT,
        log_config=logging_config("mcp"), access_log=False,
    )
