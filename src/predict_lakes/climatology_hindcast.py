"""Reproducible weekly hindcast and verification for the climatology baseline."""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable

from .climatology import MINIMUM_TRAINING_YEARS, forecast_rows_from_daily
from .forecast_targets import DAILY_MIN_HOURS, WINDOW_MIN_VALID_DAYS, read_daily_target_temperature
from .window_definition import WINDOW_DAYS, target_window_bounds


def weekly_issue_schedule(daily: dict[date, float]) -> list[dict[str, Any]]:
    """Select valid Monday state dates and issue at 00:00 UTC on Tuesday."""
    return [
        {
            "forecast_state_date": day,
            "forecast_issue_time": datetime.combine(day + timedelta(days=1), datetime.min.time(), timezone.utc),
        }
        for day in sorted(daily)
        if date(2008, 1, 1) <= day and (day + timedelta(days=1)).year <= 2018 and day.weekday() == 0
    ]


def _observed_window(daily: dict[date, float], state_date: date, index: int, minimum_days: int) -> dict[str, Any]:
    start, end = target_window_bounds(state_date, index)
    values = [daily[day] for day in (start + timedelta(days=i) for i in range(WINDOW_DAYS)) if day in daily]
    valid = len(values) >= minimum_days
    return {
        "target_window_start": start.isoformat(), "target_window_end": end.isoformat(),
        "valid_days": len(values), "is_valid": valid,
        "observed_mean_temperature": mean(values) if valid else None,
    }


def _correlation(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    x_mean, y_mean = mean(xs), mean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    denominator = math.sqrt(sum((x - x_mean) ** 2 for x in xs) * sum((y - y_mean) ** 2 for y in ys))
    return numerator / denominator if denominator else None


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    errors = [row["error"] for row in rows]
    forecasts = [row["predicted_mean_temperature"] for row in rows]
    observations = [row["observed_mean_temperature"] for row in rows]
    return {
        "n": len(rows),
        "bias_forecast_minus_observed": mean(errors) if errors else None,
        "mae": mean(abs(value) for value in errors) if errors else None,
        "rmse": math.sqrt(mean(value * value for value in errors)) if errors else None,
        "correlation": _correlation(forecasts, observations),
    }


def run_weekly_hindcast(
    canonical_paths: Iterable[str | Path], *, minimum_training_years: int = MINIMUM_TRAINING_YEARS,
    daily_minimum_hours: int = DAILY_MIN_HOURS, window_minimum_valid_days: int = WINDOW_MIN_VALID_DAYS,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Generate forecasts first, then join future observations on the verification side."""
    daily, source_provenance = read_daily_target_temperature(canonical_paths, minimum_hours=daily_minimum_hours)
    schedule = weekly_issue_schedule(daily)
    forecast_rows: list[dict[str, Any]] = []
    for issue in schedule:
        state_date = issue["forecast_state_date"]
        issue_time = issue["forecast_issue_time"]
        rows = forecast_rows_from_daily(
            daily, state_date, issue_time.year,
            minimum_training_years=minimum_training_years,
            window_minimum_valid_days=window_minimum_valid_days,
        )
        for row in rows:
            forecast_rows.append({
                "forecast_issue_time": issue_time.isoformat(),
                "forecast_state_date": state_date.isoformat(),
                "lake_id": "windermere", "basin": "south",
                "variable": "water_temperature", "depth_m": 2.0,
                "target_name": row["target_name"],
                "target_window_start": row["target_window_start"],
                "target_window_end": row["target_window_end"],
                "predicted_mean_temperature": row["predicted_mean_temperature"],
                "unit": "degC", "model_identifier": "expanding_prior_year_window_climatology",
                "model_version": "1.0", "is_valid_forecast": row["is_valid"],
                "n_candidate_previous_years": row["n_candidate_previous_years"],
                "n_valid_training_years": row["n_valid_training_years"],
                "training_years": "|".join(str(year) for year in row["training_years"]),
                "training_windows_sha256": row["training_windows_sha256"],
                "historical_window_standard_deviation": row["historical_window_standard_deviation"],
                "availability_mode": "observation_time_proxy",
                "availability_assumption": "historical data_available_time is unknown; observation_time is a pseudo-operational proxy",
            })

    # Verification begins only after forecast rows have been completed.
    verification_rows: list[dict[str, Any]] = []
    for forecast in forecast_rows:
        index = int(forecast["target_name"].split("_")[1])
        observed = _observed_window(daily, date.fromisoformat(forecast["forecast_state_date"]), index, window_minimum_valid_days)
        if not forecast["is_valid_forecast"] or not observed["is_valid"]:
            continue
        verification_rows.append({
            **forecast,
            "observed_mean_temperature": observed["observed_mean_temperature"],
            "observed_valid_days": observed["valid_days"],
            "error": forecast["predicted_mean_temperature"] - observed["observed_mean_temperature"],
            "issue_month": int(forecast["forecast_issue_time"][5:7]),
        })

    by_horizon = {
        name: _metrics([row for row in verification_rows if row["target_name"] == name])
        for name in ("month_1", "month_2", "month_3")
    }
    by_issue_month = {
        name: {
            str(month): _metrics([row for row in verification_rows if row["target_name"] == name and row["issue_month"] == month])
            for month in range(1, 13)
        }
        for name in ("month_1", "month_2", "month_3")
    }
    training_counts: dict[str, dict[str, Any]] = {}
    for name in ("month_1", "month_2", "month_3"):
        by_year: dict[int, list[int]] = defaultdict(list)
        for row in forecast_rows:
            if row["target_name"] == name:
                by_year[int(row["forecast_issue_time"][:4])].append(row["n_valid_training_years"])
        training_counts[name] = {
            str(year): {"minimum": min(values), "median": median(values), "maximum": max(values)}
            for year, values in sorted(by_year.items())
        }
    summary = {
        "model_identifier": "expanding_prior_year_window_climatology",
        "model_version": "1.0",
        "issue_schedule": {
            "definition": "one issue per week: valid Monday daily state, issued Tuesday at 00:00 UTC",
            "justification": "daily issues would produce 29/30-overlapping month_1 targets; weekly sampling reduces redundancy sevenfold while retaining year-round seasonal coverage",
            "n_scheduled_issue_dates": len(schedule),
        },
        "training_year_rule": "every training observation calendar year is strictly earlier than the issue year",
        "calendar_alignment": "same target-window start month/day in each prior year, followed by 30 consecutive UTC dates; February 29 starts are unavailable in non-leap years",
        "daily_completeness_rule": f"at least {daily_minimum_hours} distinct valid hourly observations per UTC day; no imputation",
        "window_completeness_rule": f"at least {window_minimum_valid_days} of 30 valid daily means; no imputation",
        "minimum_training_years": minimum_training_years,
        "verification_error_definition": "forecast minus observed",
        "forecast_target_artifact_used_by_model": False,
        "counts_by_horizon": {
            name: {
                "scheduled": sum(row["target_name"] == name for row in forecast_rows),
                "valid_forecasts": sum(row["target_name"] == name and row["is_valid_forecast"] for row in forecast_rows),
                "valid_forecast_target_pairs": sum(row["target_name"] == name for row in verification_rows),
            }
            for name in ("month_1", "month_2", "month_3")
        },
        "metrics_by_horizon": by_horizon,
        "metrics_by_issue_month": by_issue_month,
        "training_year_counts_by_issue_year": training_counts,
        "source_provenance_verification_side": {
            "canonical_input_sha256": source_provenance["canonical_input_sha256"],
            "source_dataset_ids": source_provenance["source_dataset_ids"],
            "source_files": source_provenance["source_files"],
        },
    }
    return summary, forecast_rows, verification_rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["forecast_issue_time"])
        writer.writeheader()
        writer.writerows(rows)


def write_hindcast_outputs(summary: dict[str, Any], forecasts: list[dict[str, Any]], verification: list[dict[str, Any]], output_dir: str | Path) -> None:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "verification_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    _write_csv(destination / "hindcast_forecasts.csv", forecasts)
    _write_csv(destination / "verification_pairs.csv", verification)

    import matplotlib.pyplot as plt

    names = ("month_1", "month_2", "month_3")
    figure, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True)
    for axis, name in zip(axes, names):
        rows = [row for row in verification if row["target_name"] == name]
        dates = [date.fromisoformat(row["forecast_state_date"]) for row in rows]
        axis.plot(dates, [row["observed_mean_temperature"] for row in rows], label="observed", linewidth=1)
        axis.plot(dates, [row["predicted_mean_temperature"] for row in rows], label="climatology", linewidth=1)
        axis.set_ylabel(f"{name} (degC)")
    axes[0].legend(ncol=2)
    axes[-1].set_xlabel("Forecast-state date")
    figure.tight_layout()
    figure.savefig(destination / "hindcast_time_series.png", dpi=150)
    plt.close(figure)

    figure, axes = plt.subplots(1, 3, figsize=(10, 3.5), sharex=True, sharey=True)
    for axis, name in zip(axes, names):
        rows = [row for row in verification if row["target_name"] == name]
        observed = [row["observed_mean_temperature"] for row in rows]
        predicted = [row["predicted_mean_temperature"] for row in rows]
        axis.scatter(observed, predicted, s=8, alpha=0.55)
        low = min(observed + predicted)
        high = max(observed + predicted)
        axis.plot([low, high], [low, high], color="black", linewidth=0.8)
        axis.set_title(name)
        axis.set_xlabel("Observed (degC)")
    axes[0].set_ylabel("Forecast (degC)")
    figure.tight_layout()
    figure.savefig(destination / "forecast_vs_observed.png", dpi=150)
    plt.close(figure)
