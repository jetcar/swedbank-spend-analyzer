import re
from datetime import timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, BankSession, Category, CategoryRule, ExpenseSplit, Transaction
from app.transaction_details import transaction_counterparty_name
from app.services.category_defaults import ensure_month_categories


CONTAINS_PREFIX = "contains:"


def normalize_match_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def transaction_search_text(transaction: Transaction) -> str:
    return normalize_match_text(" ".join(filter(None, (
        transaction_counterparty_name(transaction), transaction.description,
    ))))


def transaction_merchant_key(transaction: Transaction) -> str | None:
    counterparty = transaction_counterparty_name(transaction)
    if not counterparty:
        return None
    normalized = normalize_match_text(counterparty)
    return f"counterparty:{normalized}"[:500] or None


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
        rule.merchant_key: rule
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
    if months:
        for transaction_month_value in sorted(months):
            await ensure_month_categories(db, owner_id, transaction_month_value)
    categories = list((await db.scalars(select(Category).where(
        Category.owner_id == owner_id,
        Category.enabled.is_(True),
        Category.month.in_(months),
    ))).all()) if months else []
    category_by_month_name = {(category.month, category.name): category for category in categories}
    category_by_month_group = {(category.month, category.rule_group): category for category in categories}
    category_by_month_icon: dict[tuple[str, str], Category | None] = {}
    for category in categories:
        icon_key = (category.month, category.icon)
        category_by_month_icon[icon_key] = None if icon_key in category_by_month_icon else category
    contains_rules = sorted(
        (candidate for candidate in rules_by_key.values() if candidate.merchant_key.startswith(CONTAINS_PREFIX)),
        key=lambda candidate: (-len(candidate.merchant_key), candidate.merchant_key),
    )
    assigned = 0
    for transaction in transactions:
        key = transaction_merchant_key(transaction)
        search_text = transaction_search_text(transaction)
        rule = next((candidate for candidate in contains_rules
                     if candidate.merchant_key[len(CONTAINS_PREFIX):] in search_text), None)
        if rule is None:
            rule = rules_by_key.get(key)
        transaction_month_value = transaction_month(transaction)
        category = None if rule is None or not rule.category_group else category_by_month_group.get(
            (transaction_month_value, rule.category_group)
        )
        if category is None and rule is not None:
            category = category_by_month_name.get((transaction_month_value, rule.category_name))
        if category is None and rule is not None and rule.category_icon:
            category = category_by_month_icon.get((transaction_month_value, rule.category_icon))
        if category is not None:
            transaction.category_id = category.id
            assigned += 1
    return assigned


async def category_keywords(
    db: AsyncSession, owner_id: str, categories: list[Category],
) -> dict[int, list[str]]:
    rules = (await db.scalars(select(CategoryRule).where(
        CategoryRule.owner_id == owner_id,
        CategoryRule.merchant_key.startswith(CONTAINS_PREFIX),
    ).order_by(CategoryRule.merchant_key))).all()
    result: dict[int, list[str]] = {category.id: [] for category in categories}
    categories_by_group = {category.rule_group: category for category in categories}
    for rule in rules:
        category = categories_by_group.get(rule.category_group)
        if category is not None:
            result[category.id].append(rule.merchant_key[len(CONTAINS_PREFIX):])
    return result


async def replace_category_keywords(
    db: AsyncSession, owner_id: str, category: Category, keywords: list[str],
) -> int:
    normalized = [normalize_match_text(keyword) for keyword in keywords]
    keys = {f"{CONTAINS_PREFIX}{keyword}" for keyword in normalized}
    existing = list((await db.scalars(select(CategoryRule).where(
        CategoryRule.owner_id == owner_id,
        CategoryRule.merchant_key.in_(keys),
    ))).all()) if keys else []
    conflicts = [rule for rule in existing if rule.category_group != category.rule_group]
    if conflicts:
        from fastapi import HTTPException
        raise HTTPException(409, f'Keyword already belongs to category "{conflicts[0].category_name}"')
    await db.execute(delete(CategoryRule).where(
        CategoryRule.owner_id == owner_id,
        CategoryRule.category_group == category.rule_group,
        CategoryRule.merchant_key.startswith(CONTAINS_PREFIX),
    ))
    db.add_all(CategoryRule(
        owner_id=owner_id, merchant_key=key, category_name=category.name, category_icon=category.icon,
        category_group=category.rule_group,
    ) for key in sorted(keys))
    await db.flush()
    return await apply_category_rules(db, owner_id, merchant_keys=keys)


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
        db.add(CategoryRule(owner_id=owner_id, merchant_key=key, category_name=category.name,
                            category_icon=category.icon, category_group=category.rule_group))
    else:
        rule.category_name = category.name
        rule.category_icon = category.icon
        rule.category_group = category.rule_group
    await db.flush()
    return await apply_category_rules(db, owner_id, merchant_keys={key})


async def forget_category_rule(db: AsyncSession, owner_id: str, transaction: Transaction) -> None:
    key = transaction_merchant_key(transaction)
    if key:
        await db.execute(delete(CategoryRule).where(
            CategoryRule.owner_id == owner_id, CategoryRule.merchant_key == key,
        ))
