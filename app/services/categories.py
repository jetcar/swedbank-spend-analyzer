from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import func, select, union_all, update
from sqlalchemy.exc import IntegrityError

from app import database, dates, schemas
from app.services import category_rules
from app.services.category_defaults import (
    CATEGORY_DEFAULTS, category_defaults, ensure_month_categories, localize_untouched_defaults,
)
from app.models import Account, BankSession, Category, CategoryRule, ExpenseSplit, Transaction


CENT = Decimal("0.01")


def money_string(amount: Decimal) -> str:
    return str(Decimal(amount).quantize(CENT, rounding=ROUND_HALF_UP))


def effective_monthly_budget(amount: Decimal, period: str, month: str) -> Decimal:
    year, month_number = (int(value) for value in month.split("-"))
    days = Decimal(monthrange(year, month_number)[1])
    multiplier = {"month": Decimal(1), "week": days / Decimal(7), "day": days}[period]
    return (Decimal(amount) * multiplier).quantize(CENT, rounding=ROUND_HALF_UP)


def effective_available_budget(amount: Decimal, period: str, month: str, *, today: date | None = None) -> Decimal:
    monthly = effective_monthly_budget(amount, period, month)
    if period == "month":
        return monthly
    current = today or dates.current_date()
    current_month = current.strftime("%Y-%m")
    if month < current_month:
        return monthly
    if month > current_month:
        return Decimal("-0.00") if Decimal(amount) < 0 else Decimal("0.00")
    elapsed_days = Decimal(current.day)
    multiplier = elapsed_days if period == "day" else elapsed_days / Decimal(7)
    return (Decimal(amount) * multiplier).quantize(CENT, rounding=ROUND_HALF_UP)


async def list_categories(*, month: str, language: Literal['ru', 'en', 'et'], owner_id: str) -> list[dict[str, Any]]:
    selected_month = month or dates.current_month()
    async with database.tenant_session(owner_id) as db:
        categories = await ensure_month_categories(db, owner_id, selected_month, language)
        if await category_rules.apply_category_rules(db, owner_id, month=selected_month):
            await db.commit()
        start, end = dates.month_bounds(selected_month)
        # Aggregate the complete month, independently of expense list limits.
        effective_date = func.coalesce(Transaction.booking_date, Transaction.value_date)
        monthly = (
            select(Transaction.id, Transaction.category_id, Transaction.amount, Transaction.direction)
            .join(Account).join(BankSession)
            .where(
                BankSession.owner_id == owner_id, Account.monitored.is_(True),
                Transaction.direction.in_(("DBIT", "CRDT")),
                Transaction.excluded_from_statistics.is_(False),
                effective_date >= start, effective_date < end,
            ).cte("monthly")
        )
        has_splits = select(ExpenseSplit.id).where(ExpenseSplit.transaction_id == monthly.c.id).exists()
        allocations = union_all(
            select(monthly.c.category_id, monthly.c.amount, monthly.c.direction).where(~has_splits),
            select(ExpenseSplit.category_id, ExpenseSplit.amount, monthly.c.direction)
            .join(monthly, monthly.c.id == ExpenseSplit.transaction_id),
        ).cte("allocations")
        totals = (await db.execute(
            select(
                allocations.c.category_id,
                func.sum(allocations.c.amount).filter(allocations.c.direction == "DBIT"),
                func.sum(allocations.c.amount).filter(allocations.c.direction == "CRDT"),
            ).where(allocations.c.category_id.is_not(None)).group_by(allocations.c.category_id)
        )).all()
        spent = {category_id: amount for category_id, amount, _ in totals}
        earned = {category_id: amount for category_id, _, amount in totals}
    return [
        {"id": category.id, "name": category.name, "icon": category.icon, "month": category.month, "budget": money_string(category.budget or Decimal(0)),
         "budget_period": category.budget_period,
         "monthly_budget": money_string(effective_monthly_budget(category.budget or 0, category.budget_period, category.month)),
         "available_budget": money_string(effective_available_budget(category.budget or 0, category.budget_period, category.month)),
         "enabled": category.enabled, "spent": money_string(spent.get(category.id) or Decimal(0)),
         "earned": money_string(earned.get(category.id) or Decimal(0)),
         "currency": category.currency, "forecast_mode": category.forecast_mode, "payment_status": category.payment_status}
        for category in categories
    ]


async def create_category(*, category_input: schemas.NewCategory, owner_id: str) -> dict[str, Any]:
    selected_month = category_input.month
    dates.month_bounds(selected_month)
    async with database.tenant_session(owner_id) as db:
        exists = await db.scalar(
            select(Category).where(
                Category.owner_id == owner_id,
                Category.name == category_input.name.strip(),
                Category.month == selected_month,
            )
        )
        if exists:
            raise HTTPException(409, "Category already exists for this month")
        category = Category(owner_id=owner_id, name=category_input.name.strip(), icon=category_input.icon, month=selected_month,
                            budget=category_input.budget, budget_period=category_input.budget_period, name_customized=True)
        db.add(category)
        await db.commit()
        await db.refresh(category)
    return {"id": category.id, "name": category.name, "icon": category.icon, "month": category.month,
            "budget": money_string(category.budget or Decimal(0)), "budget_period": category.budget_period,
            "monthly_budget": money_string(effective_monthly_budget(category.budget or 0, category.budget_period, category.month)),
            "available_budget": money_string(effective_available_budget(category.budget or 0, category.budget_period, category.month)),
            "enabled": category.enabled}


async def update_category(*, category_id: int, settings: schemas.CategorySettings, owner_id: str) -> dict[str, Any]:
    async with database.tenant_session(owner_id) as db:
        category = await db.scalar(
            select(Category).where(Category.owner_id == owner_id, Category.id == category_id)
        )
        if category is None:
            raise HTTPException(404, "Category not found")
        if settings.name is not None:
            if settings.name != category.name:
                old_name = category.name
                duplicate = await db.scalar(
                    select(Category.id).where(
                        Category.owner_id == owner_id,
                        Category.month == category.month,
                        Category.name == settings.name,
                    )
                )
                if duplicate is not None:
                    raise HTTPException(409, "Category already exists for this month")
                category.name = settings.name
                await db.execute(update(CategoryRule).where(
                    CategoryRule.owner_id == owner_id, CategoryRule.category_name == old_name,
                ).values(category_name=settings.name))
            category.name_customized = True
        if settings.budget is not None:
            category.budget = settings.budget
        if settings.budget_period is not None:
            category.budget_period = settings.budget_period
        if settings.enabled is not None:
            category.enabled = settings.enabled
        if settings.icon is not None:
            category.icon = settings.icon
        for field in ("currency", "forecast_mode", "payment_status"):
            value = getattr(settings, field)
            if value is not None:
                setattr(category, field, value)
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            if settings.name is not None and "uq_category_owner_name_month" in str(exc.orig):
                raise HTTPException(409, "Category already exists for this month") from exc
            raise
    return {"id": category_id, "name": category.name, "budget": money_string(category.budget or Decimal(0)),
            "budget_period": category.budget_period,
            "monthly_budget": money_string(effective_monthly_budget(category.budget or 0, category.budget_period, category.month)),
            "available_budget": money_string(effective_available_budget(category.budget or 0, category.budget_period, category.month)),
            "enabled": category.enabled, "icon": category.icon}


async def delete_category(*, category_id: int, owner_id: str) -> dict[str, Any]:
    async with database.tenant_session(owner_id) as db:
        category = await db.scalar(
            select(Category).where(Category.owner_id == owner_id, Category.id == category_id)
        )
        if category is None:
            raise HTTPException(404, "Category not found")
        await db.execute(update(Transaction).where(Transaction.category_id == category_id).values(category_id=None))
        await db.delete(category)
        await db.commit()
    return {"id": category_id, "deleted": True}
