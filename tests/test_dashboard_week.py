import unittest
from datetime import datetime

from app.routers.dashboard import dashboard_week


class DashboardWeekTests(unittest.TestCase):
    def test_year_end_iso_rollover_stays_in_last_calendar_week(self):
        self.assertEqual(dashboard_week(datetime(2025, 12, 28)), 52)
        self.assertEqual(dashboard_week(datetime(2025, 12, 29)), 52)
        self.assertEqual(dashboard_week(datetime(2025, 12, 31)), 52)

    def test_new_calendar_year_starts_at_week_one(self):
        self.assertEqual(dashboard_week(datetime(2026, 1, 1)), 1)
        self.assertEqual(dashboard_week(datetime(2026, 1, 4)), 1)
        self.assertEqual(dashboard_week(datetime(2026, 1, 5)), 2)


if __name__ == "__main__":
    unittest.main()