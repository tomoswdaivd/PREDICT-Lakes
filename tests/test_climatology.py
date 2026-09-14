import csv
import inspect
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from predict_lakes.climatology import forecast_climatology
from predict_lakes.climatology_hindcast import weekly_issue_schedule
from predict_lakes.window_definition import equivalent_window_bounds, target_window_bounds


class ClimatologyTests(unittest.TestCase):
    fields = [
        "lake_id", "basin", "observation_time", "data_available_time", "variable", "depth_m",
        "value", "unit", "source_dataset", "source_file", "source_row", "qc_status",
    ]

    def _state(self, *, state_date="2015-07-14", issue_time="2015-07-15T00:00:00+00:00"):
        return {
            "site": {"lake_id": "windermere", "basin": "south"},
            "target": {"variable": "water_temperature", "depth_m": 2.0, "unit": "degC", "resolution": "daily", "calendar": "UTC"},
            "last_complete_daily_state_date": state_date,
            "forecast_issue_time": issue_time,
            "availability_mode": "observation_time_proxy",
            "availability_assumption": "historical data availability is unknown; observation time is a pseudo-operational proxy",
        }

    def _rows(self, values_by_year):
        rows = []
        for year, value in values_by_year.items():
            start = datetime(year, 7, 15, 0, 59, tzinfo=timezone.utc)
            for day in range(90):
                for hour in range(18):
                    rows.append({"observation_time": (start + timedelta(days=day, hours=hour)).isoformat(), "value": value})
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

    def test_exact_windows_and_leap_year_convention(self):
        self.assertEqual(target_window_bounds(date(2015, 7, 14), 1), (date(2015, 7, 15), date(2015, 8, 13)))
        self.assertEqual(equivalent_window_bounds(date(2016, 2, 29), 2012), (date(2012, 2, 29), date(2012, 3, 29)))
        self.assertIsNone(equivalent_window_bounds(date(2016, 2, 29), 2015))

    def test_weekly_schedule_uses_monday_states_and_stays_within_archive_issue_years(self):
        daily = {date(2018, 12, 24): 5.0, date(2018, 12, 30): 5.0, date(2018, 12, 31): 5.0}
        schedule = weekly_issue_schedule(daily)
        self.assertEqual(len(schedule), 1)
        self.assertEqual(schedule[0]["forecast_state_date"], date(2018, 12, 24))
        self.assertEqual(schedule[0]["forecast_issue_time"].isoformat(), "2018-12-25T00:00:00+00:00")

    def test_extreme_later_years_and_issue_year_cannot_change_forecast(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._canonical(directory, self._rows({2010: 10, 2011: 11, 2012: 12, 2015: 999, 2016: 9999}))
            first = forecast_climatology([path], self._state())
            path = self._canonical(directory, self._rows({2010: 10, 2011: 11, 2012: 12, 2015: -999, 2016: -9999}))
            second = forecast_climatology([path], self._state())
            self.assertEqual(first, second)
            self.assertEqual(first["forecasts"][0]["predicted_mean_temperature"], 11.0)

    def test_changing_prior_training_year_changes_forecast(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._canonical(directory, self._rows({2010: 10, 2011: 11, 2012: 12}))
            first = forecast_climatology([path], self._state())
            path = self._canonical(directory, self._rows({2010: 10, 2011: 20, 2012: 12}))
            second = forecast_climatology([path], self._state())
            self.assertNotEqual(first["forecasts"][0]["predicted_mean_temperature"], second["forecasts"][0]["predicted_mean_temperature"])

    def test_training_years_are_prior_and_minimum_is_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._canonical(directory, self._rows({2012: 12, 2013: 13, 2014: 14, 2015: 999}))
            valid = forecast_climatology([path], self._state())
            self.assertTrue(all(year < 2015 for row in valid["forecasts"] for year in row["training_years"]))
            self.assertTrue(all(row["is_valid"] for row in valid["forecasts"]))
            invalid = forecast_climatology([path], self._state(), minimum_training_years=4)
            self.assertTrue(all(not row["is_valid"] and row["predicted_mean_temperature"] is None for row in invalid["forecasts"]))

    def test_target_artifact_is_not_an_input_and_runs_are_deterministic(self):
        self.assertNotIn("forecast_targets", inspect.signature(forecast_climatology).parameters)
        with tempfile.TemporaryDirectory() as directory:
            path = self._canonical(directory, self._rows({2010: 10, 2011: 11, 2012: 12}))
            first = forecast_climatology([path], self._state())
            second = forecast_climatology([path], self._state())
            self.assertEqual(first, second)
            self.assertFalse(first["forecast_target_input_used"])
            self.assertEqual(first["availability_mode"], "observation_time_proxy")
            self.assertTrue(all("observed_mean_temperature" not in row for row in first["forecasts"]))


if __name__ == "__main__":
    unittest.main()
