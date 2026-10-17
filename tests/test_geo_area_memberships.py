import hashlib
import unittest
from datetime import date

from src.target_duty.geo_area_memberships import inspect_csv, source_url


class GeoAreaMembershipTests(unittest.TestCase):
    def test_pinned_url_requires_an_immutable_version(self):
        self.assertIn("/versions/v4.0.1628/", source_url("v4.0.1628"))
        with self.assertRaises(ValueError):
            source_url("latest")

    def test_weekly_source_archives_same_version_geo_area_join_tables(self):
        from pathlib import Path
        workflow=(Path(__file__).resolve().parents[1]/".github"/"workflows"/
                  "weekly-gb-full-source.yml").read_text()
        self.assertIn('("geo-areas","geo-area-descriptions")',workflow)
        self.assertIn('f"versions/{version}/tables/{table}/data?format=csv"',workflow)
        self.assertIn("gb-weekly-geo-areas.csv",workflow)
        self.assertIn("gb-weekly-geo-area-descriptions.csv",workflow)
        self.assertIn("gb-weekly-geo-area-reference.json",workflow)
        self.assertIn("--geo-areas gb-weekly-geo-areas.csv",workflow)
        self.assertIn("--geo-area-descriptions gb-weekly-geo-area-descriptions.csv",workflow)
        self.assertIn('geo["geo_areas_source_sha256"]',workflow)
        self.assertIn('geo["geo_area_descriptions_source_sha256"]',workflow)

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

    def test_current_official_dbt_membership_header_schema_is_supported(self):
        rows = [
            "trackedmodel_ptr_id,geo_group_id,member_id,validity_start,validity_end",
            "membership-1,G001,CN,2020-01-01,",
            "membership-2,G002,US,2020-01-01,",
        ]
        payload = ("\n".join(rows) + "\n").encode()
        result=inspect_csv(payload,date(2026,10,17),min_rows=1)
        self.assertEqual(result["source_columns"],{
            "group":"geo_group_id","member":"member_id",
            "start":"validity_start","end":"validity_end"})
        self.assertEqual(result["active_china_group_ids"],["G001"])

    def test_numeric_foreign_keys_join_to_same_version_geo_area_codes(self):
        membership = (
            "trackedmodel_ptr_id,geo_group_id,member_id,validity_start,validity_end\n"
            "1,281,1916,2020-01-01,#NA\n"
            "2,10520770,10458331,2023-06-21,#NA\n"
        ).encode()
        area_lines = [
            "trackedmodel_ptr_id,sid,area_id,area_code,parent_id,validity_start,validity_end",
            "1916,1,CN,0,#NA,1984-01-01,#NA",
            "281,2,1011,1,#NA,1958-01-01,#NA",
            "10520770,3,5051,1,#NA,2023-06-21,#NA",
        ]
        for i in range(50):
            area_lines.append(f"{20000+i},{3000+i},X{i:03d},1,#NA,2020-01-01,#NA")
        areas = ("\n".join(area_lines) + "\n").encode()
        descriptions = (
            "trackedmodel_ptr_id,sid,described_geographicalarea_id,description,validity_start\n"
            "10,11,1916,China,2020-01-01\n"
            "12,13,281,Erga Omnes,2020-01-01\n"
        ).encode()
        result = inspect_csv(membership, date(2026, 10, 17), min_rows=1,
                             area_payload=areas, descriptions_payload=descriptions)
        self.assertEqual(result["active_china_group_ids"], ["1011"])
        self.assertEqual(result["unresolved_active_membership_count"], 1)
        self.assertEqual(result["geo_areas_source_sha256"], hashlib.sha256(areas).hexdigest())
        self.assertEqual(result["geo_area_descriptions_source_sha256"],
                         hashlib.sha256(descriptions).hexdigest())
        self.assertEqual(result["unresolved_active_memberships"][0]["group_code"], "5051")
        self.assertEqual(result["unresolved_active_memberships"][0]["member_code"], "")

    def test_unresolved_active_group_for_known_china_member_fails_closed(self):
        membership = (
            "trackedmodel_ptr_id,geo_group_id,member_id,validity_start,validity_end\n"
            "1,99999,1916,2020-01-01,#NA\n"
        ).encode()
        area_lines = [
            "trackedmodel_ptr_id,sid,area_id,area_code,parent_id,validity_start,validity_end",
            "1916,1,CN,0,#NA,1984-01-01,#NA",
            "281,2,1011,1,#NA,1958-01-01,#NA",
        ]
        for i in range(50):
            area_lines.append(f"{20000+i},{3000+i},X{i:03d},1,#NA,2020-01-01,#NA")
        areas = ("\n".join(area_lines) + "\n").encode()
        with self.assertRaisesRegex(ValueError, "unresolved group foreign key"):
            inspect_csv(membership, date(2026, 10, 17), min_rows=1, area_payload=areas)

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
