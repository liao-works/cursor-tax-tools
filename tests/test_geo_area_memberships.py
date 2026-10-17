import hashlib
import unittest
from datetime import date

from src.target_duty.geo_area_memberships import inspect_csv, source_url


class GeoAreaMembershipTests(unittest.TestCase):
    def test_pinned_url_requires_an_immutable_version(self):
        self.assertIn("/versions/v4.0.1628/", source_url("v4.0.1628"))
        with self.assertRaises(ValueError):
            source_url("latest")

    def test_active_china_group_memberships_respect_dates(self):
        rows = [
            "geographical_area__id,member__id,membership__effective_start_date,membership__effective_end_date",
            "1011,CN,2020-01-01,#NA",
            "G001,CN,2025-01-01,#NA",
            "G001,US,2020-01-01,#NA",
            "G002,CN,2020-01-01,2026-10-15",
            "G003,CN,2026-10-18,#NA",
        ]
        payload = ("\n".join(rows) + "\n").encode()
        result = inspect_csv(payload, date(2026, 10, 17), min_rows=1)
        self.assertEqual(result["row_count"], 5)
        self.assertEqual(result["active_china_group_ids"], ["1011", "G001"])
        self.assertEqual(result["active_china_group_count"], 2)
        self.assertEqual(result["source_sha256"], hashlib.sha256(payload).hexdigest())
        self.assertNotIn("G002", result["active_china_group_ids"])
        self.assertNotIn("G003", result["active_china_group_ids"])

    def test_no_active_china_membership_fails_closed(self):
        payload = (
            "geographical_area__id,member__id,membership__effective_start_date,membership__effective_end_date\n"
            "G001,US,2020-01-01,#NA\n"
        ).encode()
        with self.assertRaisesRegex(ValueError, "no active China groups"):
            inspect_csv(payload, date(2026, 10, 17), min_rows=1)

    def test_bad_dates_and_missing_membership_fields_fail_closed(self):
        header = "geographical_area__id,member__id,membership__effective_start_date,membership__effective_end_date"
        with self.assertRaisesRegex(ValueError, "validity date"):
            inspect_csv((header+"\nG001,CN,not-a-date,#NA\n").encode(),
                        date(2026, 10, 17), min_rows=1)
        with self.assertRaisesRegex(ValueError, "Missing group/member/start"):
            inspect_csv((header+"\nG001,CN,#NA,#NA\n").encode(),
                        date(2026, 10, 17), min_rows=1)


if __name__ == "__main__":
    unittest.main()
