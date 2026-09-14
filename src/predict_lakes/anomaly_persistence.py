"""Leakage-safe full anomaly-persistence baseline for 30-day targets."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from .climatology import (
    MINIMUM_TRAINING_YEARS,
    _load_state,
    _parse_utc,
    _stable_hash,
    _validate_state,
    forecast_rows_from_daily,
    historical_window_climatology,
    read_training_daily_temperature,
)
from .forecast_targets import DAILY_MIN_HOURS, TARGET, WINDOW_MIN_VALID_DAYS
from .window_definition import WINDOW_DAYS

MODEL_ID = "full_anomaly_persistence"
MODEL_VERSION = "1.0"


def read_recent_legal_daily_temperature(
    canonical_paths: Iterable[str | Path], issue_time: datetime, state_date: date, *,
    availability_mode: str, minimum_hours: int = DAILY_MIN_HOURS,
) -> tuple[dict[date, float], dict[str, Any]]:
    """Read daily values in the recent 30-date window using issue-time legality."""
    if availability_mode not in {"observation_time_proxy", "data_available_time"}:
        raise ValueError("availability_mode must be observation_time_proxy or data_available_time")
    if minimum_hours < 1 or minimum_hours > 24:
        raise ValueError("minimum_hours must be between 1 and 24")
    start = state_date - timedelta(days=WINDOW_DAYS - 1)
    hourly: dict[datetime, tuple[float, str, str, str]] = {}
    canonical_inputs: list[str] = []
    for input_path in canonical_paths:
        path = Path(input_path)
        canonical_inputs.append(str(path))
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if any(row.get(key) != value for key, value in TARGET.items()):
                    continue
                observation_time = _parse_utc(row["observation_time"], "observation_time")
                availability_raw = (row.get("data_available_time") or "").strip()
                availability_time = _parse_utc(availability_raw, "data_available_time") if availability_raw else None
                if availability_mode == "data_available_time" and availability_time is None:
                    raise ValueError("data_available_time mode requires known availability for every target observation")
                legal = observation_time <= issue_time
                if availability_mode == "data_available_time":
                    legal = legal and availability_time <= issue_time
                if not legal or not start <= observation_time.date() <= state_date:
                    continue
                if observation_time in hourly:
                    raise ValueError(f"duplicate recent timestamp across canonical inputs: {observation_time.isoformat()}")
                hourly[observation_time] = (
                    float(row["value"]), row.get("source_dataset", ""), row.get("source_file", ""), availability_raw,
                )
    grouped: dict[date, list[float]] = defaultdict(list)
    for timestamp, (value, _, _, _) in hourly.items():
        grouped[timestamp.date()].append(value)
    daily = {day: mean(values) for day, values in grouped.items() if len(values) >= minimum_hours}
    hash_rows = [
        {
            "observation_time": timestamp.isoformat(), "value": value, "source_dataset": dataset,
            "source_file": source_file, "data_available_time": availability,
        }
        for timestamp, (value, dataset, source_file, availability) in sorted(hourly.items())
    ]
    return daily, {
        "canonical_inputs": sorted(canonical_inputs),
        "recent_legal_observation_slice_sha256": _stable_hash(hash_rows),
        "source_dataset_ids": sorted({dataset for _, dataset, _, _ in hourly.values() if dataset}),
        "source_files": sorted({source_file for _, _, source_file, _ in hourly.values() if source_file}),
        "recent_legality_rule": (
            "observation_time <= forecast_issue_time"
            if availability_mode == "observation_time_proxy"
            else "observation_time and data_available_time <= forecast_issue_time"
        ),
    }


def anomaly_persistence_rows_from_daily(
    daily: dict[date, float], state_date: date, issue_year: int, *,
    minimum_training_years: int = MINIMUM_TRAINING_YEARS,
    window_minimum_valid_days: int = WINDOW_MIN_VALID_DAYS,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Calculate the recent anomaly and add it unchanged to target climatologies."""
    recent_start = state_date - timedelta(days=WINDOW_DAYS - 1)
    recent_dates = [recent_start + timedelta(days=offset) for offset in range(WINDOW_DAYS)]
    recent_values = [daily[day] for day in recent_dates if day in daily and day <= state_date]
    recent_valid = state_date in daily and len(recent_values) >= window_minimum_valid_days
    recent_mean = mean(recent_values) if recent_valid else None
    recent_climatology = historical_window_climatology(
        daily, recent_start, issue_year,
        minimum_training_years=minimum_training_years,
        window_minimum_valid_days=window_minimum_valid_days,
    )
    anomaly_valid = recent_valid and recent_climatology["is_valid"]
    current_anomaly = (
        recent_mean - recent_climatology["climatological_mean"] if anomaly_valid else None
    )
    anomaly = {
        "window_start": recent_start.isoformat(),
        "window_end": state_date.isoformat(),
        "expected_days": WINDOW_DAYS,
        "valid_days": len(recent_values),
        "completeness_fraction": len(recent_values) / WINDOW_DAYS,
        "recent_window_is_valid": recent_valid,
        "recent_30_day_mean": recent_mean,
        "historical_recent_window_climatology": recent_climatology["climatological_mean"],
        "historical_training_years": recent_climatology["training_years"],
        "n_valid_historical_years": recent_climatology["n_valid_training_years"],
        "historical_training_windows": recent_climatology["training_windows"],
        "historical_training_windows_sha256": recent_climatology["training_windows_sha256"],
        "unavailable_february_29_start_years": recent_climatology["unavailable_february_29_start_years"],
        "is_valid": anomaly_valid,
        "current_anomaly": current_anomaly,
        "unit": "degC",
    }
    target_climatologies = forecast_rows_from_daily(
        daily, state_date, issue_year,
        minimum_training_years=minimum_training_years,
        window_minimum_valid_days=window_minimum_valid_days,
    )
    forecasts: list[dict[str, Any]] = []
    for target in target_climatologies:
        valid = anomaly_valid and target["is_valid"]
        target_climatology = target["predicted_mean_temperature"]
        forecasts.append({
            **target,
            "model_identifier": MODEL_ID,
            "target_climatology_mean": target_climatology,
            "current_anomaly": current_anomaly,
            "is_valid": valid,
            "predicted_mean_temperature": target_climatology + current_anomaly if valid else None,
            "forecast_equation": "target_window_climatology + current_anomaly (coefficient fixed at 1.0)",
        })
    return anomaly, forecasts


def forecast_anomaly_persistence(
    canonical_paths: Iterable[str | Path], forecast_state: dict[str, Any] | str | Path, *,
    minimum_training_years: int = MINIMUM_TRAINING_YEARS,
    daily_minimum_hours: int = DAILY_MIN_HOURS,
    window_minimum_valid_days: int = WINDOW_MIN_VALID_DAYS,
) -> dict[str, Any]:
    """Forecast all horizons by adding one unfitted current anomaly to climatology."""
    if minimum_training_years < 1:
        raise ValueError("minimum_training_years must be positive")
    if window_minimum_valid_days < 1 or window_minimum_valid_days > WINDOW_DAYS:
        raise ValueError("window_minimum_valid_days must be between 1 and 30")
    state, state_input = _load_state(forecast_state)
    state_date, issue_time = _validate_state(state)
    availability_mode = state.get("availability_mode")
    paths = list(canonical_paths)
    training_daily, training_provenance = read_training_daily_temperature(
        paths, issue_time.year, minimum_hours=daily_minimum_hours,
    )
    recent_daily, recent_provenance = read_recent_legal_daily_temperature(
        paths, issue_time, state_date,
        availability_mode=availability_mode, minimum_hours=daily_minimum_hours,
    )
    if state_date not in recent_daily:
        raise ValueError("forecast state's last complete daily date is not complete in the legally available canonical observations")
    daily = {**training_daily, **recent_daily}
    anomaly, forecasts = anomaly_persistence_rows_from_daily(
        daily, state_date, issue_time.year,
        minimum_training_years=minimum_training_years,
        window_minimum_valid_days=window_minimum_valid_days,
    )
    return {
        "component": "forecast_anomaly_persistence",
        "workflow_role": "forecast_side_only",
        "model": {
            "identifier": MODEL_ID, "version": MODEL_VERSION,
            "configuration": {
                "anomaly_coefficient": 1.0,
                "coefficient_fitted": False,
                "minimum_training_years": minimum_training_years,
                "daily_minimum_hours": daily_minimum_hours,
                "window_minimum_valid_days": window_minimum_valid_days,
            },
        },
        "forecast_issue_time": issue_time.isoformat(),
        "forecast_state_date": state_date.isoformat(),
        "availability_mode": availability_mode,
        "availability_assumption": state.get("availability_assumption"),
        "current_state_anomaly": anomaly,
        "training_year_rule": f"only observations with calendar year < issue year {issue_time.year} enter climatologies",
        "forecast_target_input_used": False,
        "forecasts": forecasts,
        "provenance": {
            **state_input,
            "canonical_inputs": training_provenance["canonical_inputs"],
            "eligible_training_observation_slice_sha256": training_provenance["eligible_training_observation_slice_sha256"],
            "recent_legal_observation_slice_sha256": recent_provenance["recent_legal_observation_slice_sha256"],
            "source_dataset_ids": sorted(set(training_provenance["source_dataset_ids"] + recent_provenance["source_dataset_ids"])),
            "source_files": sorted(set(training_provenance["source_files"] + recent_provenance["source_files"])),
            "recent_legality_rule": recent_provenance["recent_legality_rule"],
            "forecast_record_sha256": _stable_hash({"current_state_anomaly": anomaly, "forecasts": forecasts}),
            "source_timestamps_preserved": True,
            "leakage_note": "Future observations are excluded from the recent state, and issue-year/later observations are excluded from both climatologies before aggregation and hashing.",
        },
    }


def write_anomaly_persistence_forecast(result: dict[str, Any], output_path: str | Path) -> None:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
