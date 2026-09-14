"""Build leakage-safe daily forecast states from canonical observations."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

DAILY_MIN_HOURS = 18
TARGET = {"lake_id": "windermere", "basin": "south", "variable": "water_temperature", "depth_m": "2.0"}


def _parse_time(value: str, field: str) -> datetime:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp: {value!r}") from exc
    if timestamp.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return timestamp.astimezone(timezone.utc)


def _issue_time(value: str | datetime) -> datetime:
    if isinstance(value, str):
        return _parse_time(value, "forecast_issue_time")
    if value.tzinfo is None:
        raise ValueError("forecast_issue_time must include a timezone")
    return value.astimezone(timezone.utc)


def _read_legal_observations(paths: Iterable[str | Path], issue_time: datetime, availability_mode: str) -> tuple[dict[datetime, float], dict[str, Any]]:
    if availability_mode not in {"observation_time_proxy", "data_available_time"}:
        raise ValueError("availability_mode must be observation_time_proxy or data_available_time")
    observations: dict[datetime, float] = {}
    source_datasets: set[str] = set()
    source_files: set[str] = set()
    canonical_inputs: list[str] = []
    legal_provenance_rows: list[dict[str, str]] = []
    for input_path in paths:
        path = Path(input_path)
        canonical_inputs.append(str(path))
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if any(row.get(key) != value for key, value in TARGET.items()):
                    continue
                observation_time = _parse_time(row["observation_time"], "observation_time")
                availability_raw = (row.get("data_available_time") or "").strip()
                availability_time = _parse_time(availability_raw, "data_available_time") if availability_raw else None
                if availability_mode == "data_available_time" and availability_time is None:
                    raise ValueError("data_available_time mode requires a known data_available_time for every target observation")
                legal = observation_time <= issue_time
                if availability_mode == "data_available_time":
                    legal = legal and availability_time <= issue_time
                if not legal:
                    continue
                if observation_time in observations:
                    raise ValueError(f"duplicate target timestamp across canonical inputs: {observation_time.isoformat()}")
                observations[observation_time] = float(row["value"])
                source_datasets.add(row.get("source_dataset", ""))
                source_files.add(row.get("source_file", ""))
                legal_provenance_rows.append({key: row.get(key, "") for key in ("observation_time", "data_available_time", "value", "source_dataset", "source_file", "source_row")})
    legal_payload = json.dumps(sorted(legal_provenance_rows, key=lambda row: (row["observation_time"], row["source_dataset"], row["source_row"])), sort_keys=True, separators=(",", ":")).encode("utf-8")
    legal_hash = hashlib.sha256(legal_payload).hexdigest()
    return observations, {"source_dataset_ids": sorted(source_datasets), "source_files": sorted(source_files), "canonical_inputs": canonical_inputs, "legal_observation_slice_sha256": legal_hash}


def _daily_history(observations: dict[datetime, float], minimum_hours: int) -> list[dict[str, Any]]:
    grouped: dict[date, list[tuple[datetime, float]]] = defaultdict(list)
    for timestamp, value in observations.items():
        grouped[timestamp.astimezone(timezone.utc).date()].append((timestamp, value))
    history = []
    for day in sorted(grouped):
        values = sorted(grouped[day])
        if len(values) < minimum_hours:
            continue
        history.append({
            "date": day.isoformat(),
            "temperature_2m": mean(value for _, value in values),
            "n_valid_hours": len(values),
            "first_legal_observation_time": values[0][0].isoformat(),
            "last_legal_observation_time": values[-1][0].isoformat(),
        })
    return history


def build_forecast_state(
    canonical_paths: Iterable[str | Path],
    forecast_issue_time: str | datetime,
    *,
    availability_mode: str = "observation_time_proxy",
    minimum_hours: int = DAILY_MIN_HOURS,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build a state using only observations legally available at an issue time."""
    issue_time = _issue_time(forecast_issue_time)
    if minimum_hours < 1 or minimum_hours > 24:
        raise ValueError("minimum_hours must be between 1 and 24")
    paths = list(canonical_paths)
    observations, provenance = _read_legal_observations(paths, issue_time, availability_mode)
    history = _daily_history(observations, minimum_hours)
    latest = history[-1] if history else None
    raw_latest = max(observations) if observations else None
    recent_start = latest["date"] if latest else None
    recent_days = history[-30:]
    state = {
        "component": "build_forecast_state",
        "site": {"lake_id": "windermere", "basin": "south"},
        "target": {"variable": "water_temperature", "depth_m": 2.0, "unit": "degC", "resolution": "daily", "calendar": "UTC"},
        "forecast_issue_time": issue_time.isoformat(),
        "availability_mode": availability_mode,
        "availability_assumption": ("Historical data_available_time is unknown; observation_time is used as an explicit pseudo-operational availability proxy." if availability_mode == "observation_time_proxy" else "Known data_available_time is the operational availability criterion; future observation_time is also rejected."),
        "daily_completeness_rule": f"at least {minimum_hours} distinct valid hourly observations on a UTC calendar date, using only legally available observations; no imputation",
        "last_legally_available_raw_observation_time": raw_latest.isoformat() if raw_latest else None,
        "last_complete_daily_state_date": latest["date"] if latest else None,
        "latest_valid_daily_temperature_2m": latest["temperature_2m"] if latest else None,
        "latest_valid_daily_observation_date": latest["date"] if latest else None,
        "n_valid_historical_daily_observations": len(history),
        "recent_data": {"window_days": 30, "n_valid_daily_observations": len(recent_days), "latest_history_date": recent_start, "latest_day_n_valid_hours": latest["n_valid_hours"] if latest else None},
        "history_csv": "forecast_state_history.csv",
        "provenance": {**provenance, "source_timestamps_preserved": True, "data_available_time_note": "Historical archive values are blank/unknown; no availability timestamp was fabricated."},
    }
    return state, history


def write_forecast_state(state: dict[str, Any], history: list[dict[str, Any]], output_dir: str | Path) -> None:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "forecast_state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    with (destination / "forecast_state_history.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["date", "temperature_2m", "n_valid_hours", "first_legal_observation_time", "last_legal_observation_time"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(history)
