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
from predict_lakes.forecast_state import build_forecast_state
from predict_lakes.forecast_targets import build_forecast_targets


class ObservationTests(unittest.TestCase):
    def _state(self, state_date, issue_time):
        return {"site": {"lake_id": "windermere", "basin": "south"}, "target": {"variable": "water_temperature", "depth_m": 2.0, "unit": "degC", "resolution": "daily", "calendar": "UTC"}, "last_complete_daily_state_date": state_date, "forecast_issue_time": issue_time, "availability_mode": "observation_time_proxy"}

    def _canonical(self, directory, rows):
        path = Path(directory) / "canonical.csv"
        fields = ["lake_id", "basin", "observation_time", "data_available_time", "variable", "depth_m", "value", "unit", "source_dataset", "source_file", "source_row", "qc_status"]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({"lake_id": "windermere", "basin": "south", "data_available_time": "", "variable": "water_temperature", "depth_m": "2.0", "unit": "degC", "source_dataset": "synthetic", "source_file": "synthetic.csv", "source_row": "3", "qc_status": "", **row})
        return path

    def test_forecast_state_excludes_extreme_future_values(self):
        from datetime import datetime, timedelta, timezone
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            rows = [{"observation_time": (start + timedelta(hours=i)).isoformat(), "value": 5.0 if i < 18 else 999.0} for i in range(24)]
            path = self._canonical(directory, rows)
            state, history = build_forecast_state([path], "2020-01-01T17:59:00+00:00")
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["temperature_2m"], 5.0)
            self.assertEqual(state["last_legally_available_raw_observation_time"], "2020-01-01T17:00:00+00:00")

    def test_earlier_issue_removes_later_daily_state(self):
        from datetime import datetime, timedelta, timezone
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            rows = [{"observation_time": (start + timedelta(hours=i)).isoformat(), "value": float(i)} for i in range(48)]
            path = self._canonical(directory, rows)
            late, _ = build_forecast_state([path], "2020-01-02T23:00:00+00:00")
            early, _ = build_forecast_state([path], "2020-01-01T23:00:00+00:00")
            self.assertEqual(late["n_valid_historical_daily_observations"], 2)
            self.assertEqual(early["n_valid_historical_daily_observations"], 1)

    def test_intraday_cutoff_requires_18_legal_hours(self):
        from datetime import datetime, timedelta, timezone
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            rows = [{"observation_time": (start + timedelta(hours=i)).isoformat(), "value": 1.0} for i in range(24)]
            path = self._canonical(directory, rows)
            state, history = build_forecast_state([path], "2020-01-01T16:59:00+00:00")
            self.assertEqual(history, [])
            self.assertIsNone(state["last_complete_daily_state_date"])

    def test_proxy_unknown_availability_and_repeated_runs_are_explicit_and_deterministic(self):
        from datetime import datetime, timedelta, timezone
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            rows = [{"observation_time": (start + timedelta(hours=i)).isoformat(), "value": 2.0} for i in range(18)]
            path = self._canonical(directory, rows)
            first = build_forecast_state([path], "2020-01-01T18:00:00+00:00")
            second = build_forecast_state([path], "2020-01-01T18:00:00+00:00")
            self.assertEqual(first, second)
            self.assertEqual(first[0]["availability_mode"], "observation_time_proxy")
            self.assertIn("unknown", first[0]["availability_assumption"])
            with self.assertRaises(ValueError):
                build_forecast_state([path], "2020-01-01T18:00:00+00:00", availability_mode="data_available_time")

    def test_naive_issue_time_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._canonical(directory, [])
            with self.assertRaises(ValueError):
                build_forecast_state([path], "2020-01-01T00:00:00")

    def test_minute_59_timestamp_is_not_rounded(self):
        from datetime import datetime, timedelta, timezone
        start = datetime(2020, 1, 1, 0, 59, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            rows = [{"observation_time": (start + timedelta(hours=i)).isoformat(), "value": 3.0} for i in range(18)]
            path = self._canonical(directory, rows)
            state, history = build_forecast_state([path], "2020-01-01T18:00:00+00:00")
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["first_legal_observation_time"], "2020-01-01T00:59:00+00:00")

    def test_target_windows_anchor_after_state_date_and_do_not_overlap(self):
        from datetime import datetime, timedelta, timezone
        start = datetime(2020, 7, 15, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            rows = []
            for day in range(90):
                value = 1.0 + day // 30
                rows.extend({"observation_time": (start + timedelta(days=day, hours=hour)).isoformat(), "value": value} for hour in range(18))
            path = self._canonical(directory, rows)
            state = self._state("2020-07-14", "2020-07-15T12:00:00+00:00")
            result = build_forecast_targets([path], state)
            windows = result["targets"]
            self.assertEqual((windows[0]["nominal_start_date"], windows[0]["nominal_end_date"]), ("2020-07-15", "2020-08-13"))
            self.assertEqual((windows[1]["nominal_start_date"], windows[1]["nominal_end_date"]), ("2020-08-14", "2020-09-12"))
            self.assertEqual((windows[2]["nominal_start_date"], windows[2]["nominal_end_date"]), ("2020-09-13", "2020-10-12"))
            self.assertEqual([window["observed_mean_temperature"] for window in windows], [1.0, 2.0, 3.0])

    def test_target_window_requires_27_daily_means_without_imputation(self):
        from datetime import datetime, timedelta, timezone
        start = datetime(2020, 1, 2, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            rows = []
            for day in range(27):
                rows.extend({"observation_time": (start + timedelta(days=day, hours=hour)).isoformat(), "value": 7.0} for hour in range(18))
            path = self._canonical(directory, rows)
            state = self._state("2020-01-01", "2020-01-02T12:00:00+00:00")
            target = build_forecast_targets([path], state)["targets"][0]
            self.assertTrue(target["is_valid"])
            self.assertEqual(target["valid_days"], 27)
            self.assertEqual(target["completeness_fraction"], 0.9)
            self.assertEqual(target["observed_mean_temperature"], 7.0)

    def test_future_changes_affect_targets_but_not_forecast_state(self):
        from datetime import datetime, timedelta, timezone
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            def rows(future_value):
                result = [{"observation_time": (start + timedelta(hours=hour)).isoformat(), "value": 5.0} for hour in range(18)]
                for day in range(1, 91):
                    result.extend({"observation_time": (start + timedelta(days=day, hours=hour)).isoformat(), "value": future_value} for hour in range(18))
                return result
            path = self._canonical(directory, rows(10.0))
            state_a, history_a = build_forecast_state([path], "2020-01-01T23:00:00+00:00")
            targets_a = build_forecast_targets([path], state_a)
            self._canonical(directory, rows(100.0))
            state_b, history_b = build_forecast_state([path], "2020-01-01T23:00:00+00:00")
            targets_b = build_forecast_targets([path], state_b)
            self.assertEqual((state_a, history_a), (state_b, history_b))
            self.assertNotEqual(targets_a["targets"][0]["observed_mean_temperature"], targets_b["targets"][0]["observed_mean_temperature"])
            self.assertTrue(targets_a["not_for_forecast_model_input"])
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
