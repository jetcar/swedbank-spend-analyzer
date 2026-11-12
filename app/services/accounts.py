from typing import Any

from fastapi import HTTPException
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import database, schemas
from app.models import Account, BankSession, Transaction


def account_merge_key(account: Account) -> str:
    if account.iban:
        return f"iban:{account.iban}"
    if account.identification_hash:
        return f"hash:{account.identification_hash}"
    return f"bank-id:{account.bank_account_id}"


async def merge_duplicate_accounts(db: AsyncSession) -> None:
    accounts = list((await db.scalars(select(Account).order_by(Account.id))).all())
    session_owners = dict((await db.execute(select(BankSession.id, BankSession.owner_id))).all())
    canonical_by_key: dict[tuple[str, str], Account] = {}
    for account in accounts:
        key = (session_owners[account.session_id], account_merge_key(account))
        canonical = canonical_by_key.get(key)
        if canonical is None:
            canonical_by_key[key] = account
            continue
        duplicate_transactions = list(
            (await db.scalars(select(Transaction).where(Transaction.account_id == account.id))).all()
        )
        for transaction in duplicate_transactions:
            existing = await db.scalar(
                select(Transaction).where(
                    Transaction.account_id == canonical.id,
                    Transaction.external_id == transaction.external_id,
                )
            )
            if existing is None:
                transaction.account_id = canonical.id
            else:
                if existing.category_id is None and transaction.category_id is not None:
                    existing.category_id = transaction.category_id
                await db.delete(transaction)
        await db.delete(account)


def account_balance_expression():
    signed_amount = case(
        (Transaction.direction == "CRDT", func.abs(Transaction.amount)),
        (Transaction.direction == "DBIT", -func.abs(Transaction.amount)),
        else_=0,
    )
    transaction_total = (
        select(func.coalesce(func.sum(signed_amount), 0))
        .where(
            Transaction.account_id == Account.id,
            or_(Account.currency.is_(None), Transaction.currency.is_(None), Transaction.currency == Account.currency),
        )
        .correlate(Account)
        .scalar_subquery()
    )
    return Account.balance_adjustment + transaction_total


async def list_accounts(*, owner_id: str) -> list[dict[str, Any]]:
    async with database.tenant_session(owner_id) as db:
        accounts = list(
            (await db.execute(
                select(Account, account_balance_expression().label("balance"))
                .join(BankSession).where(BankSession.owner_id == owner_id)
                .order_by(Account.id)
            )).all()
        )
    return [
        {
            "id": account.id,
            "iban": account.iban,
            "name": account.name,
            "currency": account.currency,
            "monitored": account.monitored,
            "balance": format(balance, ".2f"),
        }
        for account, balance in accounts
    ]


async def set_account_balance(*, account_id: int, settings: schemas.AccountBalanceSettings, owner_id: str) -> dict[str, Any]:
    async with database.tenant_session(owner_id) as db:
        account = await db.scalar(
            select(Account).join(BankSession)
            .where(BankSession.owner_id == owner_id, Account.id == account_id)
            .with_for_update(of=Account)
        )
        if account is None:
            raise HTTPException(404, "Account not found")
        current_balance = await db.scalar(select(account_balance_expression()).where(Account.id == account_id))
        account.balance_adjustment += settings.balance - current_balance
        await db.commit()
    return {"id": account_id, "balance": format(settings.balance, ".2f")}


async def set_account_currency(*, account_id: int, settings: schemas.AccountCurrencySettings, owner_id: str) -> dict[str, Any]:
    async with database.tenant_session(owner_id) as db:
        account = await db.scalar(
            select(Account).join(BankSession)
            .where(BankSession.owner_id == owner_id, Account.id == account_id)
            .with_for_update(of=Account)
        )
        if account is None:
            raise HTTPException(404, "Account not found")
        account.currency = settings.currency
        await db.commit()
    return {"id": account_id, "currency": settings.currency}


async def set_account_monitoring(*, account_id: int, monitored: bool, owner_id: str) -> dict[str, Any]:
    async with database.tenant_session(owner_id) as db:
        account = await db.scalar(
            select(Account)
            .join(BankSession)
            .where(BankSession.owner_id == owner_id, Account.id == account_id)
        )
        if account is None:
            raise HTTPException(404, "Account not found")
        account.monitored = monitored
        await db.commit()
    return {"id": account_id, "monitored": monitored}
