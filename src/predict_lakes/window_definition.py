"""Shared, data-free definitions for forecast target windows."""

from __future__ import annotations

from datetime import date, timedelta

WINDOW_DAYS = 30


def target_window_bounds(state_date: date, index: int) -> tuple[date, date]:
    """Return inclusive bounds for month_1, month_2, or month_3."""
    if index not in (1, 2, 3):
        raise ValueError("target window index must be 1, 2, or 3")
    start = state_date + timedelta(days=(index - 1) * WINDOW_DAYS + 1)
    return start, start + timedelta(days=WINDOW_DAYS - 1)


def equivalent_window_bounds(target_start: date, training_year: int) -> tuple[date, date] | None:
    """Match start month/day in a prior year; return None for non-existent 29 February."""
    try:
        start = date(training_year, target_start.month, target_start.day)
    except ValueError:
        return None
    return start, start + timedelta(days=WINDOW_DAYS - 1)
