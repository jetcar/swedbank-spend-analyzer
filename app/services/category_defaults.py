"""Monthly category initialization shared by category queries and rules."""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import dates
from app.models import Category


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
        or category.budget_period not in (None, "month")
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
            Category(owner_id=owner_id, name=category.name, icon=category.icon, month=month, budget=category.budget, budget_period=category.budget_period, enabled=category.enabled, name_customized=category.name_customized,
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
