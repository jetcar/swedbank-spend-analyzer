from contextlib import aclosing
from decimal import Decimal
from hashlib import sha256
from typing import Any
import json
import re

from sqlalchemy import select, update

from app import bank_client, database, dates
from app.services import sync_state
from app.models import Account, BankSession, ExpenseSplit, PlanItemTransaction, Transaction


_CARD_NUMBER_RE = re.compile(r"(?<!\d)(\d{6}\*{4,}\d{4})(?!\d)")
_CARD_DATE_RE = re.compile(r"(?<!\d)(\d{2}\.\d{2}\.\d{2})(?!\d)")
_CARD_TIME_RE = re.compile(r"(?<!\d)\d{2}:\d{2}(?!\d)")
_INSTALLMENT_PREFIX_RE = re.compile(r"\bIntressivaba\s+kuni\s+\d{2}\.\d{2}\.\d{4};?", re.IGNORECASE)
_CARD_NOISE_WORDS = {"EUR", "INTRESSIVABA", "KUNI", "OST", "SULARAHA"}
_BOOKED_STATUSES = {"BOOK", "BOOKED"}


def _remittance_text(data: dict[str, Any]) -> str:
    values = data.get("remittance_information") or []
    if isinstance(values, str):
        return values
    return " ".join(str(value) for value in values)


def _card_merchant_words(data: dict[str, Any]) -> list[str]:
    merchant = _INSTALLMENT_PREFIX_RE.sub(" ", _remittance_text(data))
    merchant = _CARD_NUMBER_RE.sub(" ", merchant)
    merchant = _CARD_DATE_RE.sub(" ", merchant)
    merchant = _CARD_TIME_RE.sub(" ", merchant)
    merchant = re.sub(r"\([^)]*\)", " ", merchant)
    merchant = re.sub(r"(?<!\w)\w*\d\w*(?!\w)", " ", merchant)
    merchant = re.sub(r"(?<!\w)[+-]?\d+(?:[.,]\d+)?(?!\w)", " ", merchant)
    words = [
        word.upper()
        for word in re.findall(r"[^\W\d_]+", merchant, re.UNICODE)
        if word.upper() not in _CARD_NOISE_WORDS
    ]
    while words[-1:] == ["EE"]:
        words.pop()
    return words


def _merchant_word_boundaries_match(first: dict[str, Any], second: dict[str, Any]) -> bool:
    def boundaries(words: list[str]) -> set[int]:
        positions: set[int] = set()
        position = 0
        for word in words[:-1]:
            position += len(word)
            positions.add(position)
        return positions

    first_boundaries = boundaries(_card_merchant_words(first))
    second_boundaries = boundaries(_card_merchant_words(second))
    return first_boundaries <= second_boundaries or second_boundaries <= first_boundaries


def _card_transaction_match_key(data: dict[str, Any], *, compact_merchant: bool) -> str | None:
    text = _remittance_text(data)
    card_match = _CARD_NUMBER_RE.search(text)
    date_match = _CARD_DATE_RE.search(text)
    amount = data.get("transaction_amount") or data.get("amount") or {}
    if not card_match or not date_match or amount.get("amount") is None:
        return None

    words = _card_merchant_words(data)
    if not words:
        return None
    normalized_amount = format(Decimal(str(amount["amount"])), "f")
    stable_data = "|".join(
        (
            card_match.group(1),
            date_match.group(1),
            normalized_amount,
            str(amount.get("currency") or ""),
            str(data.get("credit_debit_indicator") or ""),
            "".join(words) if compact_merchant else " ".join(words),
        )
    )
    return sha256(stable_data.encode()).hexdigest()


def card_transaction_match_key(data: dict[str, Any]) -> str | None:
    return _card_transaction_match_key(data, compact_merchant=False)


def _compact_card_transaction_match_key(data: dict[str, Any]) -> str | None:
    return _card_transaction_match_key(data, compact_merchant=True)


def card_transaction_variant(data: dict[str, Any]) -> str | None:
    text = _remittance_text(data)
    if not card_transaction_match_key(data):
        return None
    if _INSTALLMENT_PREFIX_RE.search(text):
        return "installment"
    if _CARD_TIME_RE.search(text):
        return "authorization"
    return "settled"


def _status(data: dict[str, Any]) -> str:
    return str(data.get("status") or "").upper()


def _is_pending_booked_transition(first: dict[str, Any], second: dict[str, Any]) -> bool:
    statuses = {_status(first), _status(second)}
    return "PDNG" in statuses and bool(statuses & _BOOKED_STATUSES) and len(statuses) == 2


def _same_remittance(first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_text = " ".join(_remittance_text(first).split()).casefold()
    second_text = " ".join(_remittance_text(second).split()).casefold()
    return bool(first_text) and first_text == second_text


def _is_repeated_pending_transaction(first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_key = card_transaction_match_key(first)
    return (
        _status(first) == _status(second) == "PDNG"
        and bool(first_key)
        and first_key == card_transaction_match_key(second)
        and _same_remittance(first, second)
    )


def _provider_booking_dates(data: dict[str, Any], transaction_date) -> set:
    raw_values = data.get("_provider_booking_dates") or []
    if not isinstance(raw_values, list):
        raw_values = []
    values = [*raw_values, data.get("booking_date") or data.get("value_date")]
    return {
        parsed
        for value in values
        if (parsed := dates.parse_datetime(value)) is not None
        and transaction_date is not None
        and transaction_date < parsed
    }


def _provider_transaction_ids(data: dict[str, Any]) -> set[str]:
    raw_values = data.get("_provider_transaction_ids") or []
    if not isinstance(raw_values, list):
        raw_values = []
    return {
        str(value)
        for value in (*raw_values, data.get("entry_reference"), data.get("transaction_id"))
        if value
    }


def _is_known_provider_transaction(first: dict[str, Any], second: dict[str, Any]) -> bool:
    has_id_history = bool(
        first.get("_provider_transaction_ids") or second.get("_provider_transaction_ids")
    )
    return has_id_history and bool(
        _provider_transaction_ids(first) & _provider_transaction_ids(second)
    )


def _is_repeated_booked_transaction(first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_key = card_transaction_match_key(first)
    first_transaction_date = dates.parse_datetime(first.get("transaction_date"))
    second_transaction_date = dates.parse_datetime(second.get("transaction_date"))
    first_booking_dates = _provider_booking_dates(first, first_transaction_date)
    second_booking_dates = _provider_booking_dates(second, second_transaction_date)
    has_id_history = bool(
        first.get("_provider_transaction_ids") or second.get("_provider_transaction_ids")
    )
    same_transaction = (
        _status(first) in _BOOKED_STATUSES
        and _status(second) in _BOOKED_STATUSES
        and bool(first_key)
        and first_key == card_transaction_match_key(second)
        and _same_remittance(first, second)
        and first_transaction_date is not None
        and first_transaction_date == second_transaction_date
        and bool(first_booking_dates)
        and bool(second_booking_dates)
    )
    if not same_transaction:
        return False
    if has_id_history:
        return _is_known_provider_transaction(first, second)
    return len(first_booking_dates | second_booking_dates) > 1


def _can_merge_card_transactions(
    first: dict[str, Any], second: dict[str, Any], *, compact_merchant: bool
) -> bool:
    key = _compact_card_transaction_match_key if compact_merchant else card_transaction_match_key
    first_key = key(first)
    if not first_key or first_key != key(second):
        return False
    if compact_merchant and not _merchant_word_boundaries_match(first, second):
        return False
    first_variant = card_transaction_variant(first)
    second_variant = card_transaction_variant(second)
    if _is_repeated_pending_transaction(first, second) or _is_repeated_booked_transaction(first, second):
        return True
    if _is_pending_booked_transition(first, second):
        return first_variant != second_variant or _same_remittance(first, second)
    if _status(first) != _status(second):
        return False
    return first_variant != second_variant


def can_merge_card_transactions(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return _can_merge_card_transactions(first, second, compact_merchant=False)


def transaction_identifier(data: dict[str, Any]) -> str:
    known_id = data.get("entry_reference") or data.get("transaction_id")
    if known_id:
        return str(known_id)
    stable_data = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(stable_data.encode()).hexdigest()


def transaction_description(data: dict[str, Any]) -> str | None:
    values = data.get("remittance_information") or data.get("additional_information") or []
    if isinstance(values, str):
        return values
    return " ".join(str(value) for value in values) if values else None


def _transaction_amount(data: dict[str, Any]) -> tuple[Decimal | None, str | None]:
    amount = data.get("transaction_amount") or data.get("amount") or {}
    value = amount.get("amount")
    return (Decimal(str(value)) if value is not None else None, amount.get("currency"))


def _raw_data_with_first_dates(existing: Transaction, incoming: dict[str, Any]) -> dict[str, Any]:
    current = existing.raw_data if isinstance(existing.raw_data, dict) else {}
    merged = dict(incoming)
    has_first_date = existing.booking_date is not None or existing.value_date is not None or any(
        current.get(key) for key in ("booking_date", "transaction_date", "value_date")
    )
    if has_first_date:
        for key in ("booking_date", "transaction_date", "value_date"):
            if current.get(key):
                merged[key] = current[key]
    if current.get("_provider_booking_dates"):
        merged["_provider_booking_dates"] = current["_provider_booking_dates"]
    if current.get("_provider_transaction_ids"):
        merged["_provider_transaction_ids"] = current["_provider_transaction_ids"]
    return merged


def _balance_contribution(account: Account, transaction: Transaction) -> Decimal:
    if account.currency and transaction.currency and account.currency != transaction.currency:
        return Decimal("0")
    amount = abs(Decimal(str(transaction.amount or 0)))
    if transaction.direction == "CRDT":
        return amount
    if transaction.direction == "DBIT":
        return -amount
    return Decimal("0")


async def _merge_duplicate_annotations(
    db,
    account: Account,
    survivor: Transaction,
    duplicate: Transaction,
) -> None:
    survivor_plan = await db.scalar(
        select(PlanItemTransaction.item_id).where(PlanItemTransaction.transaction_id == survivor.id)
    )
    if survivor_plan is None:
        await db.execute(
            update(PlanItemTransaction)
            .where(PlanItemTransaction.transaction_id == duplicate.id)
            .values(transaction_id=survivor.id)
        )
    # One logical transaction can fulfill only one item. An existing survivor
    # allocation takes precedence; deleting the duplicate removes its extra link.
    if survivor.category_id is None and duplicate.category_id is not None:
        survivor.category_id = duplicate.category_id
    if survivor.category is None and duplicate.category is not None:
        survivor.category = duplicate.category
    survivor.excluded_from_statistics = survivor.excluded_from_statistics or duplicate.excluded_from_statistics
    survivor_has_splits = await db.scalar(
        select(ExpenseSplit.id).where(ExpenseSplit.transaction_id == survivor.id).limit(1)
    )
    if survivor_has_splits is None:
        await db.execute(
            update(ExpenseSplit)
            .where(ExpenseSplit.transaction_id == duplicate.id)
            .values(transaction_id=survivor.id)
        )
    survivor_raw = survivor.raw_data or {}
    duplicate_raw = duplicate.raw_data or {}
    is_duplicate_balance = _is_pending_booked_transition(
        survivor_raw, duplicate_raw
    ) or _is_repeated_pending_transaction(survivor_raw, duplicate_raw)
    if not is_duplicate_balance:
        contribution = _balance_contribution(account, duplicate)
        if contribution:
            await db.execute(
                update(Account)
                .where(Account.id == account.id)
                .values(balance_adjustment=Account.balance_adjustment + contribution)
            )
    await db.delete(duplicate)


async def _matching_card_transactions(
    db,
    account_id: int,
    raw: dict[str, Any],
    amount: Decimal | None,
    currency: str | None,
    exclude_id: int | None = None,
) -> list[Transaction]:
    if card_transaction_match_key(raw) is None or amount is None:
        return []
    candidates = list(
        (
            await db.scalars(
                select(Transaction).where(
                    Transaction.account_id == account_id,
                    Transaction.amount == amount,
                    Transaction.currency == currency,
                    Transaction.direction == raw.get("credit_debit_indicator"),
                )
            )
        ).all()
    )
    if exclude_id is not None:
        candidates = [candidate for candidate in candidates if candidate.id != exclude_id]
    exact_matches = [
        candidate
        for candidate in candidates
        if can_merge_card_transactions(candidate.raw_data or {}, raw)
    ]
    known_provider_matches = [
        candidate
        for candidate in exact_matches
        if _is_known_provider_transaction(candidate.raw_data or {}, raw)
    ]
    if known_provider_matches:
        return known_provider_matches
    compact_matches = [
        candidate
        for candidate in candidates
        if _can_merge_card_transactions(candidate.raw_data or {}, raw, compact_merchant=True)
    ]
    if not exact_matches:
        return compact_matches
    if all(
        _is_repeated_pending_transaction(candidate.raw_data or {}, raw)
        for candidate in exact_matches
    ):
        exact_ids = {candidate.id for candidate in exact_matches}
        return exact_matches + [candidate for candidate in compact_matches if candidate.id not in exact_ids]
    return exact_matches


async def _upsert_transaction(db, account: Account, raw: dict[str, Any]) -> bool:
    external_id = transaction_identifier(raw)
    existing = await db.scalar(
        select(Transaction).where(
            Transaction.account_id == account.id,
            Transaction.external_id == external_id,
        )
    )
    amount, currency = _transaction_amount(raw)
    matches = await _matching_card_transactions(
        db,
        account.id,
        raw,
        amount,
        currency,
        exclude_id=existing.id if existing is not None else None,
    )
    if existing is not None and _is_known_provider_transaction(existing.raw_data or {}, raw):
        matches = []
    cross_matches = [candidate for candidate in matches if existing is None or candidate.id != existing.id]
    compatible_group = len(cross_matches) > 1 and all(
        can_merge_card_transactions(first.raw_data or {}, second.raw_data or {})
        or _can_merge_card_transactions(
            first.raw_data or {}, second.raw_data or {}, compact_merchant=True
        )
        for index, first in enumerate(cross_matches)
        for second in cross_matches[index + 1 :]
    )
    if len(cross_matches) > 1 and not compatible_group:
        cross_matches = []
    logical_rows = ([existing] if existing is not None else []) + cross_matches
    logical_rows = [row for row in logical_rows if row is not None]
    booking_date = dates.parse_datetime(raw.get("booking_date") or raw.get("transaction_date"))
    value_date = dates.parse_datetime(raw.get("value_date"))

    if logical_rows:
        survivor = min(logical_rows, key=lambda row: row.id)
        incoming_status = _status(raw)
        booked_row = next(
            (row for row in logical_rows if _status(row.raw_data or {}) in _BOOKED_STATUSES),
            None,
        )
        keep_booked_state = incoming_status == "PDNG" and booked_row is not None
        if keep_booked_state:
            authoritative_external_id = booked_row.external_id
            authoritative_amount = booked_row.amount
            authoritative_currency = booked_row.currency
            authoritative_direction = booked_row.direction
            authoritative_raw = booked_row.raw_data or {}
            authoritative_description = booked_row.description or transaction_description(authoritative_raw)
        else:
            authoritative_external_id = external_id
            authoritative_amount = amount
            authoritative_currency = currency
            authoritative_direction = raw.get("credit_debit_indicator")
            authoritative_raw = raw
            authoritative_description = transaction_description(raw)

        deleted_duplicate = False
        for duplicate in logical_rows:
            if duplicate.id != survivor.id:
                await _merge_duplicate_annotations(db, account, survivor, duplicate)
                deleted_duplicate = True
        if deleted_duplicate:
            await db.flush()

        repeated_booked = False
        if cross_matches:
            repeated_booked = any(
                _is_repeated_booked_transaction(row.raw_data or {}, raw)
                for row in cross_matches
            ) or any(
                _is_repeated_booked_transaction(row.raw_data or {}, other.raw_data or {})
                for index, row in enumerate(logical_rows)
                for other in logical_rows[index + 1 :]
            )
            observed_dates = [
                date
                for date in (
                    *(row.booking_date for row in logical_rows),
                    *(row.value_date for row in logical_rows),
                    *(
                        dates.parse_datetime((row.raw_data or {}).get("transaction_date"))
                        for row in logical_rows
                        if repeated_booked
                    ),
                    booking_date,
                    value_date,
                    dates.parse_datetime(raw.get("transaction_date")) if repeated_booked else None,
                )
                if date is not None
            ]
            if observed_dates:
                first_date = min(observed_dates)
                survivor.booking_date = first_date
                survivor.value_date = first_date
        else:
            if survivor.booking_date is None:
                survivor.booking_date = survivor.value_date or booking_date or value_date
            if survivor.value_date is None:
                survivor.value_date = value_date or booking_date or survivor.booking_date

        survivor.external_id = authoritative_external_id
        survivor.amount = authoritative_amount
        survivor.currency = authoritative_currency
        survivor.direction = authoritative_direction
        survivor.description = authoritative_description
        merged_raw = _raw_data_with_first_dates(survivor, authoritative_raw)
        if cross_matches and survivor.booking_date is not None:
            merged_raw["booking_date"] = survivor.booking_date.date().isoformat()
        if repeated_booked:
            provider_booking_dates = sorted({
                provider_date.date().isoformat()
                for source in (*(row.raw_data or {} for row in logical_rows), raw)
                for provider_date in _provider_booking_dates(
                    source, dates.parse_datetime(source.get("transaction_date"))
                )
            })
            merged_raw["_provider_booking_dates"] = provider_booking_dates
            merged_raw["_provider_transaction_ids"] = sorted({
                provider_id
                for source in (*(row.raw_data or {} for row in logical_rows), raw)
                for provider_id in _provider_transaction_ids(source)
            })
        survivor.raw_data = merged_raw
        return False

    first_date = booking_date or value_date
    db.add(
        Transaction(
            account_id=account.id,
            external_id=external_id,
            booking_date=first_date,
            value_date=value_date or first_date,
            amount=amount,
            currency=currency,
            direction=raw.get("credit_debit_indicator"),
            description=transaction_description(raw),
            raw_data=raw,
        )
    )
    return True


async def fetch_transactions(account: Account, owner_id: str, date_from: str | None = None) -> int:
    if account.raw_data.get("test_data"):
        return 0
    headers = await bank_client.bank_api_headers(owner_id)
    params: dict[str, str] = {"strategy": "longest"}
    if date_from:
        params["date_from"] = date_from
    imported = 0
    async with aclosing(bank_client.transaction_pages(account.bank_account_id, params, headers)) as pages:
        async for payload in pages:
            async with database.tenant_session(owner_id) as db:
                for raw in payload.get("transactions", []):
                    imported += int(await _upsert_transaction(db, account, raw))
                await db.commit()
    return imported


async def sync_account_transactions(account_ids: list[int], owner_id: str) -> None:
    async with database.tenant_session(owner_id) as db:
        accounts = list(
            (
                await db.scalars(
                    select(Account)
                    .join(BankSession)
                    .where(BankSession.owner_id == owner_id, Account.id.in_(account_ids))
                )
            ).all()
        )
    for account in accounts:
        await fetch_transactions(account, owner_id)
    async with database.tenant_session(owner_id) as db:
        from app.services import category_rules
        if await category_rules.apply_category_rules(db, owner_id):
            await db.commit()
    await sync_state.record_auto_sync_result(owner_id, error=None)


async def sync_owner_transactions(owner_id: str) -> dict[str, int]:
    async with database.tenant_session(owner_id) as db:
        accounts = list(
            (
                await db.scalars(
                    select(Account)
                    .join(BankSession)
                    .where(BankSession.owner_id == owner_id, Account.monitored.is_(True))
                )
            ).all()
        )
    imported = 0
    for account in accounts:
        imported += await fetch_transactions(account, owner_id)
    async with database.tenant_session(owner_id) as db:
        from app.services import category_rules
        if await category_rules.apply_category_rules(db, owner_id):
            await db.commit()
    return {"accounts": len(accounts), "transactions_imported": imported}
