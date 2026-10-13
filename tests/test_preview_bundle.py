import csv
import hashlib
import json
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path

from src.target_duty.build import COLUMNS, build
from src.target_duty.preview_bundle import make_bundle


class PreviewBundleTests(unittest.TestCase):
    def setup_data(self, folder):
        csv_file = folder / "filtered.csv"
        row = {field: "" for field in COLUMNS}
        row.update({
            "measure__type__id": "103",
            "measure__geographical_area__id": "1011",
            "measure__effective_start_date": "2025-01-01",
            "measure__duty_expression": "10% + 77 GBP / 100 kg"
        })
        with csv_file.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=COLUMNS)
            writer.writeheader()
            for i in range(120):
                writer.writerow({**row, "commodity__code": f"{i:010d}"})
        db_file = folder / "target-duty.db"
        build(str(csv_file), str(db_file), version="v4.0.1625",
              as_of=date(2026, 10, 13), min_records=100)
        return db_file

    def test_preview_contains_typed_manifest_and_real_db(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            db = self.setup_data(folder)
            preview = folder / "target-duty-preview.zip"
            manifest = make_bundle(str(db), str(db.with_suffix(".metadata.json")), str(preview))
            self.assertFalse(manifest["production_eligible"])
            self.assertFalse(manifest["signed"])
            self.assertEqual(manifest["quality"]["record_count"], 120)
            self.assertEqual(manifest["quality"]["ni_unverified"], 120)
            with zipfile.ZipFile(preview) as z:
                self.assertEqual(set(z.namelist()), {"manifest.json", "target-duty.db"})
                self.assertEqual(hashlib.sha256(z.read("target-duty.db")).hexdigest(),
                                 json.loads(z.read("manifest.json"))["artifact"]["sha256"])

    def test_tampered_database_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            db = self.setup_data(folder)
            with db.open("ab") as handle:
                handle.write(b"corrupt")
            with self.assertRaisesRegex(ValueError, "metadata does not match"):
                make_bundle(str(db), str(db.with_suffix(".metadata.json")), str(folder/"a.zip"))

if __name__ == "__main__":
    unittest.main()
