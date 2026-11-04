"""The date windows every query is bound to. Sakila is a historical dataset, so instead of CURRENT_DATE the pipeline
takes an as-of date: the day the run pretends to happen. Everything is relative to it.

Two modes, because the two kinds of run want different month windows:

daily            month-to-date against the SAME PERIOD of earlier months. Only days before the as-of day-of-month are
                 counted in every month, so this month's 1st..(today-1) is compared with last month's 1st..(today-1)
                 (like-for-like). Five months, the current one included.
first-of-month   whole calendar months: the five months BEFORE the as-of month, complete. No day-of-month cut-off.
"""
from __future__ import annotations

from datetime import date, timedelta

NO_DAY_CUTOFF = 32  # a day-of-month can never reach this, so the cut-off never removes anything


def first_of_month(day: date, months_back: int = 0) -> date:
    month_index = day.year * 12 + (day.month - 1) - months_back
    return date(month_index // 12, month_index % 12 + 1, 1)


def window_params(as_of: date, mode: str, lookback_days: int) -> dict:
    """Bound parameters for every query. A parameter a query does not use is simply ignored by SQLite."""
    if mode not in ("daily", "first-of-month"):
        raise ValueError(f"unknown mode {mode!r}: use 'daily' or 'first-of-month'")
    base = {"as_of": as_of.isoformat(), "lookback_days": int(lookback_days)}
    if mode == "daily":
        return {**base,
                "m5_start": first_of_month(as_of, 4).isoformat(), "m2_start": first_of_month(as_of, 1).isoformat(),
                "win_end": as_of.isoformat(), "day_cutoff": as_of.day,
                "mtd_start": first_of_month(as_of).isoformat(), "mtd_end": as_of.isoformat(),
                "splm_start": first_of_month(as_of, 1).isoformat(), "splm_end": first_of_month(as_of).isoformat()}
    return {**base,
            "m5_start": first_of_month(as_of, 5).isoformat(), "m2_start": first_of_month(as_of, 2).isoformat(),
            "win_end": first_of_month(as_of).isoformat(), "day_cutoff": NO_DAY_CUTOFF,
            "mtd_start": first_of_month(as_of, 1).isoformat(), "mtd_end": first_of_month(as_of).isoformat(),
            "splm_start": first_of_month(as_of, 2).isoformat(), "splm_end": first_of_month(as_of, 1).isoformat()}


def yesterday(as_of: date) -> date:
    return as_of - timedelta(days=1)
