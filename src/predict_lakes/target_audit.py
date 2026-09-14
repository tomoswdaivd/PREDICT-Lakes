"""Audit whether 2 m temperature is a suitable common near-surface target."""

from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Iterable

DAILY_MIN_HOURS = 18


def _percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot calculate a percentile of no values")
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def read_target_series(paths: Iterable[str | Path], *, start: datetime, end: datetime) -> dict[float, dict[datetime, float]]:
    """Read 1 m and 2 m water temperatures from canonical CSV files."""
    series: dict[float, dict[datetime, float]] = {1.0: {}, 2.0: {}}
    for path in paths:
        with Path(path).open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if row.get("variable") != "water_temperature" or row.get("depth_m") not in {"1.0", "2.0"}:
                    continue
                timestamp = datetime.fromisoformat(row["observation_time"])
                if timestamp < start or timestamp > end:
                    continue
                depth = float(row["depth_m"])
                if timestamp in series[depth]:
                    raise ValueError(f"duplicate {depth:g}m timestamp: {timestamp.isoformat()}")
                series[depth][timestamp] = float(row["value"])
    return series


def aggregate_daily(series: dict[float, dict[datetime, float]], *, minimum_hours: int = DAILY_MIN_HOURS) -> list[dict[str, Any]]:
    """Aggregate each depth by UTC date, retaining counts and applying completeness."""
    by_day: dict[date, dict[float, list[float]]] = defaultdict(lambda: defaultdict(list))
    for depth, observations in series.items():
        for timestamp, value in observations.items():
            by_day[timestamp.astimezone(timezone.utc).date()][depth].append(value)
    daily = []
    for day in sorted(by_day):
        values = by_day[day]
        n1, n2 = len(values.get(1.0, [])), len(values.get(2.0, []))
        if n1 < minimum_hours or n2 < minimum_hours:
            continue
        daily.append({
            "date": day.isoformat(),
            "temperature_1m": mean(values[1.0]),
            "temperature_2m": mean(values[2.0]),
            "n_hours_1m": n1,
            "n_hours_2m": n2,
            "difference_1m_minus_2m": mean(values[1.0]) - mean(values[2.0]),
        })
    return daily


def _metrics(differences: list[float], first: list[float], second: list[float]) -> dict[str, float | int | None]:
    if not differences:
        return {"n": 0, "bias_1m_minus_2m": None, "mae": None, "rmse": None, "correlation": None}
    bias = mean(differences)
    correlation = None
    if len(differences) > 1 and pstdev(first) and pstdev(second):
        first_mean, second_mean = mean(first), mean(second)
        correlation = sum((a - first_mean) * (b - second_mean) for a, b in zip(first, second)) / math.sqrt(sum((a - first_mean) ** 2 for a in first) * sum((b - second_mean) ** 2 for b in second))
    return {
        "n": len(differences),
        "bias_1m_minus_2m": bias,
        "mae": mean(abs(value) for value in differences),
        "rmse": math.sqrt(mean(value * value for value in differences)),
        "correlation": correlation,
    }


def _group_metrics(daily: list[dict[str, Any]], key_function) -> dict[str, dict[str, float | int | None]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in daily:
        groups[key_function(row)].append(row)
    return {key: _metrics([r["difference_1m_minus_2m"] for r in rows], [r["temperature_1m"] for r in rows], [r["temperature_2m"] for r in rows]) for key, rows in sorted(groups.items())}


def audit_target(series: dict[float, dict[datetime, float]], *, expected_start: datetime, expected_end: datetime, minimum_hours: int = DAILY_MIN_HOURS) -> dict[str, Any]:
    """Calculate alignment, daily comparison, seasonal, annual, and warm-tail results."""
    expected = []
    timestamp = expected_start
    while timestamp <= expected_end:
        expected.append(timestamp)
        timestamp += timedelta(hours=1)
    one, two = series[1.0], series[2.0]
    expected_set = set(expected)
    one_valid, two_valid = set(one) & expected_set, set(two) & expected_set
    matched = one_valid & two_valid
    daily = aggregate_daily(series, minimum_hours=minimum_hours)
    differences = [r["difference_1m_minus_2m"] for r in daily]
    first = [r["temperature_1m"] for r in daily]
    second = [r["temperature_2m"] for r in daily]
    warm_threshold = _percentile(second, 0.9) if second else None
    warm = [r for r in daily if warm_threshold is not None and r["temperature_2m"] >= warm_threshold]
    summary = {
        "audit_period": {"start": expected_start.isoformat(), "end": expected_end.isoformat()},
        "daily_completeness_rule": f"at least {minimum_hours} distinct valid hourly observations at each depth per UTC calendar day; no imputation",
        "hourly": {
            "expected_timestamps_on_exact_hour_grid": len(expected),
            "valid_source_observations_1m": len(one), "valid_source_observations_2m": len(two),
            "source_row_missing_values_1m": len(expected) - len(one), "source_row_missing_values_2m": len(expected) - len(two),
            "matched_source_timestamps": len(set(one) & set(two)),
            "unmatched_valid_source_1m": len(set(one) - set(two)), "unmatched_valid_source_2m": len(set(two) - set(one)),
            "exact_hour_grid_valid_1m": len(one_valid), "exact_hour_grid_valid_2m": len(two_valid),
            "exact_hour_grid_missing_1m": len(expected_set - one_valid), "exact_hour_grid_missing_2m": len(expected_set - two_valid),
            "source_timestamp_minute_counts": dict(sorted(Counter(timestamp.minute for timestamp in set(one) | set(two)).items())),
            "note": "The exact-hour grid is diagnostic only; source timestamps at :59 are retained and matched exactly between depths.",
        },
        "daily": {"eligible_1m_days": len(_daily_depth_counts(series[1.0], minimum_hours)), "eligible_2m_days": len(_daily_depth_counts(series[2.0], minimum_hours)), "matched_days": len(daily)},
        "comparison": _metrics(differences, first, second),
        "difference_distribution": ({"min": min(differences), "p01": _percentile(differences, .01), "p05": _percentile(differences, .05), "median": _percentile(differences, .5), "p95": _percentile(differences, .95), "p99": _percentile(differences, .99), "max": max(differences), "sd": pstdev(differences)} if differences else {}),
        "by_month": _group_metrics(daily, lambda r: r["date"][5:7]),
        "by_year": _group_metrics(daily, lambda r: r["date"][:4]),
        "warmest_10_percent_by_2m": {"definition": "daily matched observations at or above the 90th percentile of daily 2 m temperature", "threshold_2m": warm_threshold, "metrics": _metrics([r["difference_1m_minus_2m"] for r in warm], [r["temperature_1m"] for r in warm], [r["temperature_2m"] for r in warm])},
    }
    return {"summary": summary, "daily": daily}


def _daily_depth_counts(observations: dict[datetime, float], minimum_hours: int) -> list[date]:
    counts: dict[date, int] = defaultdict(int)
    for timestamp in observations:
        counts[timestamp.astimezone(timezone.utc).date()] += 1
    return [day for day, count in counts.items() if count >= minimum_hours]


def write_audit_outputs(result: dict[str, Any], output_dir: str | Path) -> None:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "target_depth_audit_summary.json").write_text(json.dumps(result["summary"], indent=2) + "\n", encoding="utf-8")
    with (destination / "target_depth_daily_comparison.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["date", "temperature_1m", "temperature_2m", "n_hours_1m", "n_hours_2m", "difference_1m_minus_2m"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(result["daily"])


def write_audit_plot(result: dict[str, Any], output_path: str | Path) -> None:
    """Write a simple time-series and 1 m versus 2 m comparison plot."""
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("plot output requires matplotlib") from exc
    daily = result["daily"]
    dates = [datetime.fromisoformat(row["date"]) for row in daily]
    one = [row["temperature_1m"] for row in daily]
    two = [row["temperature_2m"] for row in daily]
    differences = [row["difference_1m_minus_2m"] for row in daily]
    figure, axes = plt.subplots(2, 1, figsize=(11, 8), constrained_layout=True)
    axes[0].plot(dates, one, label="1 m", linewidth=.8)
    axes[0].plot(dates, two, label="2 m", linewidth=.8)
    axes[0].set(title="Windermere South Basin daily near-surface temperature", ylabel="Temperature (°C)")
    axes[0].legend()
    axes[1].scatter(two, one, s=5, alpha=.35)
    low, high = min(two + one), max(two + one)
    axes[1].plot([low, high], [low, high], color="black", linewidth=.8)
    axes[1].set(title="Daily 1 m versus 2 m", xlabel="2 m temperature (°C)", ylabel="1 m temperature (°C)")
    figure.savefig(output_path, dpi=150)
    plt.close(figure)
