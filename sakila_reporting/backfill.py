"""The daily blocks: one new column per missing day, newest on the left, written oldest day first.

Each daily block keeps the date of its last filled day in its own date cell (D3 for the daily block, D28 for the per-store block). For
every missing day this inserts a column (or two, for the per-store block) at column D, writes the day's figures and ratio formulas into
it, and writes the day's date into the stamp cell.

Both blocks live on ONE tab, one under the other, each with its own columns. A whole-column insert would shift both, so the insert is an
`insertRange` over the rows of ONE block only: it moves that block's cells to the right and nothing else. The formats of the neighbouring
column are copied onto the new one in the same request, and for the per-store block the new date cell is merged across its pair.

The control flow is deliberately defensive, because the tab itself is the only record of what has been done:

* The stamp is read BEFORE the insert, so a failed insert can be diagnosed afterwards.
* The insert is NOT retried. If the call fails, the stamp is read again. A stamp that went from a real date to blank proves the columns
  had already moved (the reply was lost), and the blank columns are removed so the next run can still read the stamp and resume from
  the right day. If nothing shifted, nothing is deleted: removing columns that hold a real day would destroy data. Removing is not
  retried either, for the same reason.
* A day with no rows from the main query is written as a real zero day ONLY if a LATER day in the same catch-up has rows (then it
  really had no activity). If no later day has rows, the data may simply not be loaded yet: stamping it would mark the day covered
  while leaving it blank, and no later run would ever fill it. The inserted columns are removed and the run stops, leaving the stamp on
  the last complete day so the next run retries. A query that FAILED (no columns at all) never produces a zero day.
* The write uses FRESH copies of the update dicts on every attempt. gspread rewrites each item's 'range' in place, so re-sending the same
  list would fail every retry after the first with a malformed range.
* Any failure removes the columns just inserted and stops that block's backfill; days already written stay.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable

import pandas as pd

from . import a1, digest
from .gap import filter_by_day, parse_sheet_date, read_stamp
from .logging_utils import LOG
from .retry import with_retry

COL = digest.FIRST_DAY_COL


def get_val(value):
    """A cell value as a JSON-serialisable Python value."""
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    try:
        return value.item()
    except AttributeError:
        return value


def safe_val(df: pd.DataFrame, row: int, col: int):
    """df.iloc[row, col], or 0 with a warning if that cell is missing (a source query returned fewer rows than expected)."""
    try:
        return get_val(df.iloc[row, col])
    except IndexError:
        LOG.warning("Missing data at row %d, col %d; using placeholder 0", row, col)
        return 0


def _frame(frames: dict, name: str) -> pd.DataFrame:
    return frames.get(name, pd.DataFrame())


def _ran(frames: dict, name: str) -> bool:
    """Did this query run? A query that ran has columns even when it returned no rows (nobody was first seen that week: a real 0); a
    query that failed arrives as an empty frame with no columns at all (its cells stay blank for a later run)."""
    return _frame(frames, name).shape[1] > 0


def _col(offset: int = 0) -> str:
    return a1.col_to_letters(COL + offset)


# ------------------------------------------------------------------------------------------------ one day, one column
def _d(kpi: str) -> int:
    """The row of a KPI in the daily block."""
    return digest.D_FIRST + digest.DAILY_KPIS.index(kpi)


def daily_updates(day: date, frames: dict) -> list[dict]:
    """The writes for the daily block (column D), for one day."""
    c = _col()
    updates = []
    main = filter_by_day(_frame(frames, "daily"), day)
    if not main.empty:
        # the date, then Rentals .. Unique Categories Rented (one contiguous run), then Unreturned Opened, Largest Payment, Paying Customers
        updates.append({"range": f"{c}{digest.D_DATE}:{c}{_d('Unique Categories Rented')}", "values": [[safe_val(main, 0, i)] for i in range(0, 8)]})
        updates.append({"range": f"{c}{_d('Unreturned Opened')}:{c}{_d('Paying Customers')}", "values": [[safe_val(main, 0, i)] for i in range(8, 11)]})
    elif _ran(frames, "daily"):  # a day with no rentals at all: every figure is a real 0
        updates.append({"range": f"{c}{_d('Rentals')}:{c}{_d('Unique Categories Rented')}", "values": [[0]] * 7})
        updates.append({"range": f"{c}{_d('Unreturned Opened')}:{c}{_d('Paying Customers')}", "values": [[0]] * 3})
    cross = filter_by_day(_frame(frames, "daily_cross_store_customers"), day)
    if not cross.empty:
        updates.append({"range": f"{c}{_d('Cross-Store Customers')}", "values": [[safe_val(cross, 0, 1)]]})
    first = filter_by_day(_frame(frames, "daily_first_seen"), day)
    if not first.empty or _ran(frames, "daily_first_seen"):  # no row for the day: nobody was first seen, a real 0
        updates.append({"range": f"{c}{_d('New Customers At Home Store')}:{c}{_d('New Customers Cross-Store')}",
                        "values": [[safe_val(first, 0, i)] for i in range(1, 3)] if not first.empty else [[0], [0]]})
    returns = returns_values(day, _frame(frames, "daily_returns"))
    if returns is not None:
        updates.append({"range": f"{c}{_d('Returned')}:{c}{_d('Returned Late')}", "values": [[v] for v in returns]})
    updates.append({"range": f"{c}{digest.D_DATE}", "values": [[day.strftime("%Y-%m-%d")]]})  # the stamp the next run reads
    updates += [
        {"range": f"{c}{_d('Revenue per Rental')}", "values": [[f"=IFERROR({c}{_d('Revenue')}/{c}{_d('Rentals')},0)"]]},
        {"range": f"{c}{_d('Rentals per Active Customer')}", "values": [[f"=IFERROR({c}{_d('Rentals')}/{c}{_d('Active Customers')},0)"]]},
        {"range": f"{c}{_d('Revenue per Paying Customer')}", "values": [[f"=IFERROR({c}{_d('Revenue')}/{c}{_d('Paying Customers')},0)"]]},
        {"range": f"{c}{_d('New Customers Share of Active')}",
         "values": [[f"=IFERROR(({c}{_d('New Customers At Home Store')}+{c}{_d('New Customers Cross-Store')})/{c}{_d('Active Customers')},0)"]]},
    ]
    return updates


def returns_values(day: date, df: pd.DataFrame):
    """Returned / on time / late for ONE day.

    None when the query failed (no columns at all), so the cells are left blank for a later run instead of being filled with
    false zeros. A day that is simply absent from a frame that ran had no returns, which is a genuine 0.
    """
    if df.shape[1] == 0:
        return None
    d = filter_by_day(df, day)
    if d.empty:
        return [0, 0, 0]
    return [safe_val(d, 0, i) for i in range(1, 4)]


def _store_row(df: pd.DataFrame, store: str) -> pd.DataFrame:
    """The row for one store, matched BY NAME: a day on which only one store appears returns one row, and reading by
    position would put that store's numbers under the other store's column. A frame with no columns at all (its query failed)
    has no row for any store."""
    if df.shape[1] < 2:
        return df
    return df[df.iloc[:, 1] == store].reset_index(drop=True)


def _s(kpi: str) -> int:
    """The row of a KPI in the per-store block."""
    return digest.S_FIRST + digest.DAILY_KPIS.index(kpi)


def daily_stores_updates(day: date, frames: dict) -> list[dict]:
    """The writes for the per-store block (column D = Store 1, column E = Store 2), for one day. The date is written once, into the
    first column of the pair; the cells of a day are merged across it."""
    main = filter_by_day(_frame(frames, "daily_stores"), day)
    cross = filter_by_day(_frame(frames, "daily_stores_cross_store_customers"), day)
    first = filter_by_day(_frame(frames, "daily_stores_first_seen"), day)
    returns = filter_by_day(_frame(frames, "daily_stores_returns"), day)
    have_returns = _ran(frames, "daily_stores_returns")
    updates = [{"range": f"{_col()}{digest.S_DATE}", "values": [[day.strftime("%Y-%m-%d")]]},  # the stamp the next run reads
               {"range": f"{_col()}{digest.S_NAMES}:{_col(1)}{digest.S_NAMES}", "values": [list(digest.STORE_LABELS)]}]
    for offset, store in enumerate(digest.STORES):
        c = _col(offset)
        m = _store_row(main, store)
        # a store with no rentals that day genuinely has zeros, not blanks
        values = [safe_val(m, 0, i) for i in (2, 3, 4, 5, 6, 7, 8)] if not m.empty else [0] * 7
        updates.append({"range": f"{c}{_s('Rentals')}:{c}{_s('Unique Categories Rented')}", "values": [[v] for v in values]})
        updates.append({"range": f"{c}{_s('Unreturned Opened')}:{c}{_s('Paying Customers')}",
                        "values": [[safe_val(m, 0, i)] for i in (9, 10, 11)] if not m.empty else [[0], [0], [0]]})
        x = _store_row(cross, store)
        if _ran(frames, "daily_stores_cross_store_customers"):
            updates.append({"range": f"{c}{_s('Cross-Store Customers')}", "values": [[safe_val(x, 0, 2) if not x.empty else 0]]})
        f = _store_row(first, store)
        if _ran(frames, "daily_stores_first_seen"):
            updates.append({"range": f"{c}{_s('New Customers At Home Store')}:{c}{_s('New Customers Cross-Store')}",
                            "values": [[safe_val(f, 0, i)] for i in (2, 3)] if not f.empty else [[0], [0]]})
        if have_returns:
            r = _store_row(returns, store)
            updates.append({"range": f"{c}{_s('Returned')}:{c}{_s('Returned Late')}",
                            "values": [[safe_val(r, 0, i)] for i in (2, 3, 4)] if not r.empty else [[0], [0], [0]]})
        updates += [
            {"range": f"{c}{_s('Revenue per Rental')}", "values": [[f"=IFERROR({c}{_s('Revenue')}/{c}{_s('Rentals')},0)"]]},
            {"range": f"{c}{_s('Rentals per Active Customer')}", "values": [[f"=IFERROR({c}{_s('Rentals')}/{c}{_s('Active Customers')},0)"]]},
            {"range": f"{c}{_s('Revenue per Paying Customer')}", "values": [[f"=IFERROR({c}{_s('Revenue')}/{c}{_s('Paying Customers')},0)"]]},
            {"range": f"{c}{_s('New Customers Share of Active')}",
             "values": [[f"=IFERROR(({c}{_s('New Customers At Home Store')}+{c}{_s('New Customers Cross-Store')})/{c}{_s('Active Customers')},0)"]]},
        ]
    return updates


@dataclass(frozen=True)
class DayBlock:
    name: str
    tab: str
    date_row: int        # the row holding the day's date: its first column is the stamp
    first_row: int       # the rows that move when a day is inserted: from here ...
    last_row: int        # ... to here
    n_columns: int       # columns per day
    main_frame: str
    build: Callable[[date, dict], list[dict]]
    merge_dates: bool = False

    @property
    def stamp(self) -> str:
        return f"{_col()}{self.date_row}"


DAILY = DayBlock("Daily", digest.TAB, digest.D_DATE, digest.D_DATE, digest.D_LAST, 1, "daily", daily_updates)
DAILY_STORES = DayBlock("Daily by Store", digest.TAB, digest.S_DATE, digest.S_DATE, digest.S_LAST, 2, "daily_stores", daily_stores_updates, merge_dates=True)
DAY_BLOCKS = (DAILY, DAILY_STORES)

DAILY_FRAMES = ("daily", "daily_cross_store_customers", "daily_first_seen", "daily_returns",
                "daily_stores", "daily_stores_cross_store_customers", "daily_stores_first_seen", "daily_stores_returns")


@dataclass
class BackfillResult:
    tab: str
    written: list[date] = field(default_factory=list)
    stopped: str | None = None  # why the backfill stopped early, if it did


def _range(sheet, block: DayBlock, first_col: int, n: int) -> dict:
    return {"sheetId": sheet.id, "startRowIndex": block.first_row - 1, "endRowIndex": block.last_row, "startColumnIndex": first_col - 1,
            "endColumnIndex": first_col - 1 + n}


def insert_day(sheet, block: DayBlock) -> None:
    """One atomic request: move the block's cells right, copy the neighbouring column's formats onto the new column(s), and merge the new
    date cell across the pair. It is not wrapped in a retry."""
    n = block.n_columns
    requests = [{"insertRange": {"range": _range(sheet, block, COL, n), "shiftDimension": "COLUMNS"}},
                {"copyPaste": {"source": _range(sheet, block, COL + n, n), "destination": _range(sheet, block, COL, n), "pasteType": "PASTE_FORMAT"}}]
    if block.merge_dates:
        merged = {"sheetId": sheet.id, "startRowIndex": block.date_row - 1, "endRowIndex": block.date_row, "startColumnIndex": COL - 1,
                  "endColumnIndex": COL - 1 + n}
        requests.append({"mergeCells": {"range": merged, "mergeType": "MERGE_ALL"}})
    sheet.spreadsheet.batch_update({"requests": requests})


def remove_day(sheet, block: DayBlock, why: str) -> None:
    """Take the columns just inserted back out (the cells to their right move back left). One attempt, never retried: a retry after a
    removal that had succeeded would remove a real day."""
    try:
        sheet.spreadsheet.batch_update({"requests": [{"deleteRange": {"range": _range(sheet, block, COL, block.n_columns), "shiftDimension": "COLUMNS"}}]})
    except Exception as exc:
        LOG.warning("Could not remove the blank column(s) %s (%s); '%s' may need a manual check for stray blank columns", why, exc, block.name)


def backfill_block(sheet, block: DayBlock, frames: dict, missing_days: list[date], retry_kwargs: dict) -> BackfillResult:
    result = BackfillResult(block.name)
    for day in missing_days:
        before = None
        try:
            before = read_stamp(sheet, block.stamp, retry_kwargs)
        except Exception:
            pass
        try:
            insert_day(sheet, block)  # deliberately NOT wrapped in with_retry
        except Exception as exc:
            LOG.warning("Could not insert column(s) for %s in '%s' (%s); stopping this block's backfill here", day, block.name, exc)
            try:
                after = read_stamp(sheet, block.stamp, retry_kwargs)
                if parse_sheet_date(before) is not None and parse_sheet_date(after) is None:
                    LOG.warning("The insert had gone through; removing the blank column(s) so the next run can still read %s and resume from the "
                                "right day", block.stamp)
                    remove_day(sheet, block, "left by the failed insert")
            except Exception as cleanup:
                LOG.warning("Could not verify/clean up after the failed insert (%s); '%s' may need a manual check for a stray blank column",
                            cleanup, block.name)
            result.stopped = "insert failed"
            break

        main = frames.get(block.main_frame, pd.DataFrame())
        if filter_by_day(main, day).empty and not (main.shape[1] > 0 and any(not filter_by_day(main, later).empty for later in missing_days if later > day)):
            LOG.warning("No source data came back for %s in '%s' and no later day has any either; leaving it uncovered so a future run retries "
                        "it, rather than marking it done with an empty column", day, block.name)
            remove_day(sheet, block, "just inserted")
            result.stopped = "no data for the day"
            break

        updates = block.build(day, frames)
        try:
            with_retry(lambda updates=updates: sheet.batch_update([dict(u) for u in updates], value_input_option="USER_ENTERED"), **retry_kwargs)
            LOG.info("'%s' updated for %s", block.name, day)
            result.written.append(day)
        except Exception as exc:
            LOG.warning("Failed to write '%s' data for %s (%s); removing the empty column(s) just inserted and stopping this block's backfill here",
                        block.name, day, exc)
            remove_day(sheet, block, "just inserted")
            result.stopped = "write failed"
            break
    if not missing_days:
        LOG.warning("No data to update for '%s'", block.name)
    return result
