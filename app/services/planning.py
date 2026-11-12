"""Monthly recurring category allowances and additional one-off cash flows."""
from collections import defaultdict
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app import database, dates, schemas
from app.models import Account, BankSession, Category, ExpenseSplit, PlanItem, PlanItemTransaction, Transaction
from app.services.accounts import account_balance_expression
from app.services.categories import ensure_month_categories


ZERO = Decimal("0")


def money(value) -> str:
    return format(Decimal(value or 0), ".2f")


def remaining(amount: Decimal, actual: Decimal, status: str, *, mode: str = "payment") -> Decimal:
    if mode == "payment" and (status == "closed" or (status == "auto" and actual > ZERO)):
        return ZERO
    return max(abs(amount) - actual, ZERO)


def monthly_transactions(owner_id: str, month: str):
    start, end = dates.month_bounds(month)
    effective_date = func.coalesce(Transaction.booking_date, Transaction.value_date)
    return (
        select(Transaction, func.coalesce(Transaction.currency, Account.currency).label("plan_currency"))
        .join(Account).join(BankSession)
        .where(
            BankSession.owner_id == owner_id, Account.monitored.is_(True),
            Transaction.excluded_from_statistics.is_(False), Transaction.amount > 0,
            Transaction.direction.in_(("DBIT", "CRDT")),
            effective_date >= start, effective_date < end,
        )
    )


def matches(item: PlanItem, transaction: Transaction, currency: str | None) -> bool:
    return transaction.direction == item.direction and currency == item.currency


def serialize_item(item: PlanItem, transaction_ids: list[int], actual: Decimal) -> dict[str, Any]:
    return {
        "id": item.id, "name": item.name, "month": item.month, "direction": item.direction,
        "amount": money(item.amount), "currency": item.currency, "payment_status": item.payment_status,
        "transaction_ids": sorted(transaction_ids), "actual": money(actual),
        "remaining": money(remaining(item.amount, actual, item.payment_status)),
    }


async def list_plan(*, month: str | None, language: str, owner_id: str) -> dict[str, Any]:
    selected_month = month or dates.current_month()
    dates.month_bounds(selected_month)
    async with database.tenant_session(owner_id) as db:
        await ensure_month_categories(db, owner_id, selected_month, language)
    # Initialization can commit. Read the complete report in a new, consistent
    # snapshot so concurrent imports or plan edits cannot mix actuals and balances.
    # Demo sessions already serialize all reads and writes using one database lock.
    async with database.tenant_session(owner_id, repeatable_read=True) as db:
        categories = list((await db.scalars(
            select(Category).where(Category.owner_id == owner_id, Category.month == selected_month).order_by(Category.name)
        )).all())
        transaction_query = monthly_transactions(owner_id, selected_month)
        transactions = {transaction.id: (transaction, currency) for transaction, currency in (await db.execute(transaction_query)).all()}
        # Join the complete eligible month instead of materializing an IN list or a paginated expense response.
        eligible_ids = transaction_query.with_only_columns(Transaction.id).subquery()
        split_rows = (await db.execute(
            select(ExpenseSplit.transaction_id, ExpenseSplit.category_id, ExpenseSplit.amount)
            .join(eligible_ids, eligible_ids.c.id == ExpenseSplit.transaction_id)
        )).all()
        items = list((await db.scalars(
            select(PlanItem).where(PlanItem.owner_id == owner_id, PlanItem.month == selected_month).order_by(PlanItem.id)
        )).all())
        links = (await db.execute(
            select(PlanItemTransaction.item_id, PlanItemTransaction.transaction_id)
            .join(PlanItem).where(PlanItem.owner_id == owner_id, PlanItem.month == selected_month)
        )).all()
        accounts = (await db.execute(
            select(Account.currency, account_balance_expression()).join(BankSession)
            .where(BankSession.owner_id == owner_id, Account.monitored.is_(True))
        )).all()

    item_by_id = {item.id: item for item in items}
    item_ids = defaultdict(list)
    item_actual = defaultdict(lambda: ZERO)
    reserved = set()
    for item_id, transaction_id in links:
        item_ids[item_id].append(transaction_id)
        eligible = transactions.get(transaction_id)
        if eligible and matches(item_by_id[item_id], *eligible):
            item_actual[item_id] += Decimal(eligible[0].amount)
            reserved.add(transaction_id)

    allocations = defaultdict(list)
    for transaction_id, category_id, amount in split_rows:
        allocations[transaction_id].append((category_id, Decimal(amount)))
    actuals = defaultdict(lambda: ZERO)
    for transaction_id, (transaction, currency) in transactions.items():
        if transaction_id in reserved:
            continue
        for category_id, amount in allocations.get(transaction_id, [(transaction.category_id, Decimal(transaction.amount))]):
            actuals[(category_id, transaction.direction, currency)] += amount

    totals = defaultdict(lambda: {"balance": ZERO, "expected_income": ZERO, "expected_expense": ZERO})
    for currency, balance in accounts:
        totals[currency]["balance"] += Decimal(balance)
    category_rows = []
    for category in categories:
        amount = Decimal(category.budget or 0)
        direction = "CRDT" if amount < ZERO else "DBIT"
        actual = actuals[(category.id, direction, category.currency)]
        pending = remaining(amount, actual, category.payment_status, mode=category.forecast_mode) if category.enabled else ZERO
        category_rows.append({
            "id": category.id, "name": category.name, "icon": category.icon, "budget": money(amount),
            "currency": category.currency, "forecast_mode": category.forecast_mode,
            "payment_status": category.payment_status, "enabled": category.enabled,
            "actual": money(actual), "remaining": money(pending),
        })
        totals[category.currency]["expected_income" if direction == "CRDT" else "expected_expense"] += pending
    item_rows = []
    for item in items:
        item_rows.append(serialize_item(item, item_ids[item.id], item_actual[item.id]))
        totals[item.currency]["expected_income" if item.direction == "CRDT" else "expected_expense"] += remaining(item.amount, item_actual[item.id], item.payment_status)
    is_current = selected_month == dates.current_month()
    return {
        "month": selected_month, "is_current_month": is_current,
        "totals": [
            {"currency": currency, **{key: money(value) for key, value in values.items()},
             "closing_balance": money(values["balance"] + values["expected_income"] - values["expected_expense"]) if is_current else None}
            for currency, values in sorted(totals.items(), key=lambda row: row[0] or "")
        ],
        "categories": category_rows, "items": item_rows,
    }


async def validate_links(db, item: PlanItem, transaction_ids: list[int]) -> Decimal:
    if not transaction_ids:
        return ZERO
    rows = (await db.execute(
        monthly_transactions(item.owner_id, item.month)
        .where(Transaction.id.in_(transaction_ids)).with_for_update(of=Transaction)
    )).all()
    if len(rows) != len(transaction_ids) or any(not matches(item, transaction, currency) for transaction, currency in rows):
        raise HTTPException(400, "Transactions must match the month, direction and currency, and belong to tracked, non-excluded positive transactions")
    conflict = await db.scalar(
        select(PlanItemTransaction.transaction_id)
        .where(PlanItemTransaction.transaction_id.in_(transaction_ids), PlanItemTransaction.item_id != item.id)
        .limit(1)
    )
    if conflict is not None:
        raise HTTPException(409, "Transaction is already linked to another plan item")
    return sum((Decimal(transaction.amount) for transaction, _ in rows), ZERO)


async def save_links(db, item: PlanItem, transaction_ids: list[int]) -> None:
    await db.execute(delete(PlanItemTransaction).where(PlanItemTransaction.item_id == item.id))
    db.add_all([PlanItemTransaction(item_id=item.id, transaction_id=transaction_id) for transaction_id in transaction_ids])


async def commit_item(db) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Plan links changed; refresh and try again") from exc


async def create_item(*, item_input: schemas.NewPlanItem, owner_id: str) -> dict[str, Any]:
    dates.month_bounds(item_input.month)
    async with database.tenant_session(owner_id) as db:
        item = PlanItem(owner_id=owner_id, **item_input.model_dump(exclude={"transaction_ids"}))
        db.add(item)
        await db.flush()
        actual = await validate_links(db, item, item_input.transaction_ids)
        await save_links(db, item, item_input.transaction_ids)
        await commit_item(db)
        return serialize_item(item, item_input.transaction_ids, actual)


async def update_item(*, item_id: int, settings: schemas.PlanItemSettings, owner_id: str) -> dict[str, Any]:
    async with database.tenant_session(owner_id) as db:
        item = await db.scalar(select(PlanItem).where(PlanItem.id == item_id, PlanItem.owner_id == owner_id).with_for_update())
        if item is None:
            raise HTTPException(404, "Plan item not found")
        current_ids = list((await db.scalars(select(PlanItemTransaction.transaction_id).where(PlanItemTransaction.item_id == item.id))).all())
        for field, value in settings.model_dump(exclude_unset=True, exclude={"transaction_ids"}).items():
            setattr(item, field, value)
        transaction_ids = settings.transaction_ids if settings.transaction_ids is not None else current_ids
        actual = await validate_links(db, item, transaction_ids)
        await save_links(db, item, transaction_ids)
        await commit_item(db)
        return serialize_item(item, transaction_ids, actual)


async def delete_item(*, item_id: int, owner_id: str) -> dict[str, Any]:
    async with database.tenant_session(owner_id) as db:
        item = await db.scalar(select(PlanItem).where(PlanItem.id == item_id, PlanItem.owner_id == owner_id).with_for_update())
        if item is None:
            raise HTTPException(404, "Plan item not found")
        await db.delete(item)
        await db.commit()
    return {"id": item_id, "deleted": True}
