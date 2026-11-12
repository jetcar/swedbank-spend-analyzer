from typing import Any

from sqlalchemy import func, select

from app import database, dates
from app.transaction_details import transaction_counterparty_name
from app.models import Account, BankSession, Category, ExpenseSplit, Transaction


async def expense_summary(*, group_by: str, owner_id: str) -> list[dict[str, Any]]:
    async with database.tenant_session(owner_id) as db:
        transactions = list(
            (
                await db.scalars(
                    select(Transaction)
                    .join(Account)
                    .join(BankSession)
                    .where(
                        BankSession.owner_id == owner_id,
                        Account.monitored.is_(True),
                        Transaction.direction == "DBIT",
                        Transaction.excluded_from_statistics.is_(False),
                    )
                )
            ).all()
        )
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for transaction in transactions:
        if group_by == "month":
            key = transaction.booking_date.strftime("%Y-%m") if transaction.booking_date else "Без даты"
        elif group_by == "category":
            key = transaction.category or "Без категории"
        else:
            key = transaction.description or "Без описания"
        currency = transaction.currency or "EUR"
        group = groups.setdefault((key, currency), {"name": key, "currency": currency, "total": 0.0, "count": 0})
        group["total"] = round(group["total"] + float(transaction.amount or 0), 2)
        group["count"] += 1
    return sorted(groups.values(), key=lambda group: group["total"], reverse=True)


async def daily_summary(*, owner_id: str) -> list[dict[str, Any]]:
    async with database.tenant_session(owner_id) as db:
        effective_date = func.coalesce(Transaction.booking_date, Transaction.value_date)
        transactions = list(
            (
                await db.scalars(
                    select(Transaction)
                    .join(Account)
                    .join(BankSession)
                    .where(
                        BankSession.owner_id == owner_id,
                        Account.monitored.is_(True),
                        Transaction.direction == "DBIT",
                        Transaction.excluded_from_statistics.is_(False),
                        effective_date.is_not(None),
                    )
                )
            ).all()
        )
    days: dict[tuple[str, str], dict[str, Any]] = {}
    for transaction in transactions:
        display_date = dates.transaction_date_details(transaction)["date"]
        if display_date is None:
            continue
        day = display_date.date().isoformat()
        currency = transaction.currency or "EUR"
        group = days.setdefault((day, currency), {"date": day, "currency": currency, "total": 0.0, "count": 0})
        group["total"] = round(group["total"] + float(transaction.amount or 0), 2)
        group["count"] += 1
    return sorted(days.values(), key=lambda group: group["date"], reverse=True)


async def daily_breakdown(*, month: str | None, owner_id: str) -> list[dict[str, Any]]:
    async with database.tenant_session(owner_id) as db:
        effective_date = func.coalesce(Transaction.booking_date, Transaction.value_date)
        filters = [
            Account.monitored.is_(True),
            Transaction.direction == "DBIT",
            Transaction.excluded_from_statistics.is_(False),
            effective_date.is_not(None),
        ]
        if month:
            start, end = dates.month_bounds(month)
            filters.extend([effective_date >= start, effective_date < end])
        transactions = list(
            (
                await db.scalars(
                    select(Transaction)
                    .join(Account)
                    .join(BankSession)
                    .where(BankSession.owner_id == owner_id, *filters)
                )
            ).all()
        )
        transaction_ids = [transaction.id for transaction in transactions]
        split_rows = list((await db.scalars(select(ExpenseSplit).where(ExpenseSplit.transaction_id.in_(transaction_ids)))).all()) if transaction_ids else []
        splits_by_transaction: dict[int, list[ExpenseSplit]] = {}
        for split in split_rows:
            splits_by_transaction.setdefault(split.transaction_id, []).append(split)
        category_ids = {transaction.category_id for transaction in transactions if transaction.category_id is not None}
        category_ids.update(split.category_id for split in split_rows)
        categories = {
            category.id: category
            for category in (
                await db.scalars(
                    select(Category).where(Category.owner_id == owner_id, Category.id.in_(category_ids))
                )
            ).all()
        }
        assignable_categories = list(
            (
                await db.scalars(
                    select(Category)
                    .where(Category.owner_id == owner_id, Category.enabled.is_(True))
                    .order_by(Category.name)
                )
            ).all()
        )
        categories_by_month: dict[str, list[Category]] = {}
        for category in assignable_categories:
            categories_by_month.setdefault(category.month, []).append(category)
    days: dict[str, dict[str, Any]] = {}
    for transaction in transactions:
        date_details = dates.transaction_date_details(transaction)
        if date_details["date"] is None:
            continue
        day = date_details["date"].date().isoformat()
        day_data = days.setdefault(day, {"date": day, "total": 0.0, "count": 0, "categories": {}})
        original_amount = float(transaction.amount or 0)
        allocations = splits_by_transaction.get(transaction.id) or [None]
        day_data["total"] = round(day_data["total"] + original_amount, 2)
        day_data["count"] += 1
        for split in allocations:
            category_id = split.category_id if split else transaction.category_id
            category = categories.get(category_id)
            category_key = str(category_id) if category else "uncategorized"
            amount = float(split.amount) if split else original_amount
            category_data = day_data["categories"].setdefault(
                category_key,
                {
                    "id": category.id if category else None,
                    "name": category.name if category else "Без категории",
                    "icon": category.icon if category else "📋",
                    "total": 0.0,
                    "count": 0,
                    "transactions": [],
                },
            )
            category_data["total"] = round(category_data["total"] + amount, 2)
            category_data["count"] += 1
            category_data["transactions"].append(
                {
                    "id": transaction.id,
                    **date_details,
                    "counterparty_name": transaction_counterparty_name(transaction),
                    "description": transaction.description,
                    "amount": amount,
                    "currency": transaction.currency or "EUR",
                    "split": bool(split),
                }
            )
    return [
        {
            "date": day["date"],
            "total": day["total"],
            "count": day["count"],
            "assignable_categories": [
                {"id": category.id, "name": category.name, "icon": category.icon}
                for category in categories_by_month.get(day["date"][:7], [])
            ],
            "categories": sorted(
                day["categories"].values(),
                key=lambda category: (category["name"] != "Без категории", -category["total"]),
            ),
        }
        for day in sorted(days.values(), key=lambda item: item["date"], reverse=True)
    ]
