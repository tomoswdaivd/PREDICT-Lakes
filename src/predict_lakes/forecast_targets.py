"""Build verification-side observed target windows from canonical observations."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from .window_definition import WINDOW_DAYS, target_window_bounds

DAILY_MIN_HOURS = 18
WINDOW_MIN_VALID_DAYS = 27
TARGET = {"lake_id": "windermere", "basin": "south", "variable": "water_temperature", "depth_m": "2.0"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_observation_time(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("observation_time must include a timezone")
    return timestamp.astimezone(timezone.utc)


def read_daily_target_temperature(canonical_paths: Iterable[str | Path], *, minimum_hours: int = DAILY_MIN_HOURS) -> tuple[dict[date, float], dict[str, Any]]:
    """Read and aggregate canonical 2 m observations to valid UTC daily means."""
    if minimum_hours < 1 or minimum_hours > 24:
        raise ValueError("minimum_hours must be between 1 and 24")
    hourly: dict[datetime, tuple[float, str, str]] = {}
    input_hashes: dict[str, str] = {}
    for input_path in canonical_paths:
        path = Path(input_path)
        input_hashes[str(path)] = _sha256(path)
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if any(row.get(key) != value for key, value in TARGET.items()):
                    continue
                timestamp = _parse_observation_time(row["observation_time"])
                if timestamp in hourly:
                    raise ValueError(f"duplicate target timestamp across canonical inputs: {timestamp.isoformat()}")
                hourly[timestamp] = (float(row["value"]), row.get("source_dataset", ""), row.get("source_file", ""))
    grouped: dict[date, list[tuple[datetime, float, str, str]]] = defaultdict(list)
    for timestamp, (value, dataset, source_file) in hourly.items():
        grouped[timestamp.date()].append((timestamp, value, dataset, source_file))
    daily: dict[date, float] = {}
    daily_sources: dict[str, dict[str, list[str]]] = {}
    for day, values in grouped.items():
        if len(values) < minimum_hours:
            continue
        daily[day] = mean(value for _, value, _, _ in values)
        daily_sources[day.isoformat()] = {
            "source_dataset_ids": sorted({dataset for _, _, dataset, _ in values if dataset}),
            "source_files": sorted({source_file for _, _, _, source_file in values if source_file}),
        }
    provenance = {
        "canonical_input_sha256": input_hashes,
        "source_dataset_ids": sorted({dataset for _, dataset, _ in hourly.values() if dataset}),
        "source_files": sorted({source_file for _, _, source_file in hourly.values() if source_file}),
        "daily_sources": daily_sources,
        "source_timestamps_preserved": True,
        "daily_completeness_rule": f"at least {minimum_hours} distinct valid hourly observations per UTC calendar day; no imputation",
    }
    return daily, provenance


def _load_state(forecast_state: dict[str, Any] | str | Path) -> tuple[dict[str, Any], dict[str, str]]:
    if isinstance(forecast_state, dict):
        return forecast_state, {"forecast_state_input": "in-memory", "forecast_state_sha256": "not_applicable"}
    path = Path(forecast_state)
    return json.loads(path.read_text(encoding="utf-8")), {"forecast_state_input": str(path), "forecast_state_sha256": _sha256(path)}


def _window(daily: dict[date, float], state_date: date, index: int, minimum_valid_days: int, provenance: dict[str, Any]) -> dict[str, Any]:
    start, end = target_window_bounds(state_date, index)
    dates = [start + timedelta(days=offset) for offset in range(WINDOW_DAYS)]
    valid = [(day, daily[day]) for day in dates if day in daily]
    source_days = provenance["daily_sources"]
    datasets = sorted({dataset for day, _ in valid for dataset in source_days[day.isoformat()]["source_dataset_ids"]})
    files = sorted({filename for day, _ in valid for filename in source_days[day.isoformat()]["source_files"]})
    is_valid = len(valid) >= minimum_valid_days
    return {
        "target_name": f"month_{index}",
        "lead_days": {"start": (index - 1) * WINDOW_DAYS + 1, "end": index * WINDOW_DAYS},
        "nominal_start_date": start.isoformat(),
        "nominal_end_date": end.isoformat(),
        "expected_days": WINDOW_DAYS,
        "valid_days": len(valid),
        "completeness_fraction": len(valid) / WINDOW_DAYS,
        "is_valid": is_valid,
        "observed_mean_temperature": mean(value for _, value in valid) if is_valid else None,
        "unit": "degC",
        "site": {"lake_id": "windermere", "basin": "south"},
        "depth_m": 2.0,
        "source_dataset_ids": datasets,
        "source_files": files,
    }


def build_forecast_targets(
    canonical_paths: Iterable[str | Path],
    forecast_state: dict[str, Any] | str | Path,
    *,
    daily_minimum_hours: int = DAILY_MIN_HOURS,
    window_minimum_valid_days: int = WINDOW_MIN_VALID_DAYS,
) -> dict[str, Any]:
    """Create three non-overlapping observed 30-day means after the state date."""
    if window_minimum_valid_days < 1 or window_minimum_valid_days > WINDOW_DAYS:
        raise ValueError("window_minimum_valid_days must be between 1 and 30")
    state, state_provenance = _load_state(forecast_state)
    state_date_raw = state.get("last_complete_daily_state_date")
    if not state_date_raw:
        raise ValueError("forecast state has no last_complete_daily_state_date")
    expected_site = {"lake_id": "windermere", "basin": "south"}
    expected_target = {"variable": "water_temperature", "depth_m": 2.0, "unit": "degC", "resolution": "daily", "calendar": "UTC"}
    if state.get("site") != expected_site or state.get("target") != expected_target:
        raise ValueError("forecast state must describe daily Windermere South Basin 2 m water temperature in degC/UTC")
    state_date = date.fromisoformat(state_date_raw)
    daily, provenance = read_daily_target_temperature(canonical_paths, minimum_hours=daily_minimum_hours)
    windows = [_window(daily, state_date, index, window_minimum_valid_days, provenance) for index in (1, 2, 3)]
    return {
        "component": "build_forecast_targets",
        "workflow_role": "verification_side_only",
        "forecast_issue_time": state.get("forecast_issue_time"),
        "forecast_state_date": state_date.isoformat(),
        "target_definition": "three non-overlapping future 30-day mean 2 m water-temperature lead windows anchored after forecast_state_date",
        "window_completeness_rule": f"at least {window_minimum_valid_days} of 30 valid daily means (each based on at least {daily_minimum_hours} hourly observations); no imputation",
        "not_for_forecast_model_input": True,
        "targets": windows,
        "provenance": {
            **state_provenance,
            "forecast_state_availability_mode": state.get("availability_mode"),
            "canonical_input_sha256": provenance["canonical_input_sha256"],
            "source_dataset_ids": provenance["source_dataset_ids"],
            "source_files": provenance["source_files"],
            "source_timestamps_preserved": True,
            "separation_note": "Future target observations are verification data and are not part of forecast state or forecasting inputs.",
        },
    }


def analyse_target_coverage(
    canonical_paths: Iterable[str | Path],
    *,
    daily_minimum_hours: int = DAILY_MIN_HOURS,
    window_minimum_valid_days: int = WINDOW_MIN_VALID_DAYS,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Count valid target horizons for every valid daily state date in 2008-2018."""
    if window_minimum_valid_days < 1 or window_minimum_valid_days > WINDOW_DAYS:
        raise ValueError("window_minimum_valid_days must be between 1 and 30")
    daily, provenance = read_daily_target_temperature(canonical_paths, minimum_hours=daily_minimum_hours)
    candidates = sorted(day for day in daily if date(2008, 1, 1) <= day <= date(2018, 12, 31))
    rows = []
    for state_date in candidates:
        windows = [_window(daily, state_date, index, window_minimum_valid_days, provenance) for index in (1, 2, 3)]
        flags = [window["is_valid"] for window in windows]
        rows.append({
            "forecast_state_date": state_date.isoformat(),
            "month_1_valid": flags[0], "month_2_valid": flags[1], "month_3_valid": flags[2],
            "month_1_valid_days": windows[0]["valid_days"], "month_2_valid_days": windows[1]["valid_days"], "month_3_valid_days": windows[2]["valid_days"],
            "supports_through_month_1": flags[0],
            "supports_through_month_2": flags[0] and flags[1],
            "supports_through_month_3": all(flags),
        })
    summary = {
        "candidate_definition": "each valid 2 m daily state date from 2008-01-01 through 2018-12-31",
        "target_definition": "month_1=days +1..+30; month_2=+31..+60; month_3=+61..+90 after forecast_state_date",
        "daily_completeness_rule": provenance["daily_completeness_rule"],
        "window_completeness_rule": f"at least {window_minimum_valid_days} of 30 valid daily means; no imputation",
        "n_candidate_state_dates": len(rows),
        "nested_support_counts": {
            "month_1": sum(row["supports_through_month_1"] for row in rows),
            "months_1_2": sum(row["supports_through_month_2"] for row in rows),
            "months_1_2_3": sum(row["supports_through_month_3"] for row in rows),
        },
        "exclusive_support_counts": {
            "month_1_only": sum(row["supports_through_month_1"] and not row["supports_through_month_2"] for row in rows),
            "months_1_2_only": sum(row["supports_through_month_2"] and not row["supports_through_month_3"] for row in rows),
            "all_three": sum(row["supports_through_month_3"] for row in rows),
        },
        "provenance": {key: provenance[key] for key in ("canonical_input_sha256", "source_dataset_ids", "source_files", "source_timestamps_preserved")},
    }
    return summary, rows


def write_forecast_targets(result: dict[str, Any], output_path: str | Path) -> None:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


def write_coverage(summary: dict[str, Any], rows: list[dict[str, Any]], output_dir: str | Path) -> None:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "target_coverage_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    with (destination / "target_coverage_by_state_date.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["forecast_state_date"])
        writer.writeheader()
        writer.writerows(rows)
