import inspect
import math
import unittest
from datetime import date, timedelta

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from predict_lakes.anomaly_relaxation import (
    fit_constrained_betas,
    forecast_anomaly_relaxation,
    relaxation_rows_from_daily,
)


class AnomalyRelaxationTests(unittest.TestCase):
    def _cases(self):
        rows = []
        for year, value in ((2010, 1.0), (2011, 2.0), (2012, 3.0)):
            for target, multiplier in (("month_1", 0.5), ("month_2", -1.0), ("month_3", 2.0)):
                rows.append({
                    "training_year": year, "target_name": target,
                    "current_anomaly": value, "future_target_anomaly": multiplier * value,
                })
        return rows

    def _daily(self):
        anomalies = {2008: -0.8, 2009: 0.5, 2010: -0.3, 2011: 1.0, 2012: -1.2, 2013: 1.6, 2014: 0.4, 2015: 1.2, 2016: 9.0, 2017: -9.0}
        daily = {}
        for year, anomaly in anomalies.items():
            day = date(year, 1, 1)
            while day.year == year:
                seasonal = 11.0 + 7.0 * math.sin(2 * math.pi * (day.timetuple().tm_yday - 105) / 365.25)
                daily[day] = seasonal + anomaly
                day += timedelta(days=1)
        return daily

    def test_zero_intercept_fit_and_constraints(self):
        fitted = fit_constrained_betas(self._cases(), 2013)
        self.assertAlmostEqual(fitted["month_1"]["raw_beta"], 0.5)
        self.assertAlmostEqual(fitted["month_1"]["constrained_beta"], 0.5)
        self.assertAlmostEqual(fitted["month_2"]["raw_beta"], -1.0)
        self.assertEqual(fitted["month_2"]["constrained_beta"], 0.0)
        self.assertAlmostEqual(fitted["month_3"]["raw_beta"], 2.0)
        self.assertEqual(fitted["month_3"]["constrained_beta"], 1.0)

    def test_issue_year_and_later_cases_cannot_change_beta(self):
        cases = self._cases()
        baseline = fit_constrained_betas(cases, 2013)
        future = cases + [
            {"training_year": year, "target_name": target, "current_anomaly": 999.0, "future_target_anomaly": -9999.0}
            for year in (2013, 2014) for target in ("month_1", "month_2", "month_3")
        ]
        self.assertEqual(baseline, fit_constrained_betas(future, 2013))
        self.assertTrue(all(year < 2013 for result in baseline.values() for year in result["training_years"]))

    def test_prior_training_change_can_change_beta(self):
        cases = self._cases()
        baseline = fit_constrained_betas(cases, 2013)
        changed = [dict(row) for row in cases]
        changed[0]["future_target_anomaly"] = 10.0
        revised = fit_constrained_betas(changed, 2013)
        self.assertNotEqual(baseline["month_1"]["raw_beta"], revised["month_1"]["raw_beta"])

    def test_minimum_distinct_years_not_raw_rows_is_enforced(self):
        repeated = self._cases() * 100
        fitted = fit_constrained_betas(repeated, 2013, minimum_training_years=4)
        self.assertTrue(all(result["n_training_rows"] == 300 for result in fitted.values()))
        self.assertTrue(all(result["n_distinct_training_years"] == 3 for result in fitted.values()))
        self.assertTrue(all(not result["is_valid"] for result in fitted.values()))

    def test_future_daily_values_and_later_years_do_not_change_historical_forecast(self):
        daily = self._daily()
        legal_slice = {day: value for day, value in daily.items() if day < date(2015, 7, 15)}
        first = relaxation_rows_from_daily(legal_slice, date(2015, 7, 14), 2015)
        changed = dict(legal_slice)
        changed.update({day: 9999.0 for day in daily if day >= date(2015, 7, 15)})
        second = relaxation_rows_from_daily(changed, date(2015, 7, 14), 2015)
        self.assertEqual(first, second)

    def test_legally_available_recent_observation_changes_current_anomaly(self):
        daily = self._daily()
        state_date = date(2015, 7, 14)
        baseline, _, _ = relaxation_rows_from_daily(daily, state_date, 2015)
        changed = dict(daily)
        changed[state_date] += 30.0
        revised, _, _ = relaxation_rows_from_daily(changed, state_date, 2015)
        self.assertNotEqual(baseline["current_anomaly"], revised["current_anomaly"])

    def test_relaxation_is_deterministic_and_requires_no_target_artifact(self):
        self.assertNotIn("forecast_targets", inspect.signature(forecast_anomaly_relaxation).parameters)
        daily = self._daily()
        first = relaxation_rows_from_daily(daily, date(2015, 7, 14), 2015)
        second = relaxation_rows_from_daily(daily, date(2015, 7, 14), 2015)
        self.assertEqual(first, second)
        _, coefficients, forecasts = first
        self.assertTrue(all(year < 2015 for result in coefficients.values() for year in result["training_years"]))
        self.assertTrue(all("observed_mean_temperature" not in row for row in forecasts))


if __name__ == "__main__":
    unittest.main()
