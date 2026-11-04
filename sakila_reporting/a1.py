"""Spreadsheet cell addresses (A1 notation) without any library, so they can be unit-tested directly."""
from __future__ import annotations

import re

_CELL = re.compile(r"^([A-Za-z]+)(\d+)$")


def col_to_letters(col: int) -> str:
    """1 -> 'A', 26 -> 'Z', 27 -> 'AA'."""
    if col < 1:
        raise ValueError(f"column numbers start at 1, got {col}")
    letters = ""
    while col:
        col, rem = divmod(col - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def letters_to_col(letters: str) -> int:
    col = 0
    for ch in letters.upper():
        col = col * 26 + (ord(ch) - 64)
    return col


def split_cell(cell: str) -> tuple[int, int]:
    """'B16' -> (column 2, row 16)."""
    match = _CELL.match(cell)
    if not match:
        raise ValueError(f"not an A1 cell address: {cell!r}")
    return letters_to_col(match.group(1)), int(match.group(2))


def cell_at(anchor: str, row_offset: int, col_offset: int) -> str:
    col, row = split_cell(anchor)
    return f"{col_to_letters(col + col_offset)}{row + row_offset}"


def rect(anchor: str, n_rows: int, n_cols: int) -> str:
    """The A1 range that a block of n_rows x n_cols starting at `anchor` covers, e.g. ('B3', 17, 3) -> 'B3:D19'."""
    if n_rows < 1 or n_cols < 1:
        raise ValueError("a range needs at least one row and one column")
    last = cell_at(anchor, n_rows - 1, n_cols - 1)
    return anchor if last == anchor else f"{anchor}:{last}"


def open_bottom(anchor: str, n_cols: int) -> str:
    """From `anchor` down to the end of the sheet, n_cols wide, e.g. ('M3', 3) -> 'M3:O'."""
    col, _ = split_cell(anchor)
    return f"{anchor}:{col_to_letters(col + n_cols - 1)}"
