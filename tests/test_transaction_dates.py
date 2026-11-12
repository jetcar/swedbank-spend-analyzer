from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from app.dates import transaction_date_details


def transaction(raw_data, booking_date=None, value_date=None):
    return SimpleNamespace(
        raw_data=raw_data,
        booking_date=booking_date,
        value_date=value_date,
    )


class TransactionDateTest(unittest.TestCase):
    def test_prefers_the_bank_booking_date(self):
        details = transaction_date_details(transaction({
            "booking_date": "2026-11-06",
            "transaction_date": "2026-11-05",
            "value_date": "2026-11-04",
        }))

        self.assertEqual(details["date"], datetime(2026, 11, 6, tzinfo=timezone.utc))
        self.assertEqual(details["date_kind"], "booking")

    def test_falls_back_to_transaction_then_value_date(self):
        transaction_details = transaction_date_details(transaction({
            "booking_date": None,
            "transaction_date": "2026-11-05",
            "value_date": "2026-11-04",
        }))
        value_details = transaction_date_details(transaction(
            {"booking_date": None, "transaction_date": None},
            value_date=datetime(2026, 11, 4, tzinfo=timezone.utc),
        ))

        self.assertEqual(transaction_details["date_kind"], "transaction")
        self.assertEqual(value_details["date_kind"], "value")
        self.assertEqual(value_details["date"], datetime(2026, 11, 4, tzinfo=timezone.utc))

    def test_keeps_the_stored_date_for_legacy_rows_without_raw_date_fields(self):
        stored = datetime(2026, 11, 3, tzinfo=timezone.utc)

        details = transaction_date_details(transaction({}, booking_date=stored))

        self.assertEqual(details["date"], stored)
        self.assertEqual(details["date_kind"], "booking")
if __name__ == "__main__":
    unittest.main()
