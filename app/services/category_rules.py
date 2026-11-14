import re
from datetime import timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, BankSession, Category, CategoryRule, ExpenseSplit, Transaction
from app.transaction_details import transaction_counterparty_name


def transaction_merchant_key(transaction: Transaction) -> str | None:
    counterparty = transaction_counterparty_name(transaction)
    value = counterparty or transaction.description
    if not value:
        return None
    normalized = re.sub(r"\s+", " ", value).strip().casefold()
    return f"{'counterparty' if counterparty else 'description'}:{normalized}"[:500] or None


def transaction_month(transaction: Transaction) -> str | None:
    value = transaction.booking_date or transaction.value_date
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m")


async def apply_category_rules(
    db: AsyncSession, owner_id: str, *, month: str | None = None, merchant_keys: set[str] | None = None,
) -> int:
    rules = list((await db.scalars(select(CategoryRule).where(CategoryRule.owner_id == owner_id))).all())
    rules_by_key = {
        rule.merchant_key: rule.category_name
        for rule in rules
        if merchant_keys is None or rule.merchant_key in merchant_keys
    }
    if not rules_by_key:
        return 0
    filters = [
        BankSession.owner_id == owner_id,
        Transaction.direction == "DBIT",
        Transaction.category_id.is_(None),
        ~select(ExpenseSplit.id).where(ExpenseSplit.transaction_id == Transaction.id).exists(),
    ]
    if month:
        from app import dates
        from sqlalchemy import func

        start, end = dates.month_bounds(month)
        effective_date = func.coalesce(Transaction.booking_date, Transaction.value_date)
        filters.extend([effective_date >= start, effective_date < end])
    transactions = list((await db.scalars(
        select(Transaction).join(Account).join(BankSession).where(*filters)
    )).all())
    months = {value for transaction in transactions if (value := transaction_month(transaction))}
    names = set(rules_by_key.values())
    categories = list((await db.scalars(select(Category).where(
        Category.owner_id == owner_id,
        Category.enabled.is_(True),
        Category.month.in_(months),
        Category.name.in_(names),
    ))).all()) if months else []
    category_by_month_name = {(category.month, category.name): category for category in categories}
    assigned = 0
    for transaction in transactions:
        key = transaction_merchant_key(transaction)
        category_name = rules_by_key.get(key)
        category = category_by_month_name.get((transaction_month(transaction), category_name))
        if category is not None:
            transaction.category_id = category.id
            assigned += 1
    return assigned


async def remember_category_rule(
    db: AsyncSession, owner_id: str, transaction: Transaction, category: Category,
) -> int:
    key = transaction_merchant_key(transaction)
    if transaction.direction != "DBIT" or not key:
        return 0
    rule = await db.scalar(select(CategoryRule).where(
        CategoryRule.owner_id == owner_id, CategoryRule.merchant_key == key,
    ))
    if rule is None:
        db.add(CategoryRule(owner_id=owner_id, merchant_key=key, category_name=category.name))
    else:
        rule.category_name = category.name
    await db.flush()
    return await apply_category_rules(db, owner_id, merchant_keys={key})


async def forget_category_rule(db: AsyncSession, owner_id: str, transaction: Transaction) -> None:
    key = transaction_merchant_key(transaction)
    if key:
        await db.execute(delete(CategoryRule).where(
            CategoryRule.owner_id == owner_id, CategoryRule.merchant_key == key,
        ))
