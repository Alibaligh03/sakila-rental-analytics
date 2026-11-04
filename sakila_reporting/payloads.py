"""Turns query results into the exact cell updates for each sheet. Pure functions: no network, easy to test."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from . import a1
from .layout import SHEETS, Block, Label, Stamp, Transposed, Value


@dataclass
class SheetPayload:
    sheet: str
    updates: list[dict] = field(default_factory=list)
    clear_ranges: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # queries with no result: their block is left untouched

    @property
    def cell_count(self) -> int:
        return sum(len(row) for u in self.updates for row in u["values"])


def clean_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Infinities become 0 and missing values become empty cells, so the sheet never shows inf or nan."""
    df = df.replace([np.inf, -np.inf], 0)
    return df.astype(object).where(df.notna(), "")  # object dtype first: filling a numeric column with "" is deprecated in pandas


def to_python(value):
    """A JSON-serialisable value: dates as dd/mm/yyyy, numpy scalars as plain Python numbers."""
    if isinstance(value, pd.Timestamp):
        return value.strftime("%d/%m/%Y")
    try:
        return value.item()
    except AttributeError:
        return value


def frame_to_values(df: pd.DataFrame) -> list[list]:
    return [df.columns.tolist()] + [[to_python(v) for v in row] for row in df.values.tolist()]


def _block_updates(block: Block, results: dict[str, pd.DataFrame], run_started: datetime):
    """(updates, clear_ranges) for one block, or None when its query has no usable result."""
    if isinstance(block, Label):
        return [{"range": block.cell, "values": [[block.text]]}], []
    if isinstance(block, Stamp):
        return [{"range": block.cell, "values": [[run_started.strftime("%Y-%m-%d %H:%M:%S")]]}], []
    df = results.get(block.query)
    if df is None or df.empty:
        return None
    df = clean_frame(df)
    if isinstance(block, Value):
        try:
            return [{"range": block.cell, "values": [[to_python(df.iloc[block.row, block.col])]]}], []
        except IndexError as exc:
            raise ValueError(f"query '{block.query}' has no value at row {block.row}, column {block.col}") from exc
    if isinstance(block, Transposed):
        values = [[col] + df[col].map(to_python).tolist() for col in df.columns]
        return [{"range": block.anchor, "values": values}], []
    updates = []
    if block.title_cell:
        updates.append({"range": block.title_cell, "values": [[block.title]]})
    updates.append({"range": block.anchor, "values": frame_to_values(df)})
    clear = [a1.open_bottom(block.anchor, len(df.columns))] if block.open_ended else []
    return updates, clear


def build_payloads(results: dict[str, pd.DataFrame], run_started: datetime, layout: dict[str, list[Block]] = SHEETS) -> dict[str, SheetPayload]:
    payloads: dict[str, SheetPayload] = {}
    for sheet, blocks in layout.items():
        payload = SheetPayload(sheet)
        for block in blocks:
            built = _block_updates(block, results, run_started)
            if built is None:
                payload.skipped.append(block.query)
                continue
            updates, clear = built
            payload.updates += updates
            payload.clear_ranges += clear
        payloads[sheet] = payload
    return payloads
