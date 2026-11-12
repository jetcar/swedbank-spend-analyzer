from typing import Any
from datetime import timezone

from fastapi import HTTPException
from sqlalchemy import delete, func, select

from app import database, dates, schemas
from app.transaction_details import transaction_counterparty_name
from app.models import Account, BankSession, Category, ExpenseSplit, Transaction


def planning_month(transaction: Transaction) -> str | None:
    """Mirror the stored booking/value date used by monthly SQL filters."""
    value = transaction.booking_date or transaction.value_date
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m")


async def list_transactions(*, limit: int, month: str | None, owner_id: str, before_id: int | None, page_by_id: bool) -> list[dict[str, Any]]:
    async with database.tenant_session(owner_id) as db:
        filters = [Account.monitored.is_(True)]
        effective_date = func.coalesce(Transaction.booking_date, Transaction.value_date)
        if month:
            start, end = dates.month_bounds(month)
            filters.extend([effective_date >= start, effective_date < end])
        if before_id is not None:
            filters.append(Transaction.id < before_id)
        transactions = list(
            (
                await db.scalars(
                    select(Transaction)
                    .join(Account)
                    .join(BankSession)
                    .where(BankSession.owner_id == owner_id, *filters)
                    .order_by(Transaction.id.desc() if page_by_id else effective_date.desc(), Transaction.id.desc())
                    .limit(limit)
                )
            ).all()
        )
        splits_by_id: dict[int, list[dict[str, Any]]] = {}
        if transactions:
            splits = (await db.scalars(select(ExpenseSplit).where(
                ExpenseSplit.transaction_id.in_([transaction.id for transaction in transactions])
            ))).all()
            for split in splits:
                splits_by_id.setdefault(split.transaction_id, []).append({
                    "category_id": split.category_id, "amount": float(split.amount),
                })
    return [
        {
            "id": transaction.id,
            "account_id": transaction.account_id,
            "category_id": transaction.category_id,
            "splits": splits_by_id.get(transaction.id, []),
            **dates.transaction_date_details(transaction),
            "planning_month": planning_month(transaction),
            "amount": float(transaction.amount) if transaction.amount is not None else None,
            "amount_exact": format(transaction.amount, ".2f") if transaction.amount is not None else None,
            "currency": transaction.currency,
            "direction": transaction.direction,
            "counterparty_name": transaction_counterparty_name(transaction),
            "description": transaction.description,
            "category": transaction.category,
            "excluded_from_statistics": transaction.excluded_from_statistics,
        }
        for transaction in transactions
    ]


async def update_transaction_statistics(*, transaction_id: int, settings: schemas.TransactionStatisticsSettings, owner_id: str) -> dict[str, Any]:
    async with database.tenant_session(owner_id) as db:
        transaction = await db.scalar(
            select(Transaction).join(Account).join(BankSession)
            .where(BankSession.owner_id == owner_id, Transaction.id == transaction_id)
        )
        if transaction is None:
            raise HTTPException(404, "Transaction not found")
        transaction.excluded_from_statistics = settings.excluded_from_statistics
        await db.commit()
    return {"id": transaction_id, "excluded_from_statistics": settings.excluded_from_statistics}


async def list_expenses(*, limit: int, month: str | None, owner_id: str) -> list[dict[str, Any]]:
    async with database.tenant_session(owner_id) as db:
        filters = [Account.monitored.is_(True), Transaction.direction == "DBIT"]
        effective_date = func.coalesce(Transaction.booking_date, Transaction.value_date)
        if month:
            start, end = dates.month_bounds(month)
            filters.extend([effective_date >= start, effective_date < end])
        expenses = list(
            (
                await db.scalars(
                    select(Transaction)
                    .join(Account)
                    .join(BankSession)
                    .where(BankSession.owner_id == owner_id, *filters)
                    .order_by(effective_date.desc())
                    .limit(limit)
                )
            ).all()
        )
        category_ids = {expense.category_id for expense in expenses if expense.category_id is not None}
        split_rows = list((await db.scalars(select(ExpenseSplit).where(ExpenseSplit.transaction_id.in_([expense.id for expense in expenses])))).all()) if expenses else []
        category_ids.update(split.category_id for split in split_rows)
        categories = {
            category.id: category
            for category in (
                await db.scalars(
                    select(Category).where(Category.owner_id == owner_id, Category.id.in_(category_ids))
                )
            ).all()
        }
    return [
        {
            "id": expense.id,
            "excluded_from_statistics": expense.excluded_from_statistics,
            **dates.transaction_date_details(expense),
            "amount": float(expense.amount) if expense.amount is not None else None,
            "currency": expense.currency,
            "counterparty_name": transaction_counterparty_name(expense),
            "description": expense.description,
            "category_id": expense.category_id,
            "splits": [
                {"category_id": split.category_id, "amount": float(split.amount)}
                for split in split_rows
                if split.transaction_id == expense.id
            ],
            "category": (
                {"id": categories[expense.category_id].id, "name": categories[expense.category_id].name, "icon": categories[expense.category_id].icon}
                if expense.category_id in categories
                else None
            ),
        }
        for expense in expenses
    ]


async def assign_expense(*, expense_id: int, assignment: schemas.CategoryAssignment, owner_id: str) -> dict[str, Any]:
    async with database.tenant_session(owner_id) as db:
        expense = await db.scalar(
            select(Transaction)
            .join(Account)
            .join(BankSession)
            .where(BankSession.owner_id == owner_id, Transaction.id == expense_id)
        )
        if expense is None:
            raise HTTPException(404, "Expense not found")
        if assignment.category_id is not None and await db.scalar(
            select(Category.id).where(Category.owner_id == owner_id, Category.id == assignment.category_id)
        ) is None:
            raise HTTPException(404, "Category not found")
        if assignment.category_id is not None:
            category = await db.scalar(
                select(Category).where(Category.owner_id == owner_id, Category.id == assignment.category_id)
            )
            if expense.booking_date and category.month != expense.booking_date.strftime("%Y-%m"):
                raise HTTPException(400, "Category belongs to another month")
        expense.category_id = assignment.category_id
        await db.execute(delete(ExpenseSplit).where(ExpenseSplit.transaction_id == expense_id))
        await db.commit()
    return {"id": expense_id, "category_id": assignment.category_id}


async def split_expense(*, expense_id: int, assignment: schemas.ExpenseSplitAssignment, owner_id: str) -> dict[str, Any]:
    async with database.tenant_session(owner_id) as db:
        expense = await db.scalar(
            select(Transaction)
            .join(Account)
            .join(BankSession)
            .where(BankSession.owner_id == owner_id, Transaction.id == expense_id)
        )
        if expense is None:
            raise HTTPException(404, "Expense not found")
        if expense.amount is None or expense.booking_date is None:
            raise HTTPException(400, "Expense cannot be split")
        category_ids = [line.category_id for line in assignment.splits]
        if len(set(category_ids)) != len(category_ids):
            raise HTTPException(400, "Each category can appear only once")
        categories = list(
            (
                await db.scalars(
                    select(Category).where(Category.owner_id == owner_id, Category.id.in_(category_ids))
                )
            ).all()
        )
        if len(categories) != len(category_ids):
            raise HTTPException(404, "Category not found")
        expense_month = expense.booking_date.strftime("%Y-%m")
        if any(category.month != expense_month for category in categories):
            raise HTTPException(400, "Categories must belong to the expense month")
        total = round(sum(line.amount for line in assignment.splits), 2)
        expected = round(float(expense.amount), 2)
        if total != expected:
            raise HTTPException(400, f"Split total must equal {expected:.2f}")
        await db.execute(delete(ExpenseSplit).where(ExpenseSplit.transaction_id == expense_id))
        db.add_all([ExpenseSplit(transaction_id=expense_id, category_id=line.category_id, amount=line.amount) for line in assignment.splits])
        expense.category_id = None
        await db.commit()
    return {"id": expense_id, "splits": [line.model_dump() for line in assignment.splits]}
