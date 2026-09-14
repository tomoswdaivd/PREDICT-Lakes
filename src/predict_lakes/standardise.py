"""Read EIDC buoy CSV releases into a traceable canonical long table."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

CANONICAL_FIELDS = [
    "lake_id", "basin", "observation_time", "data_available_time", "variable",
    "depth_m", "value", "unit", "source_dataset", "source_file", "source_row", "qc_status",
]

_VARIABLES = {
    "water temperature": ("water_temperature", "degC"),
    "water_temperature": ("water_temperature", "degC"),
    "air temperature": ("air_temperature", "degC"),
    "air_temperature": ("air_temperature", "degC"),
    "pyranometer": ("solar_irradiance", "W m-2"),
    "wind speed": ("wind_speed", "m s-1"),
    "wind_speed": ("wind_speed", "m s-1"),
}


def _normalise_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower().replace("_", " "))


def _parse_time(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%d/%m/%Y %H:%M").replace(tzinfo=timezone.utc)


def _depth(value: str) -> float | None:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*m\s*", value or "")
    return float(match.group(1)) if match else None


def read_observations(path: str | Path, dataset_id: str, *, data_available_time: str | None = None) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Return canonical records and a small source inventory for one EIDC CSV."""
    source = Path(path)
    with source.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
            subheader = next(reader)
        except StopIteration as exc:
            raise ValueError("EIDC CSV must contain two header rows") from exc
        if len(header) != len(subheader):
            raise ValueError("header rows have different widths")
        rows = [row for row in reader if row and row[0].strip()]

    columns = list(zip(header, subheader))
    records: list[dict[str, str]] = []
    missing = Counter()
    timestamps: list[datetime] = []
    for row_number, row in enumerate(rows, start=3):
        if len(row) != len(columns):
            raise ValueError(f"row {row_number} has {len(row)} fields; expected {len(columns)}")
        timestamp = _parse_time(row[0])
        timestamps.append(timestamp)
        for index, ((raw_variable, raw_depth), raw_value) in enumerate(zip(columns[1:], row[1:]), start=1):
            value = raw_value.strip()
            key = f"{raw_variable}:{raw_depth}" if raw_depth else raw_variable
            if not value:
                missing[key] += 1
                continue
            variable, unit = _VARIABLES.get(_normalise_name(raw_variable), (_normalise_name(raw_variable), ""))
            records.append({
                "lake_id": "windermere",
                "basin": "south",
                "observation_time": timestamp.isoformat(),
                "data_available_time": data_available_time or "",
                "variable": variable,
                "depth_m": "" if variable != "water_temperature" else str(_depth(raw_depth)),
                "value": value,
                "unit": unit,
                "source_dataset": dataset_id,
                "source_file": source.name,
                "source_row": str(row_number),
                "qc_status": "",
            })

    duplicate_count = len(timestamps) - len(set(timestamps))
    inventory = {
        "dataset_id": dataset_id,
        "source_file": source.name,
        "source_format": "CSV with two header rows",
        "row_count": len(rows),
        "observation_start": min(timestamps).isoformat() if timestamps else None,
        "observation_end": max(timestamps).isoformat() if timestamps else None,
        "duplicate_timestamp_count": duplicate_count,
        "timezone": "UTC (source labels this GMT)",
        "nominal_sampling": "hourly averages from measurements every four minutes",
        "depths_m": sorted({_depth(d) for variable, d in columns if _normalise_name(variable) == "water temperature" and _depth(d) is not None}),
        "variables": sorted({(_VARIABLES.get(_normalise_name(variable), (_normalise_name(variable), ""))[0]) for variable, _ in columns[1:]}),
        "missing_value_counts": dict(missing),
        "missingness_denominator": len(rows),
        "data_available_time": data_available_time,
        "data_available_time_note": "Not supplied by the historical EIDC archive; not fabricated.",
    }
    return records, inventory


def write_canonical(records: Iterable[dict[str, str]], output_path: str | Path) -> None:
    """Write canonical records as UTF-8 CSV."""
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CANONICAL_FIELDS)
        writer.writeheader()
        writer.writerows(records)


def write_inventory(inventory: dict[str, Any], output_path: str | Path) -> None:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
