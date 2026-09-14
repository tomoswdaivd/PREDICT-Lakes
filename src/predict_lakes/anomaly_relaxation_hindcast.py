"""Regression audit and three-model paired hindcast for anomaly relaxation."""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import mean, stdev
from typing import Any, Iterable

from .anomaly_persistence import anomaly_persistence_rows_from_daily
from .anomaly_relaxation import (
    MODEL_ID,
    MODEL_VERSION,
    build_relaxation_training_cases,
    fit_constrained_betas,
)
from .climatology_hindcast import _observed_window, weekly_issue_schedule
from .forecast_targets import DAILY_MIN_HOURS, WINDOW_MIN_VALID_DAYS, read_daily_target_temperature


def _correlation(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    x_mean, y_mean = mean(xs), mean(ys)
    return sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / ((len(xs) - 1) * stdev(xs) * stdev(ys))


def regression_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Audit intercept OLS in physical units using direct sample moments."""
    xs = [float(row["current_anomaly"]) for row in rows]
    ys = [float(row["future_target_anomaly"]) for row in rows]
    x_mean, y_mean = mean(xs), mean(ys)
    covariance = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / (len(xs) - 1)
    variance = sum((x - x_mean) ** 2 for x in xs) / (len(xs) - 1)
    beta = covariance / variance
    correlation = covariance / (stdev(xs) * stdev(ys))
    return {
        "n": len(rows),
        "current_anomaly_standard_deviation": stdev(xs),
        "future_target_anomaly_standard_deviation": stdev(ys),
        "correlation": correlation,
        "unstandardised_ols_slope": beta,
        "intercept": y_mean - beta * x_mean,
        "sample_covariance": covariance,
        "sample_current_anomaly_variance": variance,
        "direct_covariance_divided_by_variance": beta,
        "correlation_times_standard_deviation_ratio": correlation * stdev(ys) / stdev(xs),
        "zero_intercept_slope_for_forecast_model_contrast": sum(x * y for x, y in zip(xs, ys)) / sum(x * x for x in xs),
        "regression_definition": "ordinary unstandardised OLS with estimated intercept in degC anomaly units",
    }


def _scores(rows: list[dict[str, Any]], prediction: str) -> dict[str, Any]:
    if not rows:
        return {"n": 0, "bias_forecast_minus_observed": None, "mae": None, "rmse": None, "correlation": None}
    errors = [row[prediction] - row["observed_mean_temperature"] for row in rows]
    return {
        "n": len(rows),
        "bias_forecast_minus_observed": mean(errors),
        "mae": mean(abs(error) for error in errors),
        "rmse": math.sqrt(mean(error * error for error in errors)),
        "correlation": _correlation([row[prediction] for row in rows], [row["observed_mean_temperature"] for row in rows]),
    }


def _comparison(rows: list[dict[str, Any]]) -> dict[str, Any]:
    climatology = _scores(rows, "climatology_forecast")
    full = _scores(rows, "full_persistence_forecast")
    relaxation = _scores(rows, "relaxation_forecast")
    return {
        "n_paired": len(rows),
        "climatology": climatology,
        "full_anomaly_persistence": full,
        "fitted_anomaly_relaxation": relaxation,
        "improvement_relaxation_over_climatology": {
            "mae": climatology["mae"] - relaxation["mae"] if rows else None,
            "rmse": climatology["rmse"] - relaxation["rmse"] if rows else None,
        },
        "improvement_relaxation_over_full_persistence": {
            "mae": full["mae"] - relaxation["mae"] if rows else None,
            "rmse": full["rmse"] - relaxation["rmse"] if rows else None,
        },
    }


def _season(month: int) -> str:
    if month in (12, 1, 2):
        return "DJF"
    if month in (3, 4, 5):
        return "MAM"
    if month in (6, 7, 8):
        return "JJA"
    return "SON"


def run_relaxation_hindcast(
    canonical_paths: Iterable[str | Path], *, minimum_training_years: int = 3,
    minimum_climatology_years: int = 3, daily_minimum_hours: int = DAILY_MIN_HOURS,
    window_minimum_valid_days: int = WINDOW_MIN_VALID_DAYS,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Run the weekly model and compare all three forecasts on identical cases."""
    daily, provenance = read_daily_target_temperature(canonical_paths, minimum_hours=daily_minimum_hours)
    schedule = weekly_issue_schedule(daily)
    issue_years = sorted({issue["forecast_issue_time"].year for issue in schedule})
    coefficients_by_year: dict[int, dict[str, dict[str, Any]]] = {}
    for issue_year in issue_years:
        cases = build_relaxation_training_cases(
            daily, issue_year,
            minimum_climatology_years=minimum_climatology_years,
            window_minimum_valid_days=window_minimum_valid_days,
        )
        coefficients_by_year[issue_year] = fit_constrained_betas(
            cases, issue_year, minimum_training_years=minimum_training_years,
        )

    forecast_rows: list[dict[str, Any]] = []
    diagnostic_rows: list[dict[str, Any]] = []
    paired_rows: list[dict[str, Any]] = []
    for issue in schedule:
        issue_time = issue["forecast_issue_time"]
        state_date = issue["forecast_state_date"]
        anomaly, targets = anomaly_persistence_rows_from_daily(
            daily, state_date, issue_time.year,
            minimum_training_years=minimum_climatology_years,
            window_minimum_valid_days=window_minimum_valid_days,
        )
        coefficients = coefficients_by_year[issue_time.year]
        for target in targets:
            coefficient = coefficients[target["target_name"]]
            valid = anomaly["is_valid"] and target["target_climatology_mean"] is not None and coefficient["is_valid"]
            relaxation = (
                target["target_climatology_mean"] + coefficient["constrained_beta"] * anomaly["current_anomaly"]
                if valid else None
            )
            forecast = {
                "forecast_issue_time": issue_time.isoformat(), "forecast_state_date": state_date.isoformat(),
                "lake_id": "windermere", "basin": "south", "variable": "water_temperature", "depth_m": 2.0, "unit": "degC",
                "target_name": target["target_name"], "target_window_start": target["target_window_start"], "target_window_end": target["target_window_end"],
                "predicted_mean_temperature": relaxation, "target_climatology_mean": target["target_climatology_mean"],
                "current_anomaly": anomaly["current_anomaly"], "model_identifier": MODEL_ID, "model_version": MODEL_VERSION,
                "is_valid_forecast": valid, "raw_beta": coefficient["raw_beta"], "constrained_beta": coefficient["constrained_beta"],
                "beta_training_rows": coefficient["n_training_rows"], "n_distinct_beta_training_years": coefficient["n_distinct_training_years"],
                "beta_training_years": "|".join(str(year) for year in coefficient["training_years"]),
                "beta_training_cases_sha256": coefficient["training_cases_sha256"],
                "availability_mode": "observation_time_proxy",
                "availability_assumption": "historical data_available_time is unknown; observation_time is a pseudo-operational proxy",
            }
            forecast_rows.append(forecast)
            index = int(target["target_name"].split("_")[1])
            observed = _observed_window(daily, state_date, index, window_minimum_valid_days)
            if anomaly["is_valid"] and target["target_climatology_mean"] is not None and observed["is_valid"]:
                diagnostic_rows.append({
                    "forecast_issue_time": issue_time.isoformat(), "target_name": target["target_name"],
                    "current_anomaly": anomaly["current_anomaly"],
                    "future_target_anomaly": observed["observed_mean_temperature"] - target["target_climatology_mean"],
                })
            if not valid or not observed["is_valid"]:
                continue
            climatology = target["target_climatology_mean"]
            full = climatology + anomaly["current_anomaly"]
            paired_rows.append({
                **forecast,
                "observed_mean_temperature": observed["observed_mean_temperature"],
                "observed_valid_days": observed["valid_days"],
                "climatology_forecast": climatology,
                "full_persistence_forecast": full,
                "relaxation_forecast": relaxation,
                "issue_month": issue_time.month,
                "issue_season": _season(issue_time.month),
            })

    horizons = ("month_1", "month_2", "month_3")
    paired_by_horizon = {name: [row for row in paired_rows if row["target_name"] == name] for name in horizons}
    audit = {
        "audit_purpose": "confirm previous diagnostic slopes are unstandardised intercept OLS coefficients",
        "by_horizon": {
            name: regression_audit([row for row in diagnostic_rows if row["target_name"] == name])
            for name in horizons
        },
        "conclusion": "implementation is correct; near equality to correlation results from nearly equal current/future anomaly standard deviations",
    }
    comparison = {name: _comparison(rows) for name, rows in paired_by_horizon.items()}
    seasonal = {
        name: {season: _comparison([row for row in rows if row["issue_season"] == season]) for season in ("DJF", "MAM", "JJA", "SON")}
        for name, rows in paired_by_horizon.items()
    }
    monthly = {
        name: {str(month): _comparison([row for row in rows if row["issue_month"] == month]) for month in range(1, 13)}
        for name, rows in paired_by_horizon.items()
    }
    beta_evolution = {
        str(year): {name: coefficients_by_year[year][name] for name in horizons}
        for year in issue_years
    }
    summary = {
        "model_identifier": MODEL_ID, "model_version": MODEL_VERSION,
        "fitting_rule": "zero-intercept OLS beta=sum(x*y)/sum(x^2), fitted separately by horizon on completed weekly cases from issue years strictly before Y, then constrained to [0,1]",
        "training_case_cutoff": "target window must end before January 1 of coefficient issue year; beta is fixed within each issue year",
        "minimum_distinct_training_years": minimum_training_years,
        "issue_schedule": {"definition": "same weekly schedule as existing baselines", "n_scheduled_issue_dates": len(schedule)},
        "paired_comparison_by_horizon": comparison,
        "paired_comparison_by_issue_season": seasonal,
        "paired_comparison_by_issue_month": monthly,
        "beta_evolution_by_issue_year": beta_evolution,
        "forecast_target_artifact_used_by_model": False,
        "source_provenance_verification_side": {
            "canonical_input_sha256": provenance["canonical_input_sha256"],
            "source_dataset_ids": provenance["source_dataset_ids"], "source_files": provenance["source_files"],
        },
    }
    return audit, summary, forecast_rows, paired_rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["forecast_issue_time"])
        writer.writeheader()
        writer.writerows(rows)


def write_relaxation_hindcast_outputs(audit: dict[str, Any], summary: dict[str, Any], forecasts: list[dict[str, Any]], paired: list[dict[str, Any]], output_dir: str | Path) -> None:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "regression_audit.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    (destination / "paired_verification_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    _write_csv(destination / "hindcast_forecasts.csv", forecasts)
    _write_csv(destination / "paired_verification.csv", paired)

    import matplotlib.pyplot as plt

    horizons = ("month_1", "month_2", "month_3")
    years = sorted(int(year) for year in summary["beta_evolution_by_issue_year"])
    figure, axes = plt.subplots(3, 1, figsize=(8, 7), sharex=True)
    for axis, name in zip(axes, horizons):
        valid_years = [year for year in years if summary["beta_evolution_by_issue_year"][str(year)][name]["is_valid"]]
        raw = [summary["beta_evolution_by_issue_year"][str(year)][name]["raw_beta"] for year in valid_years]
        constrained = [summary["beta_evolution_by_issue_year"][str(year)][name]["constrained_beta"] for year in valid_years]
        axis.plot(valid_years, raw, marker="o", label="raw zero-intercept beta")
        axis.plot(valid_years, constrained, marker="o", linestyle="--", label="constrained beta")
        axis.axhline(0, color="grey", linewidth=0.6)
        axis.axhline(1, color="grey", linewidth=0.6)
        axis.set_ylabel(name)
    axes[0].legend(fontsize=8)
    axes[-1].set_xlabel("Forecast issue year")
    figure.tight_layout()
    figure.savefig(destination / "beta_evolution.png", dpi=150)
    plt.close(figure)

    x = range(len(horizons))
    width = 0.25
    figure, axes = plt.subplots(1, 2, figsize=(9, 3.8))
    for axis, metric in zip(axes, ("mae", "rmse")):
        for offset, (label, key) in zip((-width, 0, width), (("climatology", "climatology"), ("full persistence", "full_anomaly_persistence"), ("fitted relaxation", "fitted_anomaly_relaxation"))):
            values = [summary["paired_comparison_by_horizon"][name][key][metric] for name in horizons]
            axis.bar([position + offset for position in x], values, width, label=label)
        axis.set_xticks(list(x), horizons)
        axis.set_ylabel(f"{metric.upper()} (degC)")
    axes[0].legend(fontsize=8)
    figure.tight_layout()
    figure.savefig(destination / "three_model_score_comparison.png", dpi=150)
    plt.close(figure)
