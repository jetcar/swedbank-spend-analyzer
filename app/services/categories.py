from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import func, select, union_all, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import database, dates, schemas
from app.models import Account, BankSession, Category, ExpenseSplit, Transaction


CATEGORY_DEFAULTS = {
    "ru": (("Еда", "🍽️"), ("Транспорт", "🚗"), ("Дом", "🏠"), ("Подписки", "📱"), ("Прочее", "🧾")),
    "en": (("Food", "🍽️"), ("Transport", "🚗"), ("Home", "🏠"), ("Subscriptions", "📱"), ("Other", "🧾")),
    "et": (("Toit", "🍽️"), ("Transport", "🚗"), ("Kodu", "🏠"), ("Tellimused", "📱"), ("Muu", "🧾")),
}


def category_defaults(language: str) -> tuple[tuple[str, str], ...]:
    return CATEGORY_DEFAULTS.get(language, CATEGORY_DEFAULTS["ru"])


def localize_untouched_defaults(categories: list[Category], language: str) -> bool:
    current = {(category.name, category.icon) for category in categories}
    is_default_set = any(current == set(defaults) for defaults in CATEGORY_DEFAULTS.values())
    if not is_default_set or any(
        category.name_customized or category.budget or category.enabled is False
        or category.currency not in (None, "EUR")
        or category.forecast_mode not in (None, "budget")
        or category.payment_status not in (None, "auto")
        for category in categories
    ):
        return False
    names_by_icon = {icon: name for name, icon in category_defaults(language)}
    for category in categories:
        category.name = names_by_icon[category.icon]
    return True


async def ensure_month_categories(db: AsyncSession, owner_id: str, month: str, language: str = "ru") -> list[Category]:
    dates.month_bounds(month)
    categories = list(
        (
            await db.scalars(
                select(Category).where(Category.owner_id == owner_id, Category.month == month).order_by(Category.name)
            )
        ).all()
    )
    if categories:
        if localize_untouched_defaults(categories, language):
            await db.commit()
        return categories
    year, month_number = (int(value) for value in month.split("-"))
    previous_month = f"{year - (month_number == 1):04d}-{12 if month_number == 1 else month_number - 1:02d}"
    previous = list(
        (
            await db.scalars(
                select(Category)
                .where(Category.owner_id == owner_id, Category.month == previous_month)
                .order_by(Category.name)
            )
        ).all()
    )
    if previous:
        categories = [
            Category(owner_id=owner_id, name=category.name, icon=category.icon, month=month, budget=category.budget, enabled=category.enabled, name_customized=category.name_customized,
                     currency=category.currency, forecast_mode=category.forecast_mode, payment_status="auto")
            for category in previous
        ]
        localize_untouched_defaults(categories, language)
    else:
        categories = [
            Category(owner_id=owner_id, name=name, icon=icon, month=month)
            for name, icon in category_defaults(language)
        ]
    db.add_all(categories)
    try:
        await db.commit()
    except IntegrityError:
        # Concurrent first reads (categories and plan) may create the same month.
        await db.rollback()
        categories = list((await db.scalars(
            select(Category).where(Category.owner_id == owner_id, Category.month == month).order_by(Category.name)
        )).all())
        if not categories:
            raise
    return categories


async def list_categories(*, month: str, language: Literal['ru', 'en', 'et'], owner_id: str) -> list[dict[str, Any]]:
    selected_month = month or dates.current_month()
    async with database.tenant_session(owner_id) as db:
        categories = await ensure_month_categories(db, owner_id, selected_month, language)
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
        {"id": category.id, "name": category.name, "icon": category.icon, "month": category.month, "budget": float(category.budget or 0), "enabled": category.enabled, "spent": float(spent.get(category.id) or 0), "earned": float(earned.get(category.id) or 0),
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
        category = Category(owner_id=owner_id, name=category_input.name.strip(), icon=category_input.icon, month=selected_month, budget=category_input.budget, name_customized=True)
        db.add(category)
        await db.commit()
        await db.refresh(category)
    return {"id": category.id, "name": category.name, "icon": category.icon, "month": category.month, "budget": float(category.budget or 0), "enabled": category.enabled}


async def update_category(*, category_id: int, settings: schemas.CategorySettings, owner_id: str) -> dict[str, Any]:
    async with database.tenant_session(owner_id) as db:
        category = await db.scalar(
            select(Category).where(Category.owner_id == owner_id, Category.id == category_id)
        )
        if category is None:
            raise HTTPException(404, "Category not found")
        if settings.name is not None:
            if settings.name != category.name:
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
            category.name_customized = True
        if settings.budget is not None:
            category.budget = settings.budget
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
    return {"id": category_id, "name": category.name, "budget": float(category.budget or 0), "enabled": category.enabled, "icon": category.icon}


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
