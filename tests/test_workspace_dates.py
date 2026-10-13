import unittest
from datetime import datetime
from unittest.mock import Mock
from app.routers.workspace import display_transfer_no, import_cx_row, parse_datetime


class WorkspaceDateParsingTests(unittest.TestCase):
    def test_korean_month_day_uses_import_year(self):
        self.assertEqual(
            parse_datetime("04월 07일", default_year=2025),
            datetime(2025, 4, 7),
        )

    def test_compact_time_uses_import_year(self):
        self.assertEqual(
            parse_datetime("4/7 09:20", compact=True, default_year=2025),
            datetime(2025, 4, 7, 9, 20),
        )

    def test_common_excel_labels_are_normalized(self):
        case = Mock()
        case.serial_no = None
        query = Mock()
        query.filter.return_value.first.return_value = case
        db = Mock()
        db.query.return_value = query
        row = {
            "식별코드": "CASE-1",
            "교품 여부": "cradle",
            "RnD Team": "APP",
        }

        import_cx_row(db, "cart_bp_pro", row, default_year=2025)

        self.assertEqual(row["교품 여부"], "Cradle")
        self.assertEqual(row["RnD Team"], "App")

    def test_internal_missing_code_is_displayed_as_na(self):
        self.assertEqual(display_transfer_no("__NA__2025_000096"), "N/A")
        self.assertEqual(display_transfer_no("1558"), "1558")


if __name__ == "__main__":
    unittest.main()