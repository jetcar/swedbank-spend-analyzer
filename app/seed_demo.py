"""Create 150 clearly marked, repeatable demo expenses without bank API calls.

Run inside the app container: python -m app.seed_demo --month 2026-11
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import argparse
import asyncio
import json
import os

from sqlalchemy import select

from .models import Account, BankSession, Transaction
from app import database, passi_auth
from app.dates import month_bounds


MERCHANTS = (
    ("Rimi — продукты", "Еда"),
    ("Bolt — поездка", "Транспорт"),
    ("IKEA — товары для дома", "Дом"),
    ("Spotify — подписка", "Подписки"),
    ("Apollo — книги", "Прочее"),
)


async def seed(month: str) -> dict:
    owner_id = os.getenv("DEMO_OWNER_ID", passi_auth.PASSI_LOCAL_OWNER_ID).strip().lower()
    start, end = month_bounds(month)
    now = datetime.now(timezone.utc)
    days = min((end - start).days, now.day) if month == now.strftime("%Y-%m") else (end - start).days
    async with database.tenant_session(owner_id) as db:
        session = await db.scalar(
            select(BankSession).where(
                BankSession.owner_id == owner_id,
                BankSession.enable_session_id == f"local-test-data:{owner_id}",
            )
        )
        if session is None:
            session = BankSession(owner_id=owner_id, enable_session_id=f"local-test-data:{owner_id}", bank_name="TEST — synthetic", country="EE", raw_data={"test_data": True})
            db.add(session)
            await db.flush()
        account = await db.scalar(select(Account).where(Account.session_id == session.id, Account.bank_account_id == "local-test-account"))
        if account is None:
            account = Account(session_id=session.id, bank_account_id="local-test-account", name="TEST — 150 тестовых операций", currency="EUR", monitored=True, raw_data={"test_data": True})
            db.add(account)
            await db.flush()
        existing = set((await db.scalars(select(Transaction.external_id).where(Transaction.account_id == account.id))).all())
        created = 0
        for index in range(150):
            external_id = f"demo:{month}:{index + 1:03}"
            if external_id in existing:
                continue
            merchant, category = MERCHANTS[index % len(MERCHANTS)]
            booked = start + timedelta(days=(index // len(MERCHANTS)) % days, hours=8 + index % 10, minutes=index % 60)
            db.add(Transaction(
                account_id=account.id, external_id=external_id,
                booking_date=booked, value_date=booked,
                amount=Decimal(350 + (index * 137) % 12000) / 100,
                currency="EUR", direction="DBIT",
                description=f"[TEST] {merchant} #{index + 1:03}",
                raw_data={"test_data": True, "suggested_category": category},
            ))
            created += 1
        await db.commit()
        return {"created": created, "account_id": account.id, "month": month, "total_demo": 150}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", default=datetime.now(timezone.utc).strftime("%Y-%m"))
    args = parser.parse_args()
    try:
        print(json.dumps(await seed(args.month)))
    finally:
        await database.engine.dispose()
if __name__ == "__main__":
    asyncio.run(main())
