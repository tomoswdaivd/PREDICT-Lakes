"""Leakage-safe expanding historical climatology for 30-day targets."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, stdev
from typing import Any, Iterable

from .forecast_targets import DAILY_MIN_HOURS, TARGET, WINDOW_MIN_VALID_DAYS
from .window_definition import WINDOW_DAYS, equivalent_window_bounds, target_window_bounds

MODEL_ID = "expanding_prior_year_window_climatology"
MODEL_VERSION = "1.0"
MINIMUM_TRAINING_YEARS = 3


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_utc(value: str, field: str) -> datetime:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return timestamp.astimezone(timezone.utc)


def _load_state(forecast_state: dict[str, Any] | str | Path) -> tuple[dict[str, Any], dict[str, str]]:
    if isinstance(forecast_state, dict):
        return forecast_state, {"forecast_state_input": "in-memory", "forecast_state_sha256": "not_applicable"}
    path = Path(forecast_state)
    return json.loads(path.read_text(encoding="utf-8")), {
        "forecast_state_input": str(path),
        "forecast_state_sha256": _sha256(path),
    }


def _validate_state(state: dict[str, Any]) -> tuple[date, datetime]:
    expected_site = {"lake_id": "windermere", "basin": "south"}
    expected_target = {
        "variable": "water_temperature", "depth_m": 2.0, "unit": "degC",
        "resolution": "daily", "calendar": "UTC",
    }
    if state.get("site") != expected_site or state.get("target") != expected_target:
        raise ValueError("forecast state must describe daily Windermere South Basin 2 m water temperature in degC/UTC")
    if not state.get("last_complete_daily_state_date"):
        raise ValueError("forecast state has no last_complete_daily_state_date")
    if not state.get("forecast_issue_time"):
        raise ValueError("forecast state has no forecast_issue_time")
    state_date = date.fromisoformat(state["last_complete_daily_state_date"])
    issue_time = _parse_utc(state["forecast_issue_time"], "forecast_issue_time")
    if state_date > issue_time.date():
        raise ValueError("forecast state date cannot be after forecast issue date")
    return state_date, issue_time


def read_training_daily_temperature(
    canonical_paths: Iterable[str | Path], issue_year: int, *, minimum_hours: int = DAILY_MIN_HOURS,
) -> tuple[dict[date, float], dict[str, Any]]:
    """Read only observations from years strictly before ``issue_year``."""
    if minimum_hours < 1 or minimum_hours > 24:
        raise ValueError("minimum_hours must be between 1 and 24")
    hourly: dict[datetime, tuple[float, str, str]] = {}
    canonical_inputs: list[str] = []
    for input_path in canonical_paths:
        path = Path(input_path)
        canonical_inputs.append(str(path))
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if any(row.get(key) != value for key, value in TARGET.items()):
                    continue
                timestamp = _parse_utc(row["observation_time"], "observation_time")
                if timestamp.year >= issue_year:
                    continue
                if timestamp in hourly:
                    raise ValueError(f"duplicate training timestamp across canonical inputs: {timestamp.isoformat()}")
                hourly[timestamp] = (float(row["value"]), row.get("source_dataset", ""), row.get("source_file", ""))

    grouped: dict[date, list[tuple[datetime, float]]] = defaultdict(list)
    for timestamp, (value, _, _) in hourly.items():
        grouped[timestamp.date()].append((timestamp, value))
    daily = {
        day: mean(value for _, value in values)
        for day, values in grouped.items()
        if len(values) >= minimum_hours
    }
    hash_rows = [
        {"observation_time": timestamp.isoformat(), "value": value, "source_dataset": dataset, "source_file": source_file}
        for timestamp, (value, dataset, source_file) in sorted(hourly.items())
    ]
    return daily, {
        "canonical_inputs": sorted(canonical_inputs),
        "eligible_training_observation_slice_sha256": _stable_hash(hash_rows),
        "source_dataset_ids": sorted({dataset for _, dataset, _ in hourly.values() if dataset}),
        "source_files": sorted({source_file for _, _, source_file in hourly.values() if source_file}),
        "training_observation_rule": f"observation calendar year < {issue_year}",
        "daily_completeness_rule": f"at least {minimum_hours} distinct valid hourly observations per UTC calendar day; no imputation",
    }


def forecast_rows_from_daily(
    daily: dict[date, float], state_date: date, issue_year: int, *, minimum_training_years: int,
    window_minimum_valid_days: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in (1, 2, 3):
        target_start, target_end = target_window_bounds(state_date, index)
        climatology = historical_window_climatology(
            daily, target_start, issue_year,
            minimum_training_years=minimum_training_years,
            window_minimum_valid_days=window_minimum_valid_days,
        )
        rows.append({
            "target_name": f"month_{index}",
            "lead_days": {"start": (index - 1) * WINDOW_DAYS + 1, "end": index * WINDOW_DAYS},
            "target_window_start": target_start.isoformat(),
            "target_window_end": target_end.isoformat(),
            "candidate_previous_years": climatology["candidate_previous_years"],
            "n_candidate_previous_years": climatology["n_candidate_previous_years"],
            "training_years": climatology["training_years"],
            "n_valid_training_years": climatology["n_valid_training_years"],
            "minimum_training_years": minimum_training_years,
            "is_valid": climatology["is_valid"],
            "predicted_mean_temperature": climatology["climatological_mean"],
            "historical_window_standard_deviation": climatology["historical_window_standard_deviation"],
            "spread_interpretation": "descriptive sample standard deviation; not a calibrated probabilistic forecast",
            "unit": "degC",
            "site": {"lake_id": "windermere", "basin": "south"},
            "variable": "water_temperature",
            "depth_m": 2.0,
            "training_windows": climatology["training_windows"],
            "training_windows_sha256": climatology["training_windows_sha256"],
            "calendar_alignment": "same target-window start month/day in each prior year, followed by 30 consecutive UTC dates",
            "unavailable_february_29_start_years": climatology["unavailable_february_29_start_years"],
        })
    return rows


def historical_window_climatology(
    daily: dict[date, float], window_start: date, issue_year: int, *,
    minimum_training_years: int, window_minimum_valid_days: int,
) -> dict[str, Any]:
    """Summarise a matched 30-day window over calendar years before issue year."""
    observed_years = sorted({day.year for day in daily if day.year < issue_year})
    candidate_years = list(range(min(observed_years), issue_year)) if observed_years else []
    training_windows: list[dict[str, Any]] = []
    unavailable_leap_years: list[int] = []
    for year in candidate_years:
        bounds = equivalent_window_bounds(window_start, year)
        if bounds is None:
            unavailable_leap_years.append(year)
            continue
        start, end = bounds
        dates = [start + timedelta(days=offset) for offset in range(WINDOW_DAYS)]
        values = [daily[day] for day in dates if day in daily and day.year < issue_year]
        if len(values) >= window_minimum_valid_days:
            training_windows.append({
                "year": year, "start_date": start.isoformat(), "end_date": end.isoformat(),
                "valid_days": len(values), "mean_temperature": mean(values),
            })
    training_years = [window["year"] for window in training_windows]
    training_means = [window["mean_temperature"] for window in training_windows]
    valid = len(training_means) >= minimum_training_years
    return {
        "candidate_previous_years": candidate_years,
        "n_candidate_previous_years": len(candidate_years),
        "training_years": training_years,
        "n_valid_training_years": len(training_years),
        "is_valid": valid,
        "climatological_mean": mean(training_means) if valid else None,
        "historical_window_standard_deviation": stdev(training_means) if len(training_means) > 1 else None,
        "training_windows": training_windows,
        "training_windows_sha256": _stable_hash(training_windows),
        "unavailable_february_29_start_years": unavailable_leap_years,
    }


def forecast_climatology(
    canonical_paths: Iterable[str | Path], forecast_state: dict[str, Any] | str | Path, *,
    minimum_training_years: int = MINIMUM_TRAINING_YEARS,
    daily_minimum_hours: int = DAILY_MIN_HOURS,
    window_minimum_valid_days: int = WINDOW_MIN_VALID_DAYS,
) -> dict[str, Any]:
    """Forecast target-window means from direct equivalent windows in prior years."""
    if minimum_training_years < 1:
        raise ValueError("minimum_training_years must be positive")
    if window_minimum_valid_days < 1 or window_minimum_valid_days > WINDOW_DAYS:
        raise ValueError("window_minimum_valid_days must be between 1 and 30")
    state, state_input = _load_state(forecast_state)
    state_date, issue_time = _validate_state(state)
    daily, training_provenance = read_training_daily_temperature(
        canonical_paths, issue_time.year, minimum_hours=daily_minimum_hours,
    )
    forecasts = forecast_rows_from_daily(
        daily, state_date, issue_time.year, minimum_training_years=minimum_training_years,
        window_minimum_valid_days=window_minimum_valid_days,
    )
    return {
        "component": "forecast_climatology",
        "workflow_role": "forecast_side_only",
        "model": {
            "identifier": MODEL_ID, "version": MODEL_VERSION,
            "configuration": {
                "minimum_training_years": minimum_training_years,
                "daily_minimum_hours": daily_minimum_hours,
                "window_minimum_valid_days": window_minimum_valid_days,
                "seasonal_smoother": None,
            },
        },
        "forecast_issue_time": issue_time.isoformat(),
        "forecast_state_date": state_date.isoformat(),
        "availability_mode": state.get("availability_mode"),
        "availability_assumption": state.get("availability_assumption"),
        "target_definition": "three non-overlapping future 30-day mean 2 m water-temperature lead windows anchored after forecast_state_date",
        "training_year_rule": f"only observations with calendar year < issue year {issue_time.year}",
        "forecast_target_input_used": False,
        "forecasts": forecasts,
        "provenance": {
            **state_input,
            **training_provenance,
            "forecast_record_sha256": _stable_hash(forecasts),
            "source_timestamps_preserved": True,
            "leakage_note": "Forecast-year and later observations are filtered before daily aggregation and are not hashed into forecast provenance.",
        },
    }


def write_climatology_forecast(result: dict[str, Any], output_path: str | Path) -> None:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
