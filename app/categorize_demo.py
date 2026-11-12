"""Assign seeded demo expenses through the real MCP HTTP endpoint."""
from datetime import datetime, timezone
import argparse
import asyncio
import json
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import select
import httpx2

from .models import Account, Transaction
from app import database, passi_auth
from app.dates import month_bounds


async def categorize(month: str, url: str) -> dict:
    owner_id = os.getenv("DEMO_OWNER_ID", passi_auth.PASSI_LOCAL_OWNER_ID).strip().lower()
    if passi_auth.AUTH_ENABLED or os.getenv("MCP_OAUTH_ENABLED", "0").lower() in {"1", "true", "yes", "on"}:
        raise RuntimeError("This fixture helper requires local authentication mode")
    if owner_id != passi_auth.PASSI_LOCAL_OWNER_ID:
        raise RuntimeError("DEMO_OWNER_ID must match the local MCP API owner")
    start, end = month_bounds(month)
    async with database.tenant_session(owner_id) as db:
        rows = (await db.scalars(select(Transaction).join(Account).where(
            Account.bank_account_id == "local-test-account",
            Transaction.booking_date >= start, Transaction.booking_date < end,
        ))).all()
        demo = {row.id: row.raw_data["suggested_category"] for row in rows if row.raw_data.get("test_data")}
    if len(demo) != 150:
        raise RuntimeError("Run app.seed_demo for this month first; expected 150 demo expenses")
    token = os.environ.get("MCP_CLIENT_TOKEN", "")
    if not token:
        raise RuntimeError("MCP_CLIENT_TOKEN is required")
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as http:
        async with streamable_http_client(url, http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as client:
                await client.initialize()

                async def call(name, arguments):
                    result = await client.call_tool(name, arguments)
                    if result.is_error:
                        raise RuntimeError(f"{name}: {result.content}")
                    data = result.structured_content
                    return data.get("result", data)

                categories = {row["name"]: row for row in await call("get_categories", {"month": month})}
                icons = {"Еда": "🍽️", "Транспорт": "🚗", "Дом": "🏠", "Подписки": "📱", "Прочее": "🧾"}
                for name in set(demo.values()):
                    if name not in categories:
                        categories[name] = await call("create_category", {"name": name, "month": month, "icon": icons[name]})
                    if not categories[name]["enabled"]:
                        raise RuntimeError(f"Demo category is disabled: {name}")
                assigned = 0
                for expense in await call("get_expenses", {"month": month, "limit": 5000}):
                    if expense["id"] not in demo:
                        continue
                    category_id = categories[demo[expense["id"]]]["id"]
                    if expense["category_id"] != category_id:
                        await call("assign_expense", {"expense_id": expense["id"], "category_id": category_id})
                        assigned += 1
                verified = [row for row in await call("get_expenses", {"month": month, "limit": 5000}) if row["id"] in demo]
                assert len(verified) == 150
                assert all(row["category_id"] == categories[demo[row["id"]]]["id"] for row in verified)
                return {"assigned_via_mcp": assigned, "verified": len(verified), "categories": {name: list(demo.values()).count(name) for name in icons}}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", default=datetime.now(timezone.utc).strftime("%Y-%m"))
    parser.add_argument("--url", default="http://mcp:8001/mcp")
    args = parser.parse_args()
    try:
        print(json.dumps(await categorize(args.month, args.url), ensure_ascii=False))
    finally:
        await database.engine.dispose()
if __name__ == "__main__":
    asyncio.run(main())
