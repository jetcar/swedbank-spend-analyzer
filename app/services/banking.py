from datetime import datetime, timedelta, timezone
from typing import Any
import secrets

from fastapi import HTTPException
from sqlalchemy import delete, select

from app import bank_client, config, database, dates, demo
from app.models import Account, BankSession, PendingBankAuthorization


def account_identifier(data: dict[str, Any]) -> str | None:
    value = data.get("uid") or data.get("account_id") or data.get("accountId")
    if isinstance(value, dict):
        return value.get("iban") or value.get("identification")
    return value


async def consume_authorization(state: str, browser_binding: str, *, require_binding: bool) -> str:
    async with database.control_session_factory() as db:
        pending = await db.scalar(
            select(PendingBankAuthorization).where(
                PendingBankAuthorization.state == state,
                PendingBankAuthorization.expires_at >= datetime.now(timezone.utc),
            )
        )
        if pending is None:
            raise HTTPException(400, "Invalid or expired authorization callback")
        if require_binding:
            if not pending.browser_binding or not browser_binding:
                raise HTTPException(400, "Authorization callback is not bound to this browser")
            if not secrets.compare_digest(browser_binding, pending.browser_binding):
                raise HTTPException(400, "Authorization callback is not bound to this browser")
        owner_id = await db.scalar(
            delete(PendingBankAuthorization)
            .where(
                PendingBankAuthorization.state == state,
                PendingBankAuthorization.expires_at >= datetime.now(timezone.utc),
            )
            .returning(PendingBankAuthorization.owner_id)
        )
        if owner_id is None:
            await db.rollback()
            raise HTTPException(400, "Invalid or expired authorization callback")
        await db.commit()
    return owner_id


async def start_authorization(owner_id: str, state: str, browser_binding: str, headers: dict[str, str]) -> str:
    url = await bank_client.start_authorization(owner_id, state, headers)
    if owner_id.startswith(demo.PREFIX):
        pending = demo.store.get(owner_id).pending
        pending[state] = demo.now() + timedelta(seconds=config.BANK_AUTH_STATE_TTL_SECONDS)
        while len(pending) > config.BANK_AUTH_BROWSER_FLOW_LIMIT:
            pending.pop(next(iter(pending)))
        return url
    async with database.control_session_factory() as db:
        await db.execute(
            delete(PendingBankAuthorization).where(
                PendingBankAuthorization.expires_at < datetime.now(timezone.utc)
            )
        )
        db.add(
            PendingBankAuthorization(
                state=state,
                owner_id=owner_id,
                browser_binding=browser_binding,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=config.BANK_AUTH_STATE_TTL_SECONDS),
            )
        )
        await db.commit()
    return url


async def connect_accounts(owner_id: str, code: str) -> list[int]:
    session_data = await bank_client.create_session(owner_id, code)
    async with database.tenant_session(owner_id) as db:
        bank_session = BankSession(
            owner_id=owner_id,
            enable_session_id=session_data["session_id"],
            bank_name=bank_client.bank_aspsp(owner_id)["name"],
            country=bank_client.bank_aspsp(owner_id)["country"],
            valid_until=dates.parse_datetime(session_data.get("access", {}).get("valid_until")),
            raw_data=session_data,
        )
        db.add(bank_session)
        await db.flush()
        accounts = []
        for raw_account in session_data.get("accounts", []):
            account_data = raw_account if isinstance(raw_account, dict) else {"uid": raw_account}
            bank_account_id = account_identifier(account_data)
            if not bank_account_id:
                continue
            account_iban = (account_data.get("account_id", {}) or {}).get("iban") if isinstance(account_data.get("account_id"), dict) else None
            identification_hash = account_data.get("identification_hash")
            account = None
            if account_iban:
                account = await db.scalar(
                    select(Account)
                    .join(BankSession)
                    .where(BankSession.owner_id == owner_id, Account.iban == account_iban)
                )
            elif identification_hash:
                account = await db.scalar(
                    select(Account)
                    .join(BankSession)
                    .where(BankSession.owner_id == owner_id, Account.identification_hash == identification_hash)
                )
            if account is None:
                account = Account(
                    session_id=bank_session.id,
                    bank_account_id=str(bank_account_id),
                    identification_hash=identification_hash,
                    iban=account_iban,
                    name=account_data.get("name"),
                    currency=account_data.get("currency"),
                    raw_data=account_data,
                )
                db.add(account)
            else:
                account.session_id = bank_session.id
                account.bank_account_id = str(bank_account_id)
                account.identification_hash = identification_hash
                account.name = account_data.get("name")
                if account.currency is None:
                    account.currency = account_data.get("currency")
                account.raw_data = account_data
            accounts.append(account)
        await db.commit()
        account_ids = [account.id for account in accounts]
    return account_ids
