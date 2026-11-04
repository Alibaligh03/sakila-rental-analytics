"""Everything written to the 'Daily Digest' tab besides the daily backfill: the fixed text, the monthly blocks with their % Change
columns, and the stacked segment tables; plus the creation of the tab.

The monthly blocks write into fixed cells, looked up BY MONTH (and store), never by row position: a month with no rentals has no row in
the result, and reading by position would shift every later month one column to the left. A block is written only if its query
returned data; a month (or store) absent from a non-empty result is a real 0.

The segment tables are cleared and rewritten as a whole, so a result that got shorter never leaves old rows underneath.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from . import digest
from .backfill import get_val, safe_val
from .logging_utils import LOG
from .retry import with_retry
from .windows import first_of_month


# ------------------------------------------------------------------------------------------------ monthly blocks
@dataclass(frozen=True)
class Slot:
    """What one column of a monthly block stands for: a month (0 = the current one, 1 = the one before, ...) and, in the per-store block,
    a store."""
    letter: str
    month: int
    store: str | None = None


@dataclass(frozen=True)
class Down:
    """A month-keyed frame written DOWN each slot's column: `cols` of the row that belongs to that slot's month (and store)."""
    first_row: int
    frame: str
    cols: tuple
    slots: tuple


@dataclass(frozen=True)
class Across:
    """The snapshot frames always have one row per snapshot, so they are written by position: `rows` of one column, ACROSS a range."""
    range: str
    frame: str
    rows: tuple
    col: int
    earlier: bool = False  # the earlier-months group: only the columns of months that have activity are written


@dataclass(frozen=True)
class AcrossByMonth:
    """One value per slot from a month-keyed frame, written ACROSS a row: one cell per slot."""
    row: int
    frame: str
    col: int
    slots: tuple


@dataclass(frozen=True)
class Change:
    """A '% Change' column: current / previous - 1, for every row of a block (blank where the previous value is 0 or missing)."""
    first_row: int
    last_row: int
    current: str
    previous: str
    out: str


# total block: D = month to date, E = same period last month, F = % Change, G = a gap, H / I / J = three earlier months
_TOTAL = tuple(Slot(letter, k) for letter, k in zip("DEHIJ", range(5), strict=True))
# per-store block: each period over the two stores: D / E = month to date (Store 1, Store 2), F / G = same period last month, H / I = % Change
_STORES = (Slot("D", 0, "Store 1"), Slot("E", 0, "Store 2"), Slot("F", 1, "Store 1"), Slot("G", 1, "Store 2"))

_T = digest.M_FIRST    # the first KPI row of the total block; the snapshot row
_S = digest.MS_FIRST   # the same for the per-store block

MONTHLY_TOTAL = [
    Across(f"D{_T}:E{_T}", "customer_snapshots", (0, 1), 1), Across(f"H{_T}:J{_T}", "customer_snapshots", (2, 3, 4), 1, earlier=True),
    Down(_T + 1, "mtd_and_splm", tuple(range(1, 15)), _TOTAL),
    AcrossByMonth(_T + 15, "mtd_and_splm_cross_store_customers", 1, _TOTAL),
    AcrossByMonth(_T + 16, "mtd_and_splm_first_seen", 1, _TOTAL), AcrossByMonth(_T + 17, "mtd_and_splm_first_seen", 2, _TOTAL),
    Down(_T + 18, "mtd_and_splm_returns", (1, 2, 3, 4), _TOTAL),
    Down(_T + 22, "mtd_and_splm_open_and_closed", (1, 2), _TOTAL),
]
MONTHLY_STORES = [
    Across(f"D{_S}", "customer_snapshots_stores", (0,), 2), Across(f"E{_S}", "customer_snapshots_stores", (1,), 2),
    Across(f"F{_S}", "customer_snapshots_stores", (2,), 2), Across(f"G{_S}", "customer_snapshots_stores", (3,), 2),
    Down(_S + 1, "mtd_and_splm_stores", tuple(range(2, 16)), _STORES),
    AcrossByMonth(_S + 15, "mtd_and_splm_stores_cross_store_customers", 2, _STORES),
    AcrossByMonth(_S + 16, "mtd_and_splm_stores_first_seen", 2, _STORES), AcrossByMonth(_S + 17, "mtd_and_splm_stores_first_seen", 3, _STORES),
    Down(_S + 18, "mtd_and_splm_stores_returns", (2, 3, 4, 5), _STORES),
    Down(_S + 22, "mtd_and_splm_stores_open_and_closed", (2, 3), _STORES),
]
MONTHLY = MONTHLY_TOTAL + MONTHLY_STORES
CHANGES = [Change(digest.M_FIRST, digest.M_LAST, "D", "E", "F"),
           Change(digest.MS_FIRST, digest.MS_LAST, "D", "F", "H"), Change(digest.MS_FIRST, digest.MS_LAST, "E", "G", "I")]


def _frame(frames: dict, name: str) -> pd.DataFrame:
    return frames.get(name, pd.DataFrame())


def _month_iso(as_of: date, mode: str, offset: int) -> str:
    """The month a slot stands for. In daily mode column D is this month (month to date); in first-of-month mode the window
    is the whole months BEFORE the as-of month, so column D is last month."""
    return first_of_month(as_of, offset + (0 if mode == "daily" else 1)).isoformat()


def _row_for(df: pd.DataFrame, month: str, store: str | None):
    hit = df[df["Month"] == month]
    if store is not None:
        hit = hit[hit["Store"] == store]
    return hit.iloc[0] if not hit.empty else None


def earlier_months(frames: dict, as_of: date, mode: str) -> int:
    """How many of the three earlier months to show: the run of months right after the previous one that had any rentals. A month with no
    activity (or one after such a gap) gets no column, rather than a column of zeros. If the monthly query failed, all three stay."""
    df = _frame(frames, "mtd_and_splm")
    if df.empty:
        return 3
    shown = 0
    for k in (2, 3, 4):
        row = _row_for(df, _month_iso(as_of, mode, k), None)
        if row is None or not row["Rentals"] > 0:
            break
        shown += 1
    return shown


def month_updates(blocks, frames: dict, as_of: date, mode: str) -> list[dict]:
    updates = []
    shown = earlier_months(frames, as_of, mode)
    for block in blocks:
        df = _frame(frames, block.frame)
        if df.empty:
            continue
        if isinstance(block, Across):
            values = [safe_val(df, i, block.col) for i in block.rows]
            if block.earlier:
                values = [v if k < shown else "" for k, v in enumerate(values)]
            updates.append({"range": block.range, "values": [values]})
            continue
        for slot in block.slots:
            row = _row_for(df, _month_iso(as_of, mode, slot.month), slot.store)
            unused = slot in _TOTAL and slot.month >= 2 + shown  # an earlier month with no column: cleared, so nothing stale is left
            # a month (or store) that is simply absent from a non-empty frame had no activity in the window: that is a real 0
            if isinstance(block, Down):
                values = [[""] if unused else [get_val(row.iloc[i]) if row is not None else 0] for i in block.cols]
                updates.append({"range": f"{slot.letter}{block.first_row}:{slot.letter}{block.first_row + len(block.cols) - 1}", "values": values})
            else:
                value = "" if unused else get_val(row.iloc[block.col]) if row is not None else 0
                updates.append({"range": f"{slot.letter}{block.row}", "values": [[value]]})
    return updates


def change_updates() -> list[dict]:
    """The % Change formulas. They are written every run (not only for new data) so a block whose own query failed still shows them."""
    out = []
    for c in CHANGES:
        formulas = [[f'=IFERROR({c.current}{r}/{c.previous}{r}-1,"")'] for r in range(c.first_row, c.last_row + 1)]
        out.append({"range": f"{c.out}{c.first_row}:{c.out}{c.last_row}", "values": formulas})
    return out


def period_labels(as_of: date, mode: str) -> list[str]:
    """Column headings for the five periods: 'MTD Aug 2005', 'SPLM Jul 2005', ... in daily mode; plain month names in first-of-month mode."""
    def month(k):
        return first_of_month(as_of, k + (0 if mode == "daily" else 1)).strftime("%b %Y")
    if mode == "daily":
        return [f"MTD {month(0)}"] + [f"SPLM {month(k)}" for k in range(1, 5)]
    return [month(k) for k in range(5)]


def header_updates(as_of: date, mode: str, shown: int = 3) -> list[dict]:
    p = period_labels(as_of, mode)
    earlier = [label if k < shown else "" for k, label in enumerate(p[2:])]  # only the earlier months that have a column
    return [{"range": f"D{digest.M_HEAD}:F{digest.M_HEAD}", "values": [[p[0], p[1], "% Change"]]},
            {"range": f"H{digest.M_HEAD}:J{digest.M_HEAD}", "values": [earlier]},
            # the per-store block: each period heading is merged over its two stores (D:E, F:G, H:I), the store names repeat under them
            {"range": f"D{digest.MS_HEAD}", "values": [[p[0]]]}, {"range": f"F{digest.MS_HEAD}", "values": [[p[1]]]},
            {"range": f"H{digest.MS_HEAD}", "values": [["% Change"]]},
            {"range": f"D{digest.MS_NAMES}:I{digest.MS_NAMES}", "values": [list(digest.STORE_LABELS) * 3]}]


def write_month_tab(spreadsheet, title: str, blocks, frames: dict, retry_kwargs: dict, as_of: date, mode: str) -> bool:
    """The fixed text, the period headings, the % Change formulas and every monthly block that has data."""
    sheet = with_retry(spreadsheet.worksheet, title, **retry_kwargs)
    data = month_updates(blocks, frames, as_of, mode)
    updates = digest.static_cells() + header_updates(as_of, mode, earlier_months(frames, as_of, mode)) + change_updates() + data
    with_retry(lambda: sheet.batch_update([dict(u) for u in updates], value_input_option="USER_ENTERED"), **retry_kwargs)
    if data:
        LOG.info("'%s' monthly blocks updated successfully", title)
        return True
    LOG.warning("No monthly data to update in '%s'", title)
    return False


# ------------------------------------------------------------------------------------------------ segments
SEGMENT_SPECS = [
    ("activity_segments", "Customers by Rentals per Month (all stores)"),
    ("activity_segments_stores", "Customers by Rentals per Month (by store)"),
    ("country_split", "Top Customer Countries per Month"),
    ("rating_split", "Rentals by Film Rating per Month"),
]


def df_to_values(df: pd.DataFrame) -> list[list]:
    def sanitize(value):
        if isinstance(value, pd.Timestamp):
            return value.strftime("%Y-%m-%d")
        try:
            return value.item()
        except AttributeError:
            return value

    return [df.columns.tolist()] + [[sanitize(v) for v in row] for row in df.values.tolist()]


def write_segments(spreadsheet, title: str, frames: dict, retry_kwargs: dict) -> bool:
    """Every segment table with data, stacked under the Segments title with its own title row, written after clearing the whole area."""
    sheet = with_retry(spreadsheet.worksheet, title, **retry_kwargs)
    layout = digest.segment_layout(frames, SEGMENT_SPECS)
    if not layout:
        LOG.warning("No segment data to update in '%s'", title)
        return False
    updates = []
    for frame, text, row in layout:
        updates.append({"range": f"A{row}", "values": [[text]]})
        updates.append({"range": f"B{row + 1}", "values": df_to_values(frames[frame])})
    with_retry(sheet.batch_clear, [f"A{digest.SEG_FIRST}:Z"], **retry_kwargs)  # full replace, not an append
    with_retry(lambda: sheet.batch_update([dict(u) for u in updates], value_input_option="USER_ENTERED"), **retry_kwargs)
    LOG.info("'%s' segment tables updated successfully", title)
    return True


# ------------------------------------------------------------------------------------------------ creating the tab
def _is_missing_tab(exc: Exception) -> bool:
    return exc.__class__.__name__ == "WorksheetNotFound"


def ensure_tabs(spreadsheet, tab_names: list[str], retry_kwargs: dict) -> list[str]:
    """Create any missing tab. Existing tabs are never touched, so a hand-formatted tab stays as it is. The fixed text is written on
    every run by write_month_tab, so a new tab needs nothing but to exist."""
    created = []
    for title in tab_names:
        try:
            with_retry(spreadsheet.worksheet, title, **retry_kwargs)
            continue
        except Exception as exc:
            if not _is_missing_tab(exc):
                raise
        spreadsheet.add_worksheet(title=title, rows=digest.SEG_FIRST + 400, cols=30)
        created.append(title)
        LOG.info("Created tab '%s'", title)
    return created


ALL_NEW_TABS = [digest.TAB]


def used_frames() -> set[str]:
    """Every query result the digest tab consumes (the merged per-store month-to-date frames count under their merged names)."""
    from .backfill import DAILY_FRAMES
    from .layout import used_queries
    return set(DAILY_FRAMES) | used_queries() | {block.frame for block in MONTHLY} | {frame for frame, _ in SEGMENT_SPECS}
