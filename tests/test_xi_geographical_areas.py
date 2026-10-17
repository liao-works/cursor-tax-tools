import hashlib
import json
import unittest
from datetime import date

from src.target_duty.xi_geographical_areas import inspect_payload, source_url


DAY = date(2026, 10, 17)


def area(area_id, children=()):
    return {
        "id": area_id,
        "type": "geographical_area",
        "attributes": {"geographical_area_id": area_id, "description": area_id},
        "relationships": {
            "contained_geographical_areas": {
                "data": [{"type": "geographical_area", "id": child} for child in children]
            }
        },
    }


class XIGeographicalAreaTests(unittest.TestCase):
    def test_url_is_pinned_to_as_of_and_correct_endpoint(self):
        url = source_url(DAY)
        self.assertIn("/xi/api/geographical_areas?", url)
        self.assertIn("as_of=2026-10-17", url)
        self.assertIn("exclude_none", url)

    def test_nested_groups_containing_china_are_resolved_recursively(self):
        doc = {
            "data": [
                area("1011", ("CN", "US")),
                area("G001", ("G002",)),
                area("G002", ("CN",)),
                area("US"),
            ],
            "included": [area("CN")],
        }
        payload = json.dumps(doc).encode()
        result = inspect_payload(payload, DAY, min_areas=1)
        self.assertEqual(result["area_ids_containing_china"], ["1011", "CN", "G001", "G002"])
        self.assertEqual(result["china_applicable_group_ids"], ["G001", "G002"])
        self.assertEqual(result["erga_omnes_area_id"], "1011")
        self.assertEqual(result["area_descriptions"]["G001"], "G001")
        self.assertEqual(result["source_sha256"], hashlib.sha256(payload).hexdigest())
        self.assertTrue(result["complete"])

    def test_erga_omnes_is_required_but_not_a_cn_membership_edge(self):
        # HMRC may expose 1011 as an area without listing every country as a
        # contained-area child. Erga Omnes is handled as a legal scope fallback.
        payload=json.dumps({"data":[area("1011"),area("G001",("CN",))],
                            "included":[area("CN")]}).encode()
        result=inspect_payload(payload,DAY,min_areas=1)
        self.assertEqual(result["erga_omnes_area_id"],"1011")
        self.assertEqual(result["china_applicable_group_ids"],["G001"])
        self.assertNotIn("1011",result["china_applicable_group_ids"])

    def test_unresolved_member_and_cycles_fail_closed(self):
        missing = {"data": [area("1011", ("CN",)), area("G001", ("UNKNOWN",))],
                   "included": [area("CN")]}
        with self.assertRaisesRegex(ValueError, "Unresolved"):
            inspect_payload(json.dumps(missing).encode(), DAY, min_areas=1)
        cyclic = {"data": [area("1011", ("CN",)), area("G001", ("G002",)),
                           area("G002", ("G001",))],
                  "included": [area("CN")]}
        with self.assertRaisesRegex(ValueError, "Cycle"):
            inspect_payload(json.dumps(cyclic).encode(), DAY, min_areas=1)

    def test_pagination_and_missing_erga_omnes_fail_closed(self):
        paginated = {"data": [area("CN"), area("1011", ("CN",))],
                     "links": {"next": "next-page"}}
        with self.assertRaisesRegex(ValueError, "paginated"):
            inspect_payload(json.dumps(paginated).encode(), DAY, min_areas=1)
        missing_world = {"data": [area("CN"), area("G001", ("CN",))]}
        with self.assertRaisesRegex(ValueError, "inventory is incomplete"):
            inspect_payload(json.dumps(missing_world).encode(), DAY, min_areas=1)


if __name__ == "__main__":
    unittest.main()
