import csv
import inspect
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from predict_lakes.anomaly_persistence import forecast_anomaly_persistence


class AnomalyPersistenceTests(unittest.TestCase):
    fields = [
        "lake_id", "basin", "observation_time", "data_available_time", "variable", "depth_m",
        "value", "unit", "source_dataset", "source_file", "source_row", "qc_status",
    ]

    def _state(self):
        return {
            "site": {"lake_id": "windermere", "basin": "south"},
            "target": {"variable": "water_temperature", "depth_m": 2.0, "unit": "degC", "resolution": "daily", "calendar": "UTC"},
            "last_complete_daily_state_date": "2015-07-14",
            "forecast_issue_time": "2015-07-15T00:00:00+00:00",
            "availability_mode": "observation_time_proxy",
            "availability_assumption": "historical availability unknown; observation-time proxy",
        }

    def _daily_rows(self, start, days, value, *, skip=None):
        skip = set(skip or [])
        rows = []
        for day in range(days):
            if day in skip:
                continue
            rows.extend({
                "observation_time": (start + timedelta(days=day, hours=hour)).isoformat(), "value": value,
            } for hour in range(18))
        return rows

    def _rows(self, *, historical=(10.0, 12.0, 14.0), current=20.0, future=999.0, later=9999.0, current_skip=None):
        rows = []
        for year, value in zip((2010, 2011, 2012), historical):
            rows.extend(self._daily_rows(datetime(year, 6, 15, 0, 59, tzinfo=timezone.utc), 120, value))
        rows.extend(self._daily_rows(datetime(2015, 6, 15, 0, 59, tzinfo=timezone.utc), 30, current, skip=current_skip))
        rows.extend(self._daily_rows(datetime(2015, 7, 15, 0, 59, tzinfo=timezone.utc), 90, future))
        rows.extend(self._daily_rows(datetime(2016, 6, 15, 0, 59, tzinfo=timezone.utc), 120, later))
        return rows

    def _canonical(self, directory, rows):
        path = Path(directory) / "canonical.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    "lake_id": "windermere", "basin": "south", "data_available_time": "",
                    "variable": "water_temperature", "depth_m": "2.0", "unit": "degC",
                    "source_dataset": "synthetic", "source_file": "synthetic.csv", "source_row": "1",
                    "qc_status": "", **row,
                })
        return path

    def test_definition_and_same_full_anomaly_for_all_horizons(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._canonical(directory, self._rows())
            result = forecast_anomaly_persistence([path], self._state())
            anomaly = result["current_state_anomaly"]
            self.assertEqual((anomaly["window_start"], anomaly["window_end"]), ("2015-06-15", "2015-07-14"))
            self.assertEqual(anomaly["valid_days"], 30)
            self.assertEqual(anomaly["recent_30_day_mean"], 20.0)
            self.assertEqual(anomaly["historical_recent_window_climatology"], 12.0)
            self.assertEqual(anomaly["current_anomaly"], 8.0)
            additions = [row["predicted_mean_temperature"] - row["target_climatology_mean"] for row in result["forecasts"]]
            self.assertEqual(additions, [8.0, 8.0, 8.0])

    def test_extreme_issue_year_future_and_later_years_cannot_change_forecast(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._canonical(directory, self._rows(future=999.0, later=9999.0))
            first = forecast_anomaly_persistence([path], self._state())
            path = self._canonical(directory, self._rows(future=-999.0, later=-9999.0))
            second = forecast_anomaly_persistence([path], self._state())
            self.assertEqual(first, second)

    def test_legal_recent_change_alters_anomaly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._canonical(directory, self._rows(current=20.0))
            first = forecast_anomaly_persistence([path], self._state())
            rows = self._rows(current=20.0)
            for row in rows:
                if row["observation_time"].startswith("2015-07-14"):
                    row["value"] = 50.0
            path = self._canonical(directory, rows)
            second = forecast_anomaly_persistence([path], self._state())
            self.assertNotEqual(first["current_state_anomaly"]["current_anomaly"], second["current_state_anomaly"]["current_anomaly"])

    def test_prior_year_change_alters_reference_climatology(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._canonical(directory, self._rows(historical=(10.0, 12.0, 14.0)))
            first = forecast_anomaly_persistence([path], self._state())
            path = self._canonical(directory, self._rows(historical=(10.0, 30.0, 14.0)))
            second = forecast_anomaly_persistence([path], self._state())
            self.assertNotEqual(first["current_state_anomaly"]["historical_recent_window_climatology"], second["current_state_anomaly"]["historical_recent_window_climatology"])

    def test_recent_window_requires_27_of_30_days(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._canonical(directory, self._rows(current_skip=(0, 1, 2)))
            valid = forecast_anomaly_persistence([path], self._state())
            self.assertTrue(valid["current_state_anomaly"]["is_valid"])
            self.assertEqual(valid["current_state_anomaly"]["valid_days"], 27)
            path = self._canonical(directory, self._rows(current_skip=(0, 1, 2, 3)))
            invalid = forecast_anomaly_persistence([path], self._state())
            self.assertFalse(invalid["current_state_anomaly"]["is_valid"])
            self.assertTrue(all(row["predicted_mean_temperature"] is None for row in invalid["forecasts"]))

    def test_three_historical_years_required(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = self._rows()
            rows = [row for row in rows if not row["observation_time"].startswith("2012-")]
            path = self._canonical(directory, rows)
            result = forecast_anomaly_persistence([path], self._state())
            self.assertFalse(result["current_state_anomaly"]["is_valid"])
            self.assertEqual(result["current_state_anomaly"]["n_valid_historical_years"], 2)

    def test_state_date_must_be_complete_in_legal_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._canonical(directory, self._rows(current_skip=(29,)))
            with self.assertRaises(ValueError):
                forecast_anomaly_persistence([path], self._state())

    def test_deterministic_and_has_no_verification_target_input(self):
        self.assertNotIn("forecast_targets", inspect.signature(forecast_anomaly_persistence).parameters)
        with tempfile.TemporaryDirectory() as directory:
            path = self._canonical(directory, self._rows())
            first = forecast_anomaly_persistence([path], self._state())
            second = forecast_anomaly_persistence([path], self._state())
            self.assertEqual(first, second)
            self.assertFalse(first["forecast_target_input_used"])
            self.assertTrue(all("observed_mean_temperature" not in row for row in first["forecasts"]))


if __name__ == "__main__":
    unittest.main()
