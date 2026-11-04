"""Gap detection for the daily tabs: which days is a sheet missing, and how far back must the daily queries look?

Each daily block keeps the date of the last day it was filled in its own stamp cell (the newest column is always the first one, D).
From that one cell the run works out how many days are missing, sizes the daily queries' lookback window to cover them, and later
writes one new column per missing day, oldest first. This is what lets a run that was skipped for a few days catch up on
its own the next time it runs.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

from .logging_utils import LOG
from .retry import with_retry

DEFAULT_DAYS = 2   # assumed when a sheet's date cannot be read: the safe, small window
MAX_DAYS = 90      # sanity ceiling: a garbage date must not turn into an enormous query


def parse_sheet_date(value) -> date | None:
    """A cell value as a date, or None if it is empty or unrecognised (for example the very first run).

    Handles the 'YYYY-MM-DD' text the run writes, a 'DD/MM/YYYY' date typed by a person, and the serial number Sheets
    returns when it has turned the text into a real date (read back with UNFORMATTED_VALUE).
    """
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return (datetime(1899, 12, 30) + timedelta(days=value)).date()
        except (OverflowError, OSError):
            return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def read_stamp(sheet, cell: str, retry_kwargs: dict | None = None):
    return with_retry(sheet.acell, cell, value_render_option="UNFORMATTED_VALUE", **(retry_kwargs or {})).value


def compute_lookback_days(spreadsheet, targets, yesterday: date, retry_kwargs: dict | None = None) -> int:
    """How many days of history the daily queries must pull to cover every day missing in any daily block.
    `targets` is a list of (tab name, stamp cell).

    The floor is 1 day. If a sheet's date cannot be read, that sheet is assumed to be DEFAULT_DAYS behind. If the
    spreadsheet cannot be reached at all, the whole answer falls back to DEFAULT_DAYS, so a normal run still goes ahead.
    The result is capped at MAX_DAYS.
    """
    try:
        gaps = [1]
        for name, cell in targets:
            ws = with_retry(spreadsheet.worksheet, name, **(retry_kwargs or {}))
            raw = read_stamp(ws, cell, retry_kwargs)
            last_covered = parse_sheet_date(raw)
            if last_covered is None:
                LOG.warning("Could not read a recognized date from %s's %s ('%s') to size the daily lookback window; assuming %d day(s) missing there",
                            name, cell, raw, DEFAULT_DAYS)
                gaps.append(DEFAULT_DAYS)
                continue
            gaps.append(max((yesterday - last_covered).days, 0))
        lookback = max(gaps)
        if lookback > MAX_DAYS:
            LOG.warning("Detected a %d-day gap, more than the %d-day sanity ceiling; capping the daily lookback window to %d days",
                        lookback, MAX_DAYS, MAX_DAYS)
            lookback = MAX_DAYS
        LOG.info("Daily queries will look back %d day(s), based on each sheet's last-filled date", lookback)
        return lookback
    except Exception as exc:
        LOG.warning("Could not determine the actual backfill gap from the spreadsheet (%s); defaulting the daily lookback window to %d day(s)",
                    exc, DEFAULT_DAYS)
        return DEFAULT_DAYS


def get_missing_days(sheet, stamp_cell: str, up_to_day: date, lookback_days: int, retry_kwargs: dict | None = None) -> list[date]:
    """The days this sheet still needs, oldest first: from the day after its last-filled date up to `up_to_day`.

    If the date cannot be read, only `up_to_day` is returned (the old single-day behaviour). If more days are missing than
    the lookback window covers, only the most recent ones that the queries actually returned are kept.
    """
    try:
        raw = read_stamp(sheet, stamp_cell, retry_kwargs)
    except Exception as exc:
        LOG.warning("Could not read %s's %s to detect missing days (%s); writing only %s", sheet.title, stamp_cell, exc, up_to_day)
        return [up_to_day]
    last_covered = parse_sheet_date(raw)
    if last_covered is None:
        LOG.warning("%s's %s ('%s') isn't a recognized date; writing only %s", sheet.title, stamp_cell, raw, up_to_day)
        return [up_to_day]
    if last_covered >= up_to_day:
        LOG.info("%s is already up to date through %s; nothing to backfill", sheet.title, last_covered)
        return []
    missing, day = [], last_covered + timedelta(days=1)
    while day <= up_to_day:
        missing.append(day)
        day += timedelta(days=1)
    if len(missing) > lookback_days:
        LOG.warning("%s is missing %d day(s), more than the %d-day lookback window covers; only the most recent %d day(s) will be backfilled this run",
                    sheet.title, len(missing), lookback_days, lookback_days)
        missing = missing[-lookback_days:]
    return missing


def filter_by_day(df: pd.DataFrame, day: date) -> pd.DataFrame:
    """The rows of a daily frame whose first column is `day`, in their original order (for example Store 1 before Store 2).

    Column 0 is normally already an ISO 'YYYY-MM-DD' string, but if that cleanup ever failed and left real datetimes, or the
    strict parse finds nothing, a permissive parse is tried instead of silently treating every row as a non-match, which
    would drop the frame's data for every day.
    """
    if df.empty:
        return df
    col0 = df.iloc[:, 0]
    if pd.api.types.is_datetime64_any_dtype(col0):
        parsed = col0
    else:
        parsed = pd.to_datetime(col0, format="%Y-%m-%d", errors="coerce")
        if parsed.isna().all():
            parsed = pd.to_datetime(col0, errors="coerce")
    return df.loc[parsed.dt.date == day].reset_index(drop=True)
