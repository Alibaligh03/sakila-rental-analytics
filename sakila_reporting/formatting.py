"""The look of the spreadsheet, modelled on a daily KPI digest: Lato 10, centred values, no gridlines, a bold coloured header band, a
bold title band above each block, thin vertical borders, compact K / M numbers, and values coloured green or red by whether they beat
the neighbouring period. The accent colour is purple.

Everything here is a Sheets API request built from the same layout the data is written with, so the formatting always lines up with
the data. The builders are pure functions (no network) and are tested as such; `apply_design` sends them.

Applying is idempotent: for every tab it first clears the old formatting and conditional-format rules, then applies the design again,
so running it twice (or after the layout changed) never leaves leftovers or duplicates.

Conditional formats (weekday header colours, green / red) are rules rather than painted colours: the backfill inserts a column at C
every day, and a painted colour would stay behind while the dates moved right, whereas a rule reads each column's own values.
"""
from __future__ import annotations

import re

import pandas as pd

from . import a1, digest
from .layout import SHEETS, Label, Stamp, Table, Transposed, Value
from .logging_utils import LOG
from .retry import with_retry
from .tabs import SEGMENT_SPECS

# ---- palette
PURPLE = {"red": 0.54, "green": 0.31, "blue": 0.78}      # the accent: header and title bands
WHITE = {"red": 1.0, "green": 1.0, "blue": 1.0}
INK = {"red": 0.0, "green": 0.0, "blue": 0.0}
GREEN = {"red": 0.718, "green": 0.882, "blue": 0.804}    # better than the neighbouring period
RED = {"red": 0.957, "green": 0.780, "blue": 0.765}      # worse than the neighbouring period
BLUE = {"red": 0.239, "green": 0.522, "blue": 0.776}     # Saturday header
BLUE_TEXT = {"red": 0.965, "green": 0.973, "blue": 0.976}
YELLOW = {"red": 0.988, "green": 0.910, "blue": 0.698}   # Sunday header
NAME_FILL = {"red": 1.0, "green": 0.949, "blue": 0.800}  # store names
LINE = {"red": 0.0, "green": 0.0, "blue": 0.0}

# Compact numbers: 1.5K, 2.30M ... The digest this is modelled on shows every large figure this way.
COMPACT_INT = '[>=1000000]0.00,,"M";[>=1000]0.00,"K";0'
COMPACT_DEC = '[>=1000000]0.00,,"M";[>=1000]0.00,"K";0.00'
FULL = {"int": "#,##0", "money": "#,##0.00", "dec": "#,##0.00", "pct": "0.00%"}
COMPACT = {"int": COMPACT_INT, "money": COMPACT_DEC, "dec": COMPACT_DEC, "pct": "0.00%"}

UP = {"red": 0.204, "green": 0.659, "blue": 0.325}      # a positive % Change: the text turns green
DOWN = {"red": 0.918, "green": 0.263, "blue": 0.208}    # a negative one: red

SATURDAY, SUNDAY = 7, 1  # Sheets' WEEKDAY(): Sunday = 1 ... Saturday = 7
FONT = {"fontFamily": "Lato", "fontSize": 10}
ROW_HEIGHT = 21
TITLE_HEIGHT = 30
DAY_WIDTH = 120
LABEL_WIDTHS = (24, 150, 250)  # margin, Domain, KPI

_MONEY = re.compile(r"revenue|spent|spend|amount|payment|cost|price|profit|per customer|per rental|per paying", re.IGNORECASE)
_PCT = re.compile(r"\(%\)|\brate\b|\bshare\b", re.IGNORECASE)
_DEC = re.compile(r"avg|average|per active|per copy|days", re.IGNORECASE)


# ---- small helpers
def grid(sheet_id: int, ref: str) -> dict:
    """A GridRange for 'B3:D19' or a single cell 'B3'."""
    first, _, last = ref.partition(":")
    (c1, r1), (c2, r2) = a1.split_cell(first), a1.split_cell(last or first)
    return {"sheetId": sheet_id, "startRowIndex": r1 - 1, "endRowIndex": r2, "startColumnIndex": c1 - 1, "endColumnIndex": c2}


def _repeat(sheet_id: int, ref: str, **fmt) -> dict:
    """Apply some cell-format fields to a range. Only the fields given are changed."""
    cell, fields = {}, []
    for key, value in fmt.items():
        cell[key] = value
        fields.append(f"userEnteredFormat.{key}")
    return {"repeatCell": {"range": grid(sheet_id, ref), "cell": {"userEnteredFormat": cell}, "fields": ",".join(fields)}}


def _text(bold=False, color=INK, size=10, italic=False) -> dict:
    return {"textFormat": {**FONT, "fontSize": size, "bold": bold, "italic": italic, "foregroundColor": color}}


def _side(style="SOLID") -> dict:
    return {"style": style, "color": LINE}


def header(sheet_id: int, ref: str) -> dict:
    """The coloured header band: bold white text, centred."""
    return _repeat(sheet_id, ref, backgroundColor=PURPLE, horizontalAlignment="CENTER", verticalAlignment="MIDDLE", wrapStrategy="WRAP", **_text(True, WHITE))


def band(sheet_id: int, ref: str) -> dict:
    """A title band above a block: the same colour, one size up."""
    return _repeat(sheet_id, ref, backgroundColor=PURPLE, horizontalAlignment="LEFT", verticalAlignment="MIDDLE", **_text(True, WHITE, 11))


def title_band(sheet_id: int, row: int, last_col: str = "C") -> list[dict]:
    """A block's title: a band over the label columns only (A to C), merged, centred, taller than a normal row. The legend sits beside
    it in plain text."""
    ref = f"A{row}:{last_col}{row}"
    fill = _repeat(sheet_id, ref, backgroundColor=PURPLE, horizontalAlignment="CENTER", verticalAlignment="MIDDLE", **_text(True, WHITE, 11))
    return [merge(sheet_id, ref), fill, row_height(sheet_id, row, TITLE_HEIGHT, first=row)]


def legend(sheet_id: int, ref: str) -> dict:
    return _repeat(sheet_id, ref, horizontalAlignment="LEFT", verticalAlignment="MIDDLE", **_text(False, INK, 9))


def label(sheet_id: int, ref: str) -> dict:
    return _repeat(sheet_id, ref, horizontalAlignment="CENTER", verticalAlignment="MIDDLE", **_text())


def names(sheet_id: int, ref: str) -> dict:
    """Bold names on a pale fill (the store names above each day's pair of columns)."""
    return _repeat(sheet_id, ref, backgroundColor=NAME_FILL, horizontalAlignment="CENTER", verticalAlignment="MIDDLE", **_text(True))


def numbers(sheet_id: int, ref: str, kind: str, compact: bool = False) -> dict:
    pattern = (COMPACT if compact else FULL)[kind]
    return _repeat(sheet_id, ref, numberFormat={"type": "PERCENT" if kind == "pct" else "NUMBER", "pattern": pattern}, horizontalAlignment="CENTER")


def verticals(sheet_id: int, ref: str) -> dict:
    """Thin lines left and right of every value cell, and under the last row."""
    return {"updateBorders": {"range": grid(sheet_id, ref), "left": _side(), "right": _side(), "innerVertical": _side(), "bottom": _side()}}


def day_borders(sheet_id: int, first_row: int, last_row: int, n_days: int, width: int) -> list[dict]:
    """Vertical lines for a day-by-day block. With one column per day every column gets its line; with two (the per-store block) the
    lines fall BETWEEN DAYS only, so a day's two stores read as one unit, as in the digest this is modelled on."""
    last = _last(n_days, width)
    table = f"B{first_row}:{last}{last_row}"
    if width == 1:
        return [verticals(sheet_id, table)]
    out = [{"updateBorders": {"range": grid(sheet_id, table), "left": _side(), "bottom": _side(), "right": _side()}}]
    for day in range(max(n_days, 1)):
        first = a1.col_to_letters(digest.FIRST_DAY_COL + day * width)
        pair = f"{first}{first_row}:{a1.col_to_letters(digest.FIRST_DAY_COL + day * width + width - 1)}{last_row}"
        out.append({"updateBorders": {"range": grid(sheet_id, pair), "left": _side(), "right": _side()}})
    return out


def header_borders(sheet_id: int, ref: str) -> dict:
    return {"updateBorders": {"range": grid(sheet_id, ref), "top": _side(), "left": _side(), "right": _side(), "innerVertical": _side(),
                              "bottom": _side("SOLID_MEDIUM")}}


def col_width(sheet_id: int, col: int, pixels: int) -> dict:
    return {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": col - 1, "endIndex": col},
                                          "properties": {"pixelSize": pixels}, "fields": "pixelSize"}}


def hide_rows(sheet_id: int, first: int, last: int, hidden: bool = True) -> dict:
    return {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": first - 1, "endIndex": last},
                                          "properties": {"hiddenByUser": hidden}, "fields": "hiddenByUser"}}


def row_height(sheet_id: int, rows: int, pixels: int, first: int = 1) -> dict:
    """Set the height of rows first..rows (1-based, inclusive)."""
    return {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": first - 1, "endIndex": rows},
                                          "properties": {"pixelSize": pixels}, "fields": "pixelSize"}}


def sheet_props(sheet_id: int, frozen_rows: int = 0, frozen_cols: int = 0) -> dict:
    props = {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": frozen_rows, "frozenColumnCount": frozen_cols, "hideGridlines": True}}
    fields = "tabColorStyle,gridProperties.frozenRowCount,gridProperties.frozenColumnCount,gridProperties.hideGridlines"  # tabColorStyle unset = no colour
    return {"updateSheetProperties": {"properties": props, "fields": fields}}


def put(sheet_id: int, cell: str, text: str) -> dict:
    """Write a fixed text (a title or a legend) into one cell."""
    col, row = a1.split_cell(cell)
    return {"updateCells": {"start": {"sheetId": sheet_id, "rowIndex": row - 1, "columnIndex": col - 1},
                            "rows": [{"values": [{"userEnteredValue": {"stringValue": text}}]}], "fields": "userEnteredValue"}}


def _rule(sheet_id: int, ref: str, formula: str, background: dict, text: dict | None = None) -> dict:
    fmt = {"backgroundColor": background}
    if text:
        fmt["textFormat"] = {"foregroundColor": text}
    return {"addConditionalFormatRule": {"index": 0, "rule": {
        "ranges": [grid(sheet_id, ref)],
        "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": formula}]}, "format": fmt}}}}


def weekday_rules(sheet_id: int, ref: str, first_cell: str) -> list[dict]:
    """Header cells by weekday: Saturday blue, Sunday yellow. `first_cell` is the top-left cell of `ref`, which the formulas refer to."""
    col, row = a1.split_cell(first_cell)
    ref_cell = f"{a1.col_to_letters(col)}${row}"
    # Sunday gets dark text: the header's white would vanish on yellow
    def on(day: int) -> str:
        return f"=AND(ISNUMBER({ref_cell}),WEEKDAY({ref_cell})={day})"  # a blank date is day 0, which is a Saturday: guard it

    return [_rule(sheet_id, ref, on(SATURDAY), BLUE, BLUE_TEXT), _rule(sheet_id, ref, on(SUNDAY), YELLOW, INK)]


def merge(sheet_id: int, ref: str) -> dict:
    return {"mergeCells": {"range": grid(sheet_id, ref), "mergeType": "MERGE_ALL"}}


def sign_rules(sheet_id: int, ref: str) -> list[dict]:
    """The text of a % Change cell is green above zero and red below it; the cell itself stays white."""
    def rule(kind: str, color: dict) -> dict:
        return {"addConditionalFormatRule": {"index": 0, "rule": {"ranges": [grid(sheet_id, ref)], "booleanRule": {
            "condition": {"type": kind, "values": [{"userEnteredValue": "0"}]}, "format": {"textFormat": {"foregroundColor": color}}}}}}
    return [rule("NUMBER_GREATER", UP), rule("NUMBER_LESS", DOWN)]


def beats_rules(sheet_id: int, ref: str, cell: str, other: str) -> list[dict]:
    """Green where a value is higher than the one it is compared with, red where it is lower. Blank cells are never coloured.
    `cell` is the top-left cell of `ref`; `other` is the cell it is compared with, relative to it (for example 'D11' for 'C11')."""
    both = f"ISNUMBER({cell}),ISNUMBER({other})"
    return [_rule(sheet_id, ref, f"=AND({both},{cell}>{other})", GREEN), _rule(sheet_id, ref, f"=AND({both},{cell}<{other})", RED)]


def reset(sheet_id: int, n_rules: int, rows: int, cols: int) -> list[dict]:
    """Back to a blank design: no formats, no rules, no hidden rows. Data is untouched (only userEnteredFormat is cleared)."""
    whole = {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": rows, "startColumnIndex": 0, "endColumnIndex": cols}
    base = _repeat(sheet_id, f"A1:{a1.col_to_letters(cols)}{rows}", backgroundColor=WHITE, verticalAlignment="MIDDLE", horizontalAlignment="CENTER", **_text())
    return ([{"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": 0}} for _ in range(n_rules)]
            + [{"repeatCell": {"range": whole, "cell": {"userEnteredFormat": {}}, "fields": "userEnteredFormat"}}, hide_rows(sheet_id, 1, rows, False),
               row_height(sheet_id, rows, ROW_HEIGHT), base])


# ---- what kind of number is a column
def kind_of(name: str, values) -> str | None:
    """'int' | 'money' | 'dec' | 'pct', or None for text."""
    nums = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    present = [v for v in values if v != "" and v is not None]
    if not present or len(nums) != len(present):
        return None
    if _PCT.search(str(name)):
        return "pct"
    if _MONEY.search(str(name)):
        return "money"
    if all(float(v).is_integer() for v in nums) and not _DEC.search(str(name)):
        return "int"
    return "dec"


MONTH_FORMAT = {"type": "DATE", "pattern": "mmm yyyy"}
_ISO_MONTH = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


def _is_month(name, values) -> bool:
    """A 'Month' column of ISO months such as 2005-08 or 2005-08-01. The sheet turns those into dates, which show as serial numbers
    unless they are given a date format."""
    present = [v for v in values if v not in ("", None)]
    return name == "Month" and bool(present) and all(isinstance(v, str) and _ISO_MONTH.match(v) for v in present)


def _width_from(chars: int) -> int:
    return max(76, min(280, chars * 8 + 24))


# ---- the tabs built from the dashboard layout (Overview, Revenue & Finance, Film Catalogue, Customers & Geography)
def dashboard_requests(sheet_id: int, blocks, frames: dict) -> list[dict]:
    reqs, widths, title_cols, bands = [], {}, set(), []

    def widen(col: int, chars: int):
        widths[col] = max(widths.get(col, 0), chars)

    for block in blocks:
        if isinstance(block, Label):
            col, row = a1.split_cell(block.cell)
            if block.text in ("KPI", "Value"):
                reqs.append(header(sheet_id, block.cell))
            elif col == 1:
                bands.append((row, col, 3 if block.text == "Summary of Business" else 7))  # a section heading: a band across its block
            else:
                reqs.append(label(sheet_id, block.cell))
            widen(col, len(block.text) if col != 1 else 0)
        elif isinstance(block, Stamp):
            col, _ = a1.split_cell(block.cell)
            stamp_format = {"type": "DATE_TIME", "pattern": "yyyy-mm-dd hh:mm:ss"}  # the sheet turns the timestamp text into a date-time serial
            reqs.append(_repeat(sheet_id, block.cell, numberFormat=stamp_format, horizontalAlignment="CENTER", **_text()))
            widen(col, 19)
        elif isinstance(block, Value):
            df = frames.get(block.query)
            if df is None or df.empty:
                continue
            col, _ = a1.split_cell(block.cell)
            name, value = df.columns[block.col], df.iloc[block.row, block.col]
            reqs.append(numbers(sheet_id, block.cell, kind_of(name, [value]) or "int"))
            widen(col, len(f"{value:,.2f}") if isinstance(value, (int, float)) else 12)
        elif isinstance(block, Transposed):
            df = frames.get(block.query)
            if df is None or df.empty:
                continue
            col, row = a1.split_cell(block.anchor)
            n_cols = len(df) + 1
            reqs.append(header(sheet_id, a1.rect(block.anchor, 1, n_cols)))
            reqs.append(label(sheet_id, a1.rect(block.anchor, len(df.columns), 1)))
            reqs.append(header(sheet_id, block.anchor))
            if df.columns[0] == "Month" and _is_month("Month", df["Month"].tolist()):  # the heading cells are the months, one column per month
                reqs.append(_repeat(sheet_id, a1.rect(a1.cell_at(block.anchor, 0, 1), 1, n_cols - 1), numberFormat=MONTH_FORMAT, horizontalAlignment="CENTER"))
            for i, name in enumerate(df.columns[1:], start=1):
                row_range = a1.cell_at(block.anchor, i, 1) + ":" + a1.cell_at(block.anchor, i, n_cols - 1)
                reqs.append(numbers(sheet_id, row_range, kind_of(name, df[name].tolist()) or "int"))
            reqs.append(verticals(sheet_id, a1.rect(block.anchor, len(df.columns), n_cols)))
            for j in range(n_cols):
                widen(col + j, 11 if j else max(len(str(c)) for c in df.columns))
        elif isinstance(block, Table):
            df = frames.get(block.query)
            if df is None or df.empty:
                continue
            col, row = a1.split_cell(block.anchor)
            if block.title_cell:
                title_col, title_row = a1.split_cell(block.title_cell)
                bands.append((title_row, title_col, col + len(df.columns) - title_col))
                title_cols.add(title_col)
            reqs.append(header(sheet_id, a1.rect(block.anchor, 1, len(df.columns))))
            reqs.append(verticals(sheet_id, a1.rect(block.anchor, len(df) + 1, len(df.columns))))
            reqs.append(header_borders(sheet_id, a1.rect(block.anchor, 1, len(df.columns))))
            for j, name in enumerate(df.columns):
                values = df[name].tolist()
                kind = kind_of(name, values)
                if kind:
                    reqs.append(numbers(sheet_id, a1.rect(a1.cell_at(block.anchor, 1, j), len(df), 1), kind))
                if _is_month(name, values):
                    reqs.append(_repeat(sheet_id, a1.rect(a1.cell_at(block.anchor, 1, j), len(df), 1), numberFormat=MONTH_FORMAT, horizontalAlignment="CENTER"))
                widen(col + j, max([len(str(name))] + [len(f"{v:,.2f}") if kind else len(str(v)) for v in values]))
    for row, col, span in bands:  # the title bands are written last so that nothing above overrides them
        reqs.append(band(sheet_id, a1.rect(a1.col_to_letters(col) + str(row), 1, span)))
    for col, chars in widths.items():
        reqs.append(col_width(sheet_id, col, 24 if chars == 0 else _width_from(chars)))
    reqs += [col_width(sheet_id, col, 24) for col in sorted(title_cols - set(widths))]  # columns that only carry a table title
    return reqs


# ---- the Daily Digest tab
def _kind_for_kpi(text: str) -> str:
    if re.search(r"share", text, re.IGNORECASE):
        return "pct"
    if re.search(r"^revenue$|avg payment|revenue per|largest payment", text, re.IGNORECASE):
        return "money"
    if re.search(r"per active", text, re.IGNORECASE):
        return "dec"
    return "int"


def _month_kind(text: str) -> str:
    if re.search(r"rate", text, re.IGNORECASE):
        return "pct"
    if re.search(r"per customer|days kept", text, re.IGNORECASE):
        return "dec"
    return "money" if re.search(r"^revenue$|avg payment", text, re.IGNORECASE) else "int"


def _last(n_days: int, width: int) -> str:
    """The last day column of a block that has `n_days` days at `width` columns each."""
    return a1.col_to_letters(digest.FIRST_DAY_COL + max(n_days, 1) * width - 1)


def _daily_block(sheet_id: int, width: int, n_days: int, title_row: int, date_row: int, first_row: int, last_row: int) -> list[dict]:
    """A day-by-day block: title band, header with the dates, KPI rows, formatted for the days that exist (a column the backfill inserts
    later copies its neighbour's formats). Colour rules start in column C, one column left of the data, so that a column inserted at D
    falls INSIDE their range and is coloured too."""
    last = _last(n_days, width)
    reqs = title_band(sheet_id, title_row)
    reqs += [legend(sheet_id, f"D{title_row}:{a1.col_to_letters(digest.FIRST_DAY_COL + 8)}{title_row}"),
             header(sheet_id, f"B{date_row}:{last}{date_row}"), header_borders(sheet_id, f"B{date_row}:{last}{date_row}"),
             _repeat(sheet_id, f"D{date_row}:{last}{date_row}", numberFormat={"type": "DATE", "pattern": 'd"-"mmm'}),
             label(sheet_id, f"B{first_row}:C{last_row}")]
    for i, kpi in enumerate(digest.DAILY_KPIS):
        reqs.append(numbers(sheet_id, f"D{first_row + i}:{last}{first_row + i}", _kind_for_kpi(kpi), compact=True))
    reqs += day_borders(sheet_id, date_row + 1 if width == 2 else first_row, last_row, n_days, width)  # the per-store block's table starts at its names row
    reqs += weekday_rules(sheet_id, f"C{date_row}:{last}{date_row}", f"C{date_row}")
    # each day against the previous one: the next column to the right (for two columns per day, the same store's column two to the right)
    other = f"{a1.col_to_letters(3 + width)}{first_row}"
    reqs += beats_rules(sheet_id, f"C{first_row}:{last}{last_row}", f"C{first_row}", other)
    return reqs


def group_borders(sheet_id: int, first_row: int, last_row: int, groups: list[tuple[str, str]], left: str = "B") -> list[dict]:
    """Vertical lines around groups of columns (each group's first and last column), the table's left edge and its bottom: the lines fall
    BETWEEN groups, never inside one."""
    last = groups[-1][1]
    out = [{"updateBorders": {"range": grid(sheet_id, f"{left}{first_row}:{last}{last_row}"), "left": _side(), "bottom": _side(), "right": _side()}}]
    for first, final in groups:
        out.append({"updateBorders": {"range": grid(sheet_id, f"{first}{first_row}:{final}{last_row}"), "left": _side(), "right": _side()}})
    return out


def _monthly_total(sheet_id: int, earlier: int = 3) -> list[dict]:
    """'Rental Insights': this month so far, the same days last month and the % Change in one table; the earlier months (as many as have
    activity, up to three) in a second table after a gap column, as in the digest this is modelled on. Nothing is coloured except the
    % Change text."""
    d = digest
    reqs = title_band(sheet_id, d.M_TITLE)
    reqs.append(legend(sheet_id, f"D{d.M_TITLE}:I{d.M_TITLE}"))
    tables = [("B", "F")] + ([("H", a1.col_to_letters(7 + earlier))] if earlier > 0 else [])
    for first, last in tables:
        head = f"{first}{d.M_HEAD}:{last}{d.M_HEAD}"
        reqs += [header(sheet_id, head), header_borders(sheet_id, head)]
    reqs += [label(sheet_id, f"B{d.M_FIRST}:C{d.M_LAST}"), *group_borders(sheet_id, d.M_FIRST, d.M_LAST, [("D", "F")], left="B")]
    if earlier > 0:
        last = a1.col_to_letters(7 + earlier)
        reqs += group_borders(sheet_id, d.M_FIRST, d.M_LAST, [("H", last)], left="H")
    for i, kpi in enumerate(d.MONTH_KPIS):
        row, kind = d.M_FIRST + i, _month_kind(kpi)
        reqs += [numbers(sheet_id, f"D{row}:E{row}", kind, compact=True), numbers(sheet_id, f"F{row}", "pct")]
        if earlier > 0:
            reqs.append(numbers(sheet_id, f"H{row}:{a1.col_to_letters(7 + earlier)}{row}", kind, compact=True))
    reqs += sign_rules(sheet_id, f"F{d.M_FIRST}:F{d.M_LAST}")
    return reqs


def _monthly_stores(sheet_id: int) -> list[dict]:
    """'Rental Insights (STORE 1 & STORE 2)': each period (MTD, SPLM, % Change) is merged over the two stores, whose names repeat on a pale
    row underneath. Lines fall between the periods only. The % Change text is coloured per store."""
    d = digest
    names_row = d.MS_NAMES
    reqs = title_band(sheet_id, d.MS_TITLE)
    head = f"B{d.MS_HEAD}:I{d.MS_HEAD}"
    reqs += [legend(sheet_id, f"D{d.MS_TITLE}:I{d.MS_TITLE}"), header(sheet_id, head), header_borders(sheet_id, head),
             merge(sheet_id, f"D{d.MS_HEAD}:E{d.MS_HEAD}"), merge(sheet_id, f"F{d.MS_HEAD}:G{d.MS_HEAD}"), merge(sheet_id, f"H{d.MS_HEAD}:I{d.MS_HEAD}"),
             names(sheet_id, f"D{names_row}:I{names_row}"), label(sheet_id, f"B{names_row}:C{d.MS_LAST}"),
             *group_borders(sheet_id, names_row, d.MS_LAST, [("D", "E"), ("F", "G"), ("H", "I")])]
    for i, kpi in enumerate(d.MONTH_KPIS):
        row, kind = d.MS_FIRST + i, _month_kind(kpi)
        reqs += [numbers(sheet_id, f"D{row}:G{row}", kind, compact=True), numbers(sheet_id, f"H{row}:I{row}", "pct")]
    reqs += sign_rules(sheet_id, f"H{d.MS_FIRST}:I{d.MS_LAST}")
    return reqs


def segment_requests(sheet_id: int, frames: dict) -> list[dict]:
    """Each segment table gets its own title band and header row, stacked under the Segments title, from column B."""
    reqs = title_band(sheet_id, digest.SEG_TITLE)
    for frame, _, row in digest.segment_layout(frames, SEGMENT_SPECS):
        df = frames[frame]
        last = a1.col_to_letters(1 + len(df.columns))
        head = f"B{row + 1}:{last}{row + 1}"
        reqs += [band(sheet_id, f"A{row}:{last}{row}"), header(sheet_id, head), header_borders(sheet_id, head),
                 verticals(sheet_id, f"B{row + 1}:{last}{row + 1 + len(df)}")]
        for j, name in enumerate(df.columns):
            body = a1.rect(a1.cell_at(f"B{row + 2}", 0, j), len(df), 1)
            if name == "Month":  # an ISO month such as 2005-08-01 is turned into a date by the sheet, which would otherwise show as a serial number
                reqs.append(_repeat(sheet_id, body, numberFormat={"type": "DATE", "pattern": "mmm yyyy"}, horizontalAlignment="CENTER"))
                continue
            kind = kind_of(name, df[name].tolist())
            if kind:
                reqs.append(numbers(sheet_id, body, kind))
    return reqs


def digest_requests(sheet_id: int, frames: dict, days: tuple[int, int] = (1, 1), earlier: int = 3) -> list[dict]:
    """`days` = how many days the daily block and the per-store block hold right now; `earlier` = how many earlier months have a column."""
    d = digest
    reqs = [col_width(sheet_id, c, w) for c, w in zip((1, 2, 3), LABEL_WIDTHS, strict=True)]
    reqs += [col_width(sheet_id, c, DAY_WIDTH) for c in range(d.FIRST_DAY_COL, d.FIRST_DAY_COL + max(days[0], 2 * days[1], 9))]
    reqs += _daily_block(sheet_id, 1, days[0], d.D_TITLE, d.D_DATE, d.D_FIRST, d.D_LAST)
    reqs += _daily_block(sheet_id, 2, days[1], d.S_TITLE, d.S_DATE, d.S_FIRST, d.S_LAST)
    reqs.append(names(sheet_id, f"D{d.S_NAMES}:{_last(days[1], 2)}{d.S_NAMES}"))
    reqs.append(label(sheet_id, f"B{d.S_NAMES}:C{d.S_NAMES}"))
    reqs += _monthly_total(sheet_id, earlier)
    reqs += _monthly_stores(sheet_id)
    reqs += segment_requests(sheet_id, frames)
    reqs.append(sheet_props(sheet_id))
    return reqs


def columns_needed(reqs: list[dict]) -> int:
    """The highest column (1-based) any request refers to, so the tab can be widened before it is formatted."""
    top = 0

    def walk(node):
        nonlocal top
        if isinstance(node, dict):
            if "endColumnIndex" in node:
                top = max(top, node["endColumnIndex"])
            if node.get("dimension") == "COLUMNS":
                top = max(top, node.get("endIndex", 0))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(reqs)
    return top


def design_requests(tabs: dict, frames: dict, days: tuple[int, int] = (1, 1), earlier: int = 3) -> list[dict]:
    """tabs: title -> {'id', 'rows', 'cols', 'rules'}. Returns every request needed to (re)design the tabs that exist."""
    reqs = []
    for title_, tab in tabs.items():
        sid = tab["id"]
        built = None
        if title_ in SHEETS:
            built = dashboard_requests(sid, SHEETS[title_], frames) + [sheet_props(sid)]
        elif title_ == digest.TAB:
            built = digest_requests(sid, frames, days, earlier)
        if built is not None:
            tab_reqs = reset(sid, tab["rules"], tab["rows"], tab["cols"]) + built
            missing = columns_needed(tab_reqs) - tab["cols"]
            if missing > 0:  # formatting a column that does not exist is an error, so add the columns first
                tab_reqs.insert(0, {"appendDimension": {"sheetId": sid, "dimension": "COLUMNS", "length": missing}})
            reqs += tab_reqs
    return reqs


def day_counts(spreadsheet, retry_kwargs: dict) -> tuple[int, int]:
    """How many days the daily block and the per-store block hold: the dates along their date rows (a store day's date is merged across
    its two columns, so only every second cell has one)."""
    sheet = with_retry(spreadsheet.worksheet, digest.TAB, **retry_kwargs)
    first = digest.FIRST_DAY_COL
    last = a1.col_to_letters(first + 120)

    def row(r):
        got = with_retry(sheet.get, f"{a1.col_to_letters(first)}{r}:{last}{r}", value_render_option="UNFORMATTED_VALUE", **retry_kwargs)
        return got[0] if got else []

    return len(row(digest.D_DATE)), (len(row(digest.S_DATE)) + 1) // 2


def earlier_count(spreadsheet, retry_kwargs: dict) -> int:
    """How many earlier-month columns the monthly block has: the headings in H:J (an unused month has a blank heading)."""
    sheet = with_retry(spreadsheet.worksheet, digest.TAB, **retry_kwargs)
    got = with_retry(sheet.get, f"H{digest.M_HEAD}:J{digest.M_HEAD}", value_render_option="UNFORMATTED_VALUE", **retry_kwargs)
    return len([h for h in (got[0] if got else []) if h != ""])


def apply_design(spreadsheet, frames: dict, retry_kwargs: dict | None = None) -> bool:
    """Format every tab of the spreadsheet that the pipeline writes. Cosmetic: a failure is reported and never stops the run."""
    retry_kwargs = retry_kwargs or {}
    if not hasattr(spreadsheet, "fetch_sheet_metadata"):
        return False  # the local workbook has no visual design
    try:
        meta = with_retry(spreadsheet.fetch_sheet_metadata,
                          params={"fields": "sheets(properties(sheetId,title,gridProperties(rowCount,columnCount)),conditionalFormats)"}, **retry_kwargs)
        tabs = {s["properties"]["title"]: {"id": s["properties"]["sheetId"], "rows": s["properties"]["gridProperties"]["rowCount"],
                                          "cols": s["properties"]["gridProperties"]["columnCount"], "rules": len(s.get("conditionalFormats", []))}
                for s in meta.get("sheets", [])}
        days, earlier = (day_counts(spreadsheet, retry_kwargs), earlier_count(spreadsheet, retry_kwargs)) if digest.TAB in tabs else ((1, 1), 3)
        reqs = design_requests(tabs, {k: v for k, v in frames.items() if isinstance(v, pd.DataFrame)}, days, earlier)
        if reqs:
            with_retry(spreadsheet.batch_update, {"requests": reqs}, **retry_kwargs)
            LOG.info("Design applied to %d tab(s) (%d requests)", len(tabs), len(reqs))
        return True
    except Exception as exc:
        LOG.warning("Could not apply the design (%s); the data is unaffected", exc)
        return False
