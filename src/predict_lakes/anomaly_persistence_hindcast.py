"""Paired weekly hindcast diagnostics for full anomaly persistence."""

from __future__ import annotations

import csv
import json
import math
import random
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import mean, median, stdev
from typing import Any, Iterable

from .anomaly_persistence import MODEL_ID, MODEL_VERSION, anomaly_persistence_rows_from_daily
from .climatology import MINIMUM_TRAINING_YEARS
from .climatology_hindcast import _observed_window, weekly_issue_schedule
from .forecast_targets import DAILY_MIN_HOURS, WINDOW_MIN_VALID_DAYS, read_daily_target_temperature


def _correlation(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    x_mean, y_mean = mean(xs), mean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    denominator = math.sqrt(sum((x - x_mean) ** 2 for x in xs) * sum((y - y_mean) ** 2 for y in ys))
    return numerator / denominator if denominator else None


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
    persistence = _scores(rows, "anomaly_persistence_forecast")
    return {
        "n_paired": len(rows),
        "climatology": climatology,
        "anomaly_persistence": persistence,
        "change_anomaly_minus_climatology": {
            "bias": persistence["bias_forecast_minus_observed"] - climatology["bias_forecast_minus_observed"] if rows else None,
            "mae": persistence["mae"] - climatology["mae"] if rows else None,
            "rmse": persistence["rmse"] - climatology["rmse"] if rows else None,
        },
    }


def _regression(xs: list[float], ys: list[float]) -> dict[str, Any]:
    n = len(xs)
    if n < 3:
        return {"n": n, "correlation": _correlation(xs, ys), "slope": None, "intercept": None, "slope_standard_error": None, "slope_95_percent_ci": None}
    x_mean, y_mean = mean(xs), mean(ys)
    sxx = sum((x - x_mean) ** 2 for x in xs)
    if not sxx:
        return {"n": n, "correlation": None, "slope": None, "intercept": None, "slope_standard_error": None, "slope_95_percent_ci": None}
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / sxx
    intercept = y_mean - slope * x_mean
    residuals = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
    standard_error = math.sqrt(sum(value * value for value in residuals) / (n - 2) / sxx)
    margin = 1.96 * standard_error
    return {
        "n": n, "correlation": _correlation(xs, ys), "slope": slope, "intercept": intercept,
        "slope_standard_error": standard_error,
        "slope_95_percent_ci": [slope - margin, slope + margin],
        "confidence_interval_method": "normal approximation (1.96 standard errors); exploratory",
    }


def _year_block_bootstrap_slope(rows: list[dict[str, Any]], *, iterations: int = 2000, seed: int = 20260914) -> dict[str, Any]:
    """Bootstrap whole issue years to retain dependence among overlapping weekly windows."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["forecast_issue_time"][:4]].append(row)
    years = sorted(grouped)
    generator = random.Random(seed)
    slopes: list[float] = []
    for _ in range(iterations):
        sampled = [generator.choice(years) for _ in years]
        sample_rows = [row for year in sampled for row in grouped[year]]
        xs = [row["current_anomaly"] for row in sample_rows]
        ys = [row["future_target_anomaly"] for row in sample_rows]
        x_mean, y_mean = mean(xs), mean(ys)
        sxx = sum((x - x_mean) ** 2 for x in xs)
        if sxx:
            slopes.append(sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / sxx)
    slopes.sort()
    lower = slopes[int(0.025 * (len(slopes) - 1))]
    upper = slopes[int(0.975 * (len(slopes) - 1))]
    return {
        "slope_95_percent_issue_year_block_bootstrap_ci": [lower, upper],
        "bootstrap_iterations": iterations,
        "bootstrap_seed": seed,
        "n_issue_year_blocks": len(years),
        "bootstrap_note": "whole issue years are resampled to retain within-year dependence from overlapping weekly windows",
    }


def _distribution(values: list[float]) -> dict[str, Any]:
    ordered = sorted(values)
    def quantile(fraction: float) -> float:
        position = fraction * (len(ordered) - 1)
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        return ordered[lower] * (1 - weight) + ordered[upper] * weight
    return {
        "n": len(values), "mean": mean(values), "median": median(values),
        "standard_deviation": stdev(values) if len(values) > 1 else None,
        "minimum": ordered[0], "q10": quantile(0.1), "q90": quantile(0.9), "maximum": ordered[-1],
    }


def _season(month: int) -> str:
    if month in (12, 1, 2):
        return "DJF"
    if month in (3, 4, 5):
        return "MAM"
    if month in (6, 7, 8):
        return "JJA"
    return "SON"


def run_paired_hindcast(
    canonical_paths: Iterable[str | Path], *, minimum_training_years: int = MINIMUM_TRAINING_YEARS,
    daily_minimum_hours: int = DAILY_MIN_HOURS, window_minimum_valid_days: int = WINDOW_MIN_VALID_DAYS,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Generate anomaly forecasts, then perform paired verification separately."""
    daily, source_provenance = read_daily_target_temperature(canonical_paths, minimum_hours=daily_minimum_hours)
    schedule = weekly_issue_schedule(daily)
    forecast_rows: list[dict[str, Any]] = []
    anomalies_by_issue: list[dict[str, Any]] = []
    for issue in schedule:
        state_date = issue["forecast_state_date"]
        issue_time = issue["forecast_issue_time"]
        anomaly, rows = anomaly_persistence_rows_from_daily(
            daily, state_date, issue_time.year,
            minimum_training_years=minimum_training_years,
            window_minimum_valid_days=window_minimum_valid_days,
        )
        anomalies_by_issue.append({"forecast_issue_time": issue_time.isoformat(), "forecast_state_date": state_date.isoformat(), **anomaly})
        for row in rows:
            forecast_rows.append({
                "forecast_issue_time": issue_time.isoformat(), "forecast_state_date": state_date.isoformat(),
                "lake_id": "windermere", "basin": "south", "variable": "water_temperature", "depth_m": 2.0, "unit": "degC",
                "target_name": row["target_name"], "target_window_start": row["target_window_start"], "target_window_end": row["target_window_end"],
                "predicted_mean_temperature": row["predicted_mean_temperature"], "target_climatology_mean": row["target_climatology_mean"],
                "current_anomaly": row["current_anomaly"], "model_identifier": MODEL_ID, "model_version": MODEL_VERSION,
                "anomaly_coefficient": 1.0, "coefficient_fitted": False,
                "is_valid_forecast": row["is_valid"], "n_valid_target_training_years": row["n_valid_training_years"],
                "target_training_years": "|".join(str(year) for year in row["training_years"]),
                "target_training_windows_sha256": row["training_windows_sha256"],
                "recent_window_start": anomaly["window_start"], "recent_window_end": anomaly["window_end"],
                "recent_valid_days": anomaly["valid_days"], "recent_30_day_mean": anomaly["recent_30_day_mean"],
                "recent_window_climatology": anomaly["historical_recent_window_climatology"],
                "n_valid_recent_training_years": anomaly["n_valid_historical_years"],
                "recent_training_years": "|".join(str(year) for year in anomaly["historical_training_years"]),
                "availability_mode": "observation_time_proxy",
                "availability_assumption": "historical data_available_time is unknown; observation_time is a pseudo-operational proxy",
            })

    # Verification-side future observations are joined only after every forecast exists.
    paired_rows: list[dict[str, Any]] = []
    for forecast in forecast_rows:
        index = int(forecast["target_name"].split("_")[1])
        observed = _observed_window(daily, date.fromisoformat(forecast["forecast_state_date"]), index, window_minimum_valid_days)
        if not forecast["is_valid_forecast"] or not observed["is_valid"]:
            continue
        target_climatology = forecast["target_climatology_mean"]
        current_anomaly = forecast["current_anomaly"]
        paired_rows.append({
            **forecast,
            "observed_mean_temperature": observed["observed_mean_temperature"],
            "observed_valid_days": observed["valid_days"],
            "climatology_forecast": target_climatology,
            "anomaly_persistence_forecast": forecast["predicted_mean_temperature"],
            "climatology_error": target_climatology - observed["observed_mean_temperature"],
            "anomaly_persistence_error": forecast["predicted_mean_temperature"] - observed["observed_mean_temperature"],
            "future_target_anomaly": observed["observed_mean_temperature"] - target_climatology,
            "issue_month": int(forecast["forecast_issue_time"][5:7]),
            "issue_season": _season(int(forecast["forecast_issue_time"][5:7])),
            "anomaly_identity_check": forecast["predicted_mean_temperature"] - target_climatology - current_anomaly,
        })

    horizons = ("month_1", "month_2", "month_3")
    paired_by_horizon = {name: [row for row in paired_rows if row["target_name"] == name] for name in horizons}
    comparisons = {name: _comparison(rows) for name, rows in paired_by_horizon.items()}
    by_month = {
        name: {str(month): _comparison([row for row in rows if row["issue_month"] == month]) for month in range(1, 13)}
        for name, rows in paired_by_horizon.items()
    }
    by_season = {
        name: {season: _comparison([row for row in rows if row["issue_season"] == season]) for season in ("DJF", "MAM", "JJA", "SON")}
        for name, rows in paired_by_horizon.items()
    }
    persistence_diagnostics = {}
    for name, rows in paired_by_horizon.items():
        diagnostic = _regression(
            [row["current_anomaly"] for row in rows],
            [row["future_target_anomaly"] for row in rows],
        )
        diagnostic.update(_year_block_bootstrap_slope(rows))
        persistence_diagnostics[name] = diagnostic
    valid_anomalies = [row for row in anomalies_by_issue if row["is_valid"]]
    distribution_by_year = {
        str(year): _distribution([row["current_anomaly"] for row in valid_anomalies if row["forecast_issue_time"].startswith(str(year))])
        for year in range(2008, 2019)
        if any(row["forecast_issue_time"].startswith(str(year)) for row in valid_anomalies)
    }
    anomaly_trend = _regression(
        [float(row["forecast_issue_time"][:4]) for row in valid_anomalies],
        [row["current_anomaly"] for row in valid_anomalies],
    )
    summary = {
        "model_identifier": MODEL_ID, "model_version": MODEL_VERSION,
        "scientific_definition": "target_window_climatology + (recent_30_day_mean - matched_recent_window_climatology), with anomaly coefficient fixed at 1.0",
        "coefficient_fitted": False,
        "issue_schedule": {"definition": "same valid-Monday-state/Tuesday-00:00-UTC weekly schedule as climatology", "n_scheduled_issue_dates": len(schedule)},
        "completeness": {"daily_minimum_hours": daily_minimum_hours, "window_minimum_valid_days_out_of_30": window_minimum_valid_days, "imputation": False},
        "minimum_training_years": minimum_training_years,
        "paired_comparison_by_horizon": comparisons,
        "paired_comparison_by_issue_month": by_month,
        "paired_comparison_by_issue_season": by_season,
        "anomaly_survival_diagnostics": persistence_diagnostics,
        "current_anomaly_distribution_over_time": {
            "overall": _distribution([row["current_anomaly"] for row in valid_anomalies]),
            "by_issue_year": distribution_by_year,
            "linear_year_relationship": anomaly_trend,
            "interpretation_limit": "descriptive only; no attribution to long-term warming is made",
        },
        "forecast_target_artifact_used_by_model": False,
        "source_provenance_verification_side": {
            "canonical_input_sha256": source_provenance["canonical_input_sha256"],
            "source_dataset_ids": source_provenance["source_dataset_ids"],
            "source_files": source_provenance["source_files"],
        },
    }
    return summary, forecast_rows, paired_rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["forecast_issue_time"])
        writer.writeheader()
        writer.writerows(rows)


def write_paired_hindcast_outputs(summary: dict[str, Any], forecasts: list[dict[str, Any]], paired: list[dict[str, Any]], output_dir: str | Path) -> None:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "paired_verification_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    _write_csv(destination / "hindcast_forecasts.csv", forecasts)
    _write_csv(destination / "paired_verification.csv", paired)

    import matplotlib.pyplot as plt

    horizons = ("month_1", "month_2", "month_3")
    figure, axes = plt.subplots(1, 3, figsize=(11, 3.7), sharex=True, sharey=True)
    for axis, name in zip(axes, horizons):
        rows = [row for row in paired if row["target_name"] == name]
        xs = [row["current_anomaly"] for row in rows]
        ys = [row["future_target_anomaly"] for row in rows]
        axis.scatter(xs, ys, s=9, alpha=0.5)
        diagnostic = summary["anomaly_survival_diagnostics"][name]
        low, high = min(xs + ys), max(xs + ys)
        axis.plot([low, high], [low, high], color="grey", linestyle="--", linewidth=0.9, label="full persistence")
        if diagnostic["slope"] is not None:
            axis.plot([low, high], [diagnostic["intercept"] + diagnostic["slope"] * low, diagnostic["intercept"] + diagnostic["slope"] * high], color="black", linewidth=1, label="exploratory fit")
        axis.axhline(0, color="grey", linewidth=0.5)
        axis.axvline(0, color="grey", linewidth=0.5)
        axis.set_title(f"{name}: slope={diagnostic['slope']:.2f}")
        axis.set_xlabel("Current 30-day anomaly (degC)")
    axes[0].set_ylabel("Future target anomaly (degC)")
    axes[0].legend(fontsize=8)
    figure.tight_layout()
    figure.savefig(destination / "anomaly_survival.png", dpi=150)
    plt.close(figure)

    x = range(len(horizons))
    width = 0.36
    figure, axes = plt.subplots(1, 2, figsize=(8, 3.6))
    for axis, metric in zip(axes, ("mae", "rmse")):
        climatology = [summary["paired_comparison_by_horizon"][name]["climatology"][metric] for name in horizons]
        persistence = [summary["paired_comparison_by_horizon"][name]["anomaly_persistence"][metric] for name in horizons]
        axis.bar([value - width / 2 for value in x], climatology, width, label="climatology")
        axis.bar([value + width / 2 for value in x], persistence, width, label="full anomaly persistence")
        axis.set_xticks(list(x), horizons)
        axis.set_ylabel(f"{metric.upper()} (degC)")
    axes[0].legend(fontsize=8)
    figure.tight_layout()
    figure.savefig(destination / "paired_score_comparison.png", dpi=150)
    plt.close(figure)
