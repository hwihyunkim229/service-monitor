import unittest
from datetime import datetime
from types import SimpleNamespace

from app.services.device_lineage import build_lineages


def case(case_id, faulty, replacement, day):
    return SimpleNamespace(
        id=case_id,
        serial_no=faulty,
        replacement_serial_no=replacement,
        received_at=datetime(2025, 1, day),
        date_only=None,
    )


class DeviceLineageTests(unittest.TestCase):
    def test_middle_serial_is_its_own_denominator(self):
        cases = [case(1, "A-1", "B-1", 1), case(2, "B-1", "C-1", 2)]
        result = build_lineages(cases, {"B-1"})
        self.assertEqual(result[0]["root_serial"], "B-1")
        self.assertEqual(result[0]["serials"], {"B-1", "C-1"})

    def test_branch_is_reported(self):
        cases = [case(1, "A-1", "B-1", 1), case(2, "A-1", "C-1", 2)]
        result = build_lineages(cases, {"A-1"})
        self.assertTrue(result[0]["has_branch"])

    def test_shared_child_does_not_merge_unrelated_parents(self):
        cases = [
            case(1, "A-1", "B-1", 1),
            case(2, "B-1", "C-1", 2),
            case(3, "D-1", "C-1", 3),
        ]
        result = build_lineages(cases, {"A-1", "B-1", "D-1"})
        self.assertEqual([item["root_serial"] for item in result], ["A-1", "D-1"])
        self.assertEqual(result[0]["serials"], {"A-1", "B-1", "C-1"})
        self.assertEqual(result[1]["serials"], {"D-1", "C-1"})

    def test_duplicate_relationship_groups_history(self):
        cases = [case(1, "A-1", "B-1", 1), case(2, "A-1", "B-1", 2)]
        result = build_lineages(cases, {"A-1"})
        self.assertEqual(len(result[0]["steps"]), 1)
        self.assertEqual(len(result[0]["steps"][0]["cases"]), 2)


if __name__ == "__main__":
    unittest.main()