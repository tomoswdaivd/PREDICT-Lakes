"""Leakage-safe lead-dependent anomaly-relaxation forecast model."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .anomaly_persistence import (
    anomaly_persistence_rows_from_daily,
    read_recent_legal_daily_temperature,
)
from .climatology import (
    MINIMUM_TRAINING_YEARS,
    _load_state,
    _stable_hash,
    _validate_state,
    read_training_daily_temperature,
)
from .climatology_hindcast import _observed_window
from .forecast_targets import DAILY_MIN_HOURS, WINDOW_MIN_VALID_DAYS
from .window_definition import WINDOW_DAYS

MODEL_ID = "lead_dependent_anomaly_relaxation"
MODEL_VERSION = "1.0"


def build_relaxation_training_cases(
    daily: dict[date, float], coefficient_issue_year: int, *,
    minimum_climatology_years: int = MINIMUM_TRAINING_YEARS,
    window_minimum_valid_days: int = WINDOW_MIN_VALID_DAYS,
) -> list[dict[str, Any]]:
    """Build completed prior-year weekly anomaly pairs available at year start."""
    cutoff = date(coefficient_issue_year, 1, 1)
    cases: list[dict[str, Any]] = []
    for state_date in sorted(day for day in daily if day.weekday() == 0):
        case_issue_time = datetime.combine(state_date + timedelta(days=1), datetime.min.time(), timezone.utc)
        if case_issue_time.year >= coefficient_issue_year:
            continue
        anomaly, targets = anomaly_persistence_rows_from_daily(
            daily, state_date, case_issue_time.year,
            minimum_training_years=minimum_climatology_years,
            window_minimum_valid_days=window_minimum_valid_days,
        )
        if not anomaly["is_valid"]:
            continue
        for target in targets:
            target_end = date.fromisoformat(target["target_window_end"])
            if target_end >= cutoff or target["target_climatology_mean"] is None:
                continue
            index = int(target["target_name"].split("_")[1])
            observed = _observed_window(daily, state_date, index, window_minimum_valid_days)
            if not observed["is_valid"]:
                continue
            cases.append({
                "forecast_issue_time": case_issue_time.isoformat(),
                "training_year": case_issue_time.year,
                "target_name": target["target_name"],
                "current_anomaly": anomaly["current_anomaly"],
                "future_target_anomaly": observed["observed_mean_temperature"] - target["target_climatology_mean"],
                "target_window_end": target["target_window_end"],
            })
    return cases


def fit_constrained_betas(
    training_cases: Iterable[dict[str, Any]], issue_year: int, *, minimum_training_years: int = 3,
) -> dict[str, dict[str, Any]]:
    """Fit zero-intercept OLS by horizon and constrain coefficients to [0, 1]."""
    if minimum_training_years < 1:
        raise ValueError("minimum_training_years must be positive")
    eligible = [case for case in training_cases if int(case["training_year"]) < issue_year]
    results: dict[str, dict[str, Any]] = {}
    for target_name in ("month_1", "month_2", "month_3"):
        rows = [case for case in eligible if case["target_name"] == target_name]
        years = sorted({int(case["training_year"]) for case in rows})
        denominator = sum(float(case["current_anomaly"]) ** 2 for case in rows)
        numerator = sum(float(case["current_anomaly"]) * float(case["future_target_anomaly"]) for case in rows)
        raw_beta = numerator / denominator if denominator else None
        constrained_beta = min(1.0, max(0.0, raw_beta)) if raw_beta is not None else None
        results[target_name] = {
            "fitting_equation": "sum(current_anomaly * future_target_anomaly) / sum(current_anomaly^2); zero intercept",
            "n_training_rows": len(rows),
            "n_distinct_training_years": len(years),
            "training_years": years,
            "minimum_training_years": minimum_training_years,
            "raw_beta": raw_beta,
            "constrained_beta": constrained_beta,
            "constraint": "0 <= beta <= 1",
            "is_valid": len(years) >= minimum_training_years and raw_beta is not None,
            "training_cases_sha256": _stable_hash(rows),
        }
    return results


def relaxation_rows_from_daily(
    daily: dict[date, float], state_date: date, issue_year: int, *,
    minimum_training_years: int = 3,
    minimum_climatology_years: int = MINIMUM_TRAINING_YEARS,
    window_minimum_valid_days: int = WINDOW_MIN_VALID_DAYS,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Build current state, fit prior-year betas, and create three forecasts."""
    anomaly, target_rows = anomaly_persistence_rows_from_daily(
        daily, state_date, issue_year,
        minimum_training_years=minimum_climatology_years,
        window_minimum_valid_days=window_minimum_valid_days,
    )
    training_cases = build_relaxation_training_cases(
        daily, issue_year,
        minimum_climatology_years=minimum_climatology_years,
        window_minimum_valid_days=window_minimum_valid_days,
    )
    coefficients = fit_constrained_betas(
        training_cases, issue_year, minimum_training_years=minimum_training_years,
    )
    forecasts: list[dict[str, Any]] = []
    for target in target_rows:
        coefficient = coefficients[target["target_name"]]
        valid = anomaly["is_valid"] and target["target_climatology_mean"] is not None and coefficient["is_valid"]
        prediction = (
            target["target_climatology_mean"] + coefficient["constrained_beta"] * anomaly["current_anomaly"]
            if valid else None
        )
        forecasts.append({
            **target,
            "model_identifier": MODEL_ID,
            "is_valid": valid,
            "predicted_mean_temperature": prediction,
            "raw_beta": coefficient["raw_beta"],
            "constrained_beta": coefficient["constrained_beta"],
            "beta_training_rows": coefficient["n_training_rows"],
            "beta_training_years": coefficient["training_years"],
            "n_distinct_beta_training_years": coefficient["n_distinct_training_years"],
            "beta_training_cases_sha256": coefficient["training_cases_sha256"],
            "forecast_equation": "target_window_climatology + constrained_beta_h * current_anomaly; zero forecast-correction intercept",
        })
    return anomaly, coefficients, forecasts


def forecast_anomaly_relaxation(
    canonical_paths: Iterable[str | Path], forecast_state: dict[str, Any] | str | Path, *,
    minimum_training_years: int = 3,
    minimum_climatology_years: int = MINIMUM_TRAINING_YEARS,
    daily_minimum_hours: int = DAILY_MIN_HOURS,
    window_minimum_valid_days: int = WINDOW_MIN_VALID_DAYS,
) -> dict[str, Any]:
    """Fit expanding prior-year coefficients and forecast without current-target leakage."""
    if minimum_training_years < 1 or minimum_climatology_years < 1:
        raise ValueError("minimum training-year counts must be positive")
    if window_minimum_valid_days < 1 or window_minimum_valid_days > WINDOW_DAYS:
        raise ValueError("window_minimum_valid_days must be between 1 and 30")
    state, state_input = _load_state(forecast_state)
    state_date, issue_time = _validate_state(state)
    paths = list(canonical_paths)
    training_daily, training_provenance = read_training_daily_temperature(
        paths, issue_time.year, minimum_hours=daily_minimum_hours,
    )
    recent_daily, recent_provenance = read_recent_legal_daily_temperature(
        paths, issue_time, state_date,
        availability_mode=state.get("availability_mode"), minimum_hours=daily_minimum_hours,
    )
    if state_date not in recent_daily:
        raise ValueError("forecast state's last complete daily date is not complete in the legally available canonical observations")
    daily = {**training_daily, **recent_daily}
    anomaly, coefficients, forecasts = relaxation_rows_from_daily(
        daily, state_date, issue_time.year,
        minimum_training_years=minimum_training_years,
        minimum_climatology_years=minimum_climatology_years,
        window_minimum_valid_days=window_minimum_valid_days,
    )
    return {
        "component": "forecast_anomaly_relaxation",
        "workflow_role": "forecast_side_only",
        "model": {
            "identifier": MODEL_ID, "version": MODEL_VERSION,
            "configuration": {
                "one_beta_per_horizon": True, "season_specific_betas": False,
                "forecast_correction_intercept": 0.0, "beta_constraint": [0.0, 1.0],
                "minimum_beta_training_years": minimum_training_years,
                "minimum_climatology_years": minimum_climatology_years,
                "daily_minimum_hours": daily_minimum_hours,
                "window_minimum_valid_days": window_minimum_valid_days,
            },
        },
        "forecast_issue_time": issue_time.isoformat(),
        "forecast_state_date": state_date.isoformat(),
        "availability_mode": state.get("availability_mode"),
        "availability_assumption": state.get("availability_assumption"),
        "current_state_anomaly": anomaly,
        "fitted_coefficients": coefficients,
        "training_case_rule": f"weekly cases issued in years < {issue_time.year}, with target window completed before {issue_time.year}-01-01",
        "forecast_target_input_used": False,
        "forecasts": forecasts,
        "provenance": {
            **state_input,
            "canonical_inputs": training_provenance["canonical_inputs"],
            "eligible_training_observation_slice_sha256": training_provenance["eligible_training_observation_slice_sha256"],
            "recent_legal_observation_slice_sha256": recent_provenance["recent_legal_observation_slice_sha256"],
            "source_dataset_ids": sorted(set(training_provenance["source_dataset_ids"] + recent_provenance["source_dataset_ids"])),
            "source_files": sorted(set(training_provenance["source_files"] + recent_provenance["source_files"])),
            "forecast_record_sha256": _stable_hash({"current_state_anomaly": anomaly, "coefficients": coefficients, "forecasts": forecasts}),
            "leakage_note": "Coefficient cases, their targets, and climatologies predate the issue year; current issue-year targets are never model inputs.",
        },
    }


def write_anomaly_relaxation_forecast(result: dict[str, Any], output_path: str | Path) -> None:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
