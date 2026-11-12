from datetime import datetime, timezone
from typing import Any
import re

from fastapi import HTTPException

from app.models import Transaction


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def transaction_date_details(transaction: Transaction) -> dict[str, Any]:
    raw = transaction.raw_data if isinstance(transaction.raw_data, dict) else {}
    booking_date = (
        parse_datetime(raw.get("booking_date"))
        if "booking_date" in raw
        else transaction.booking_date
    )
    transaction_date = parse_datetime(raw.get("transaction_date"))
    value_date = transaction.value_date or parse_datetime(raw.get("value_date"))
    for kind, value in (
        ("booking", booking_date),
        ("transaction", transaction_date),
        ("value", value_date),
    ):
        if value is not None:
            return {
                "date": value,
                "date_kind": kind,
                "booking_date": booking_date,
                "transaction_date": transaction_date,
                "value_date": value_date,
            }
    return {
        "date": None,
        "date_kind": None,
        "booking_date": None,
        "transaction_date": None,
        "value_date": None,
    }


def month_bounds(month: str) -> tuple[datetime, datetime]:
    try:
        if not re.fullmatch(r"\d{4}-\d{2}", month):
            raise ValueError
        year, month_number = (int(value) for value in month.split("-"))
        if year < 1 or not 1 <= month_number <= 12 or (year == 9999 and month_number == 12):
            raise ValueError
        start = datetime(year, month_number, 1, tzinfo=timezone.utc)
        end = datetime(
            year + (month_number == 12),
            1 if month_number == 12 else month_number + 1,
            1,
            tzinfo=timezone.utc,
        )
    except (ValueError, TypeError, OverflowError):
        raise HTTPException(400, "Invalid month; expected YYYY-MM")
    return start, end


def current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")
