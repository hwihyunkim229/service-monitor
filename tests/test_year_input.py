import unittest
from datetime import datetime
from app.routers.renewal import parse_dt


class SelectedYearDateParsingTests(unittest.TestCase):
    def test_month_day_uses_selected_year(self):
        self.assertEqual(parse_dt("8/20", 2024), datetime(2024, 8, 20))

    def test_month_day_time_uses_selected_year(self):
        self.assertEqual(
            parse_dt("8/20 09:30", 2027),
            datetime(2027, 8, 20, 9, 30),
        )

    def test_explicit_year_takes_precedence(self):
        self.assertEqual(
            parse_dt("2025-08-20", 2024),
            datetime(2025, 8, 20),
        )


if __name__ == "__main__":
    unittest.main()