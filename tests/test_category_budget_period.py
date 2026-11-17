from datetime import date, datetime, timezone
from decimal import Decimal
import unittest

from app import dates
from app.services.categories import effective_available_budget, effective_monthly_budget


class CategoryBudgetPeriodTest(unittest.TestCase):
    def test_monthly_amount_is_unchanged(self):
        self.assertEqual(effective_monthly_budget(Decimal("125.55"), "month", "2026-11"), Decimal("125.55"))

    def test_daily_amount_uses_calendar_days(self):
        self.assertEqual(effective_monthly_budget(Decimal("10"), "day", "2026-02"), Decimal("280.00"))
        self.assertEqual(effective_monthly_budget(Decimal("10"), "day", "2028-02"), Decimal("290.00"))
        self.assertEqual(effective_monthly_budget(Decimal("10"), "day", "2026-04"), Decimal("300.00"))
        self.assertEqual(effective_monthly_budget(Decimal("10"), "day", "2026-01"), Decimal("310.00"))

    def test_large_daily_amount_keeps_exact_cents(self):
        self.assertEqual(
            effective_monthly_budget(Decimal("9999999999999.99"), "day", "2026-01"),
            Decimal("309999999999999.69"),
        )

    def test_weekly_amount_is_prorated_and_rounded_to_cents(self):
        self.assertEqual(effective_monthly_budget(Decimal("100"), "week", "2026-11"), Decimal("428.57"))
        self.assertEqual(effective_monthly_budget(Decimal("100"), "week", "2026-12"), Decimal("442.86"))

    def test_negative_income_target_keeps_its_sign(self):
        self.assertEqual(effective_monthly_budget(Decimal("-50"), "week", "2026-11"), Decimal("-214.29"))

    def test_daily_and_weekly_amounts_accrue_through_today(self):
        today = date(2026, 11, 17)
        self.assertEqual(effective_available_budget(Decimal("10"), "day", "2026-11", today=today), Decimal("170.00"))
        self.assertEqual(effective_available_budget(Decimal("70"), "week", "2026-11", today=today), Decimal("170.00"))

    def test_accrual_boundaries_preserve_monthly_behavior(self):
        today = date(2026, 11, 17)
        self.assertEqual(effective_available_budget(Decimal("10"), "day", "2026-10", today=today), Decimal("310.00"))
        self.assertEqual(effective_available_budget(Decimal("10"), "day", "2026-12", today=today), Decimal("0.00"))
        self.assertEqual(str(effective_available_budget(Decimal("-10"), "day", "2026-12", today=today)), "-0.00")
        self.assertEqual(effective_available_budget(Decimal("300"), "month", "2026-11", today=today), Decimal("300.00"))

    def test_current_day_uses_tallinn_calendar(self):
        self.assertEqual(dates.current_date(datetime(2026, 11, 16, 22, 30, tzinfo=timezone.utc)), date(2026, 11, 17))


if __name__ == "__main__":
    unittest.main()
