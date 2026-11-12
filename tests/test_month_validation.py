import unittest

from fastapi import HTTPException

from app.dates import month_bounds


class MonthValidationTest(unittest.TestCase):
    def test_maximum_month_is_rejected_without_datetime_overflow(self):
        with self.assertRaises(HTTPException) as context:
            month_bounds("9999-12")
        self.assertEqual(context.exception.status_code, 400)

    def test_valid_month_keeps_exclusive_upper_bound(self):
        start, end = month_bounds("2026-12")
        self.assertEqual(start.isoformat(), "2026-12-01T00:00:00+00:00")
        self.assertEqual(end.isoformat(), "2027-01-01T00:00:00+00:00")
if __name__ == "__main__":
    unittest.main()
