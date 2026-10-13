import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch
from app.routers.exchange import update_exchange_fields


class ExchangeUpdateTests(unittest.TestCase):
    def test_blank_return_date_is_saved_as_manual_deletion(self):
        case = SimpleNamespace(
            return_at=datetime(2026, 9, 23),
            return_at_manual=False,
            received_at=None,
            date_only=datetime(2026, 9, 1),
        )
        db = Mock()
        db.query.return_value.filter.return_value.first.return_value = case

        with patch("app.routers.exchange.SessionLocal", return_value=db):
            result = update_exchange_fields(
                "cart_bp", 1, {"field": "return_at", "value": ""}
            )

        self.assertIsNone(case.return_at)
        self.assertTrue(case.return_at_manual)
        self.assertEqual(result["value"], "")
        self.assertIsNone(result["lead_time"])
        self.assertEqual(result["source"], "manual")
        db.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()