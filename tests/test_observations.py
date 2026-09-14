import csv
import json
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from predict_lakes.ingest import ingest_observations
from predict_lakes.standardise import read_observations, write_canonical
from predict_lakes.target_audit import aggregate_daily, audit_target


class ObservationTests(unittest.TestCase):
    def test_daily_aggregation_requires_explicit_completeness(self):
        from datetime import datetime, timedelta, timezone
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        series = {1.0: {start + timedelta(hours=i): 1.0 for i in range(18)}, 2.0: {start + timedelta(hours=i): 2.0 for i in range(17)}}
        self.assertEqual(aggregate_daily(series, minimum_hours=18), [])
        series[2.0][start + timedelta(hours=17)] = 2.0
        daily = aggregate_daily(series, minimum_hours=18)
        self.assertEqual(len(daily), 1)
        self.assertEqual(daily[0]["difference_1m_minus_2m"], -1.0)

    def test_target_audit_reports_alignment_and_metrics(self):
        from datetime import datetime, timedelta, timezone
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        series = {1.0: {}, 2.0: {}}
        for i in range(24):
            timestamp = start + timedelta(hours=i)
            series[1.0][timestamp] = 10.0
            series[2.0][timestamp] = 9.0
        result = audit_target(series, expected_start=start, expected_end=start + timedelta(hours=23))
        self.assertEqual(result["summary"]["hourly"]["matched_source_timestamps"], 24)
        self.assertEqual(result["summary"]["comparison"]["bias_1m_minus_2m"], 1.0)

    def test_standardise_two_header_csv_preserves_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.csv"
            source.write_text("Date GMT,Water temperature,Air Temperature\ndepth,1m,\n01/01/2016 01:00,7.5,2.0\n01/01/2016 02:00,,2.1\n", encoding="utf-8")
            records, inventory = read_observations(source, "test-dataset")
            self.assertEqual(len(records), 3)
            self.assertEqual(records[0]["observation_time"], "2016-01-01T01:00:00+00:00")
            self.assertEqual(records[0]["depth_m"], "1.0")
            self.assertEqual(records[0]["source_row"], "3")
            self.assertEqual(inventory["missing_value_counts"]["Water temperature:1m"], 1)
            self.assertIsNone(inventory["data_available_time"])

    def test_ingest_refuses_overwrite_and_writes_checksum_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.zip"
            source.write_bytes(b"raw bytes")
            record = ingest_observations(source, "test", root / "raw", root / "manifest.json", retrieval_date="2026-01-01")
            self.assertEqual(record["filename"], "source.zip")
            self.assertTrue((root / "raw" / "source.zip").exists())
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["records"][0]["sha256"], record["sha256"])
            with self.assertRaises(FileExistsError):
                ingest_observations(source, "test", root / "raw", root / "manifest2.json")

    def test_canonical_output_is_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "canonical.csv"
            write_canonical([{"lake_id": "windermere", "basin": "south", "observation_time": "x", "data_available_time": "", "variable": "water_temperature", "depth_m": "1.0", "value": "7.5", "unit": "degC", "source_dataset": "x", "source_file": "x.csv", "source_row": "3", "qc_status": ""}], target)
            with target.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["variable"], "water_temperature")


if __name__ == "__main__":
    unittest.main()
