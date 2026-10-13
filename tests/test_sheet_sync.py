import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from app.routers import sheet_sync
from app.routers.sheet_sync import collect_sheet_values


class SheetSyncCollectionTests(unittest.TestCase):
    def test_sync_closes_google_http_session_when_request_fails(self):
        client = Mock()
        client.open_by_key.side_effect = RuntimeError("request failed")

        with (
            patch.object(sheet_sync.Credentials, "from_service_account_file"),
            patch.object(sheet_sync.gspread, "authorize", return_value=client),
            patch.object(sheet_sync.time, "time", return_value=1_000),
        ):
            sheet_sync.last_sync_time = 0
            result = sheet_sync.sync_return_date()

        self.assertEqual(result, 0)
        client.set_timeout.assert_called_once_with(sheet_sync.GOOGLE_API_TIMEOUT)
        client.http_client.session.close.assert_called_once_with()

    def test_collects_replacement_serial_from_column_j(self):
        rows = [[""] * 12 for _ in range(6)]
        row = [""] * 12
        row[0] = "CASE-001"
        row[9] = "REPL-123"
        row[11] = "2026-09-23"
        rows.append(row)

        dates, serials = collect_sheet_values(rows, 0, 11)

        self.assertEqual(dates["CASE-001"], datetime(2026, 9, 23))
        self.assertEqual(serials["CASE-001"], "REPL-123")

    def test_does_not_treat_legacy_j_return_date_as_serial(self):
        rows = [[""] * 10 for _ in range(6)]
        row = [""] * 10
        row[1] = "CASE-002"
        row[9] = "9/23"
        rows.append(row)

        dates, serials = collect_sheet_values(rows, 1, 9)

        self.assertIn("CASE-002", dates)
        self.assertNotIn("CASE-002", serials)

    def test_same_j_column_accepts_non_date_as_replacement_serial(self):
        rows = [[""] * 10 for _ in range(6)]
        row = [""] * 10
        row[1] = "CASE-004"
        row[9] = "REPL-789"
        rows.append(row)

        dates, serials = collect_sheet_values(rows, 1, 9)

        self.assertNotIn("CASE-004", dates)
        self.assertEqual(serials["CASE-004"], "REPL-789")

    def test_cart_bp_reads_return_date_from_j_and_replacement_serial_from_l(self):
        rows = [[""] * 12 for _ in range(6)]
        row = [""] * 12
        row[1] = "CASE-BP-001"
        row[9] = "2026-09-23"
        row[11] = "BP-REPL-123"
        rows.append(row)

        dates, serials = collect_sheet_values(rows, 1, 9, 11)

        self.assertEqual(dates["CASE-BP-001"], datetime(2026, 9, 23))
        self.assertEqual(serials["CASE-BP-001"], "BP-REPL-123")

    def test_independent_blank_replacement_column_is_collected_for_clearing(self):
        rows = [[""] * 12 for _ in range(6)]
        row = [""] * 10
        row[1] = "CASE-BP-002"
        row[9] = "2026-09-23"
        rows.append(row)

        _dates, serials = collect_sheet_values(rows, 1, 9, 11)

        self.assertIn("CASE-BP-002", serials)
        self.assertEqual(serials["CASE-BP-002"], "")

    def test_collects_serial_when_trailing_return_date_cell_is_empty(self):
        rows = [[""] * 10 for _ in range(6)]
        row = [""] * 10
        row[0] = "CASE-003"
        row[9] = "REPL-456"
        rows.append(row)

        dates, serials = collect_sheet_values(rows, 0, 11)

        self.assertNotIn("CASE-003", dates)
        self.assertEqual(serials["CASE-003"], "REPL-456")


if __name__ == "__main__":
    unittest.main()
