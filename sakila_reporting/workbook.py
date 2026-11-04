"""A local workbook that behaves like the parts of Google Sheets (through gspread) that the pipeline relies on.

It lets the whole pipeline, including the multi-day column backfill, run and be tested with no Google account: the default
target writes to this workbook (saved as JSON, viewable as HTML), and `--publish` writes to a real spreadsheet instead.
The pipeline only ever calls the methods below, so both are interchangeable.

What it reproduces on purpose, because the pipeline's logic depends on each one:
* USER_ENTERED turns 'YYYY-MM-DD' text into a real date, which reads back as a SERIAL NUMBER with UNFORMATTED_VALUE;
  text beginning with '=' becomes a formula, evaluated on read; numeric text becomes a number.
* insert_cols shifts the columns to the right AND re-points the formulas that referred to them, exactly as Sheets does,
  so an older day's ratio formulas keep reading their own column after a new day is inserted in front.
* batch_update rewrites each item's 'range' IN PLACE to a sheet-qualified name (gspread does this), so passing the same
  list twice breaks the second call; the pipeline therefore hands it fresh copies on every attempt.
* An insert can fail after it has already shifted the columns (insert_cols is two requests in Sheets), which is why the
  pipeline checks for and undoes a partially committed insert.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from . import a1

_EPOCH = datetime(1899, 12, 30)
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_REF = re.compile(r"(?<![A-Za-z'!])\$?([A-Z]{1,3})\$?(\d+)")


class APIError(Exception):
    """Same class name and shape as gspread's APIError, which is all the retry policy looks at."""

    def __init__(self, status: int = 500, message: str = ""):
        super().__init__(f"APIError: [{status}] {message or 'simulated'}")

        class _Response:
            status_code = status

        self.response = _Response()


class WorksheetNotFound(Exception):
    pass


@dataclass
class Cell:
    value: object


def _serial(day: date) -> float:
    return float((datetime(day.year, day.month, day.day) - _EPOCH).days)


def _from_serial(serial: float) -> date:
    return (_EPOCH + timedelta(days=serial)).date()


def _parse_range(rng: str) -> tuple[int, int | None, int | None, int | None]:
    """'C10:C17' -> (col1, row1, col2, row2); 'A:H' -> (1, None, 8, None); 'M3:O' -> (13, 3, 15, None); 'C10' -> a single cell."""
    rng = rng.split("!")[-1].replace("$", "")
    first, _, last = rng.partition(":")

    def part(text):
        m = re.match(r"^([A-Za-z]+)(\d*)$", text)
        if not m:
            raise ValueError(f"unsupported range {rng!r}")
        return a1.letters_to_col(m.group(1)), (int(m.group(2)) if m.group(2) else None)

    c1, r1 = part(first)
    c2, r2 = part(last) if last else (c1, r1)
    return c1, r1, c2, r2


class _Evaluator:
    """Evaluates the small formulas the pipeline writes: numbers, cell references, + - * /, parentheses and IFERROR(expression, "")."""

    def __init__(self, sheet: LocalWorksheet):
        self.sheet, self.pos, self.tokens, self.depth = sheet, 0, [], 0

    def evaluate(self, formula: str, depth: int = 0):
        if depth > 20:
            return "#REF!"
        self.tokens = re.findall(r'IFERROR|\$?[A-Z]{1,3}\$?\d+|\d+\.?\d*|[()+\-*/,]|""|#REF!', formula.lstrip("="))
        if "#REF!" in self.tokens:
            return "#REF!"
        self.pos, self.depth = 0, depth
        try:
            value = self._expr()
            return value if self.pos == len(self.tokens) else "#ERROR!"
        except ZeroDivisionError:
            return "#DIV/0!"
        except (TypeError, ValueError, IndexError):
            return "#VALUE!"

    def _peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _expr(self):
        value = self._term()
        while self._peek() in ("+", "-"):
            op = self.tokens[self.pos]
            self.pos += 1
            rhs = self._term()
            value = value + rhs if op == "+" else value - rhs
        return value

    def _term(self):
        value = self._factor()
        while self._peek() in ("*", "/"):
            op = self.tokens[self.pos]
            self.pos += 1
            rhs = self._factor()
            value = value * rhs if op == "*" else value / rhs
        return value

    def _iferror(self):
        """IFERROR(first, second): `first`, or `second` when `first` fails. The arguments are the tokens up to the top-level comma and the
        closing parenthesis."""
        depth, i, comma = 0, self.pos + 1, None  # self.pos is at '('
        while i < len(self.tokens):
            if self.tokens[i] == "(":
                depth += 1
            elif self.tokens[i] == ")":
                if depth == 0:
                    break
                depth -= 1
            elif self.tokens[i] == "," and depth == 0 and comma is None:
                comma = i
            i += 1
        first, second = self.tokens[self.pos + 1:comma], self.tokens[comma + 1:i]
        self.pos = i + 1
        sub = _Evaluator(self.sheet)
        sub.tokens, sub.pos, sub.depth = first, 0, self.depth
        try:
            return sub._expr()
        except (ZeroDivisionError, TypeError, ValueError, IndexError):
            if second == ['""']:
                return ""
            sub.tokens, sub.pos = second, 0
            return sub._expr()

    def _factor(self):
        token = self._peek()
        self.pos += 1
        if token == "(":
            value = self._expr()
            self.pos += 1  # ')'
            return value
        if token == "-":
            return -self._factor()
        if token == "IFERROR":
            return self._iferror()
        if token is None:
            raise ValueError("unexpected end")
        if re.match(r"^\d", token):
            return float(token)
        col, row = a1.split_cell(token.replace("$", ""))
        value = self.sheet._unformatted(row, col, self.depth + 1)
        if isinstance(value, str) and value.startswith("#"):
            raise ValueError(value)
        if value in (None, ""):
            return 0.0
        return float(value)


class LocalWorksheet:
    def __init__(self, title: str, workbook: LocalWorkbook):
        self.title, self.workbook = title, workbook
        self.id = max((w.id for w in workbook.sheets.values()), default=0) + 1
        self.merges: list[tuple[int, int, int, int]] = []  # (row1, col1, row2, col2)
        self.cells: dict[tuple[int, int], tuple[str, object]] = {}  # (row, col) -> (kind, value); kind: v, date, f
        self.faults: dict[str, list] = {}  # operation -> exceptions to raise on the next calls (tests)
        self.calls: list[str] = []

    @property
    def spreadsheet(self):
        return self.workbook

    # ---- fault injection (tests): an item is an exception, or (exception, "commit") to apply the operation first and then fail
    def _fault(self, op: str):
        self.calls.append(op)
        queue = self.faults.get(op)
        if queue:
            fault = queue.pop(0)
            if isinstance(fault, tuple):
                return fault  # (exception, "commit")
            raise fault
        return None

    # ---- reading
    def _unformatted(self, row: int, col: int, depth: int = 0):
        kind, value = self.cells.get((row, col), ("v", None))
        if kind == "f":
            return _Evaluator(self).evaluate(value, depth)
        return value

    def _display(self, row: int, col: int):
        kind, value = self.cells.get((row, col), ("v", None))
        if kind == "date":
            return _from_serial(value).isoformat()
        if kind == "f":
            return _Evaluator(self).evaluate(value)
        return value

    def acell(self, label: str, value_render_option: str = "FORMATTED_VALUE") -> Cell:
        self._fault("acell")
        col, row = a1.split_cell(label)
        value = self._unformatted(row, col) if value_render_option == "UNFORMATTED_VALUE" else self._display(row, col)
        return Cell(value if value is not None else "")

    def batch_get(self, ranges, value_render_option: str = "FORMATTED_VALUE"):
        self._fault("batch_get")
        out = []
        for rng in ranges:
            c1, r1, c2, r2 = _parse_range(rng)
            r1, r2 = r1 or 1, r2 or self.max_row
            block = []
            for r in range(r1, r2 + 1):
                row = [(self._unformatted(r, c) if value_render_option == "UNFORMATTED_VALUE" else self._display(r, c)) for c in range(c1, c2 + 1)]
                while row and row[-1] is None:
                    row.pop()
                block.append(["" if v is None else v for v in row])
            while block and not any(v != "" for v in block[-1]):
                block.pop()
            out.append(block)
        return out

    @property
    def max_row(self) -> int:
        return max((r for r, _ in self.cells), default=0)

    @property
    def max_col(self) -> int:
        return max((c for _, c in self.cells), default=0)

    # ---- writing
    def _store(self, row: int, col: int, value, option: str | None) -> None:
        if value is None or value == "":
            self.cells.pop((row, col), None)
            return
        if option == "USER_ENTERED" and isinstance(value, str):
            if value.startswith("="):
                self.cells[(row, col)] = ("f", value)
                return
            if _ISO_DATE.match(value):
                try:
                    self.cells[(row, col)] = ("date", _serial(date.fromisoformat(value)))
                    return
                except ValueError:
                    pass
            try:
                self.cells[(row, col)] = ("v", float(value) if "." in value else int(value))
                return
            except ValueError:
                pass
        self.cells[(row, col)] = ("v", value)

    def batch_update(self, data, value_input_option=None):
        fault = self._fault("batch_update")
        for item in data:
            item["range"] = f"'{self.title}'!{item['range']}"  # gspread always prefixes, rewriting the caller's dict in place
            if item["range"].count("!") > 1:  # a second pass over the same dicts: "'Sheet'!'Sheet'!C10" is not a valid range
                raise APIError(400, f"Unable to parse range: {item['range']}")
            c1, r1, _, _ = _parse_range(item["range"])
            for dr, row in enumerate(item["values"]):
                for dc, value in enumerate(row):
                    self._store((r1 or 1) + dr, c1 + dc, value, value_input_option)
        if fault:
            raise fault[0]

    def batch_clear(self, ranges):
        self._fault("batch_clear")
        for rng in ranges:
            c1, r1, c2, r2 = _parse_range(rng)
            for (r, c) in list(self.cells):
                if c1 <= c <= c2 and (r1 is None or r >= r1) and (r2 is None or r <= r2):
                    del self.cells[(r, c)]

    def insert_cols(self, values, col: int = 1, value_input_option: str = "RAW"):
        fault = self._fault("insert_cols")
        n = len(values)
        self._shift(col, n)
        if fault:  # the columns have already moved: the second request is the one that fails
            raise fault[0]
        for i, column in enumerate(values):
            for r, value in enumerate(column, start=1):
                self._store(r, col + i, value, value_input_option)

    def delete_columns(self, start_index: int, end_index: int | None = None):
        self._fault("delete_columns")
        end = end_index or start_index
        n = end - start_index + 1
        for key in [k for k in self.cells if start_index <= k[1] <= end]:
            del self.cells[key]
        self._shift(end + 1, -n, deleted=(start_index, end))

    def _shift(self, from_col: int, by: int, deleted: tuple[int, int] | None = None, rows: tuple[int, int] | None = None) -> None:
        """Move every column at or right of `from_col` by `by` (only the cells in `rows`, if given), re-pointing formula references like
        Sheets does."""
        def inside(r: int) -> bool:
            return rows is None or rows[0] <= r <= rows[1]

        moved = {}
        for (r, c), cell in self.cells.items():
            moved[(r, c + by if c >= from_col and inside(r) else c)] = cell
        self.cells = moved
        self.merges = [(r1, c1 + by if c1 >= from_col and inside(r1) else c1, r2, c2 + by if c2 >= from_col and inside(r2) else c2)
                       for r1, c1, r2, c2 in self.merges]

        def repoint(match):
            col, row = a1.letters_to_col(match.group(1)), int(match.group(2))
            if not inside(row):
                return match.group(0)
            if deleted and deleted[0] <= col <= deleted[1]:
                return "#REF!"
            return f"{a1.col_to_letters(col + by) if col >= from_col else match.group(1)}{match.group(2)}"

        for key, (kind, value) in list(self.cells.items()):
            if kind == "f":
                self.cells[key] = ("f", _REF.sub(repoint, value))

    # ---- block operations (the Sheets API's insertRange / deleteRange / mergeCells, reached through Spreadsheet.batch_update)
    def insert_block_cols(self, rows: tuple[int, int], col: int, n: int) -> None:
        self._shift(col, n, rows=rows)

    def delete_block_cols(self, rows: tuple[int, int], col: int, n: int) -> None:
        for key in [k for k in self.cells if rows[0] <= k[0] <= rows[1] and col <= k[1] < col + n]:
            del self.cells[key]
        self._shift(col + n, -n, deleted=(col, col + n - 1), rows=rows)

    def delete_all(self):
        self.cells.clear()


class LocalWorkbook:
    """The spreadsheet: a set of worksheets, optionally saved to a JSON file."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else None
        self.sheets: dict[str, LocalWorksheet] = {}
        if self.path and self.path.exists():
            self._load()

    def worksheet(self, title: str) -> LocalWorksheet:
        if title not in self.sheets:
            raise WorksheetNotFound(title)
        return self.sheets[title]

    def worksheets(self) -> list[LocalWorksheet]:
        return list(self.sheets.values())

    def add_worksheet(self, title: str, rows: int = 100, cols: int = 26) -> LocalWorksheet:
        self.sheets[title] = LocalWorksheet(title, self)
        return self.sheets[title]

    def _by_id(self, sheet_id: int) -> LocalWorksheet:
        for ws in self.sheets.values():
            if ws.id == sheet_id:
                return ws
        raise APIError(400, f"no sheet with id {sheet_id}")

    def batch_update(self, body):
        """The requests this project sends to Sheets besides cell values: insertRange / deleteRange (shift one block of rows
        sideways), copyPaste of formats (no visual design is kept locally) and mergeCells."""
        for request in body.get("requests", []):
            (kind, args), = request.items()
            if kind in ("insertRange", "deleteRange"):
                r = args["range"]
                ws = self._by_id(r["sheetId"])
                fault = ws._fault("insert_range" if kind == "insertRange" else "delete_range")
                rows, col, n = (r["startRowIndex"] + 1, r["endRowIndex"]), r["startColumnIndex"] + 1, r["endColumnIndex"] - r["startColumnIndex"]
                (ws.insert_block_cols if kind == "insertRange" else ws.delete_block_cols)(rows, col, n)
                if fault:  # the change is in, then the call "fails": the caller cannot know
                    raise fault[0]
            elif kind == "mergeCells":
                r = args["range"]
                self._by_id(r["sheetId"]).merges.append((r["startRowIndex"] + 1, r["startColumnIndex"] + 1, r["endRowIndex"], r["endColumnIndex"]))
            elif kind == "copyPaste":
                self._by_id(args["source"]["sheetId"])  # formats are not kept locally
            else:
                raise APIError(400, f"unsupported request: {kind}")
        return {}

    # ---- persistence
    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"tabs": [{"title": ws.title, "cells": [[r, c, kind, value] for (r, c), (kind, value) in sorted(ws.cells.items())],
                          "merges": ws.merges} for ws in self.sheets.values()]}
        self.path.write_text(json.dumps(data, indent=1), encoding="utf-8")

    def _load(self) -> None:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        for tab in data["tabs"]:
            ws = self.add_worksheet(tab["title"])
            ws.cells = {(r, c): (kind, value) for r, c, kind, value in tab["cells"]}
            ws.merges = [tuple(m) for m in tab.get("merges", [])]

    # ---- a readable copy of every tab
    def to_html(self) -> str:
        parts = ["<!doctype html><meta charset='utf-8'><title>Workbook</title><style>body{font:13px system-ui,Arial;margin:20px;background:#f6f7f9}"
                 "table{border-collapse:collapse;background:#fff;margin-bottom:30px}"
                 "td,th{border:1px solid #dfe3e8;padding:3px 8px;font-variant-numeric:tabular-nums}"
                 "th{background:#eef0f3;font-weight:600}h2{margin:24px 0 6px}</style><h1>Workbook</h1>"]
        for ws in self.sheets.values():
            parts.append(f"<h2>{html.escape(ws.title)}</h2>")
            if not ws.cells:
                parts.append("<p>(empty)</p>")
                continue
            head = "".join(f"<th>{a1.col_to_letters(c)}</th>" for c in range(1, ws.max_col + 1))
            body = []
            for r in range(1, ws.max_row + 1):
                cells = []
                for c in range(1, ws.max_col + 1):
                    v = ws._display(r, c)
                    cells.append(f"<td>{html.escape(f'{v:,.4f}'.rstrip('0').rstrip('.') if isinstance(v, float) else '' if v is None else str(v))}</td>")
                body.append(f"<tr><th>{r}</th>{''.join(cells)}</tr>")
            parts.append(f"<table><tr><th></th>{head}</tr>{''.join(body)}</table>")
        return "".join(parts)
