"""Where every result goes on the dashboard. This is data, not code: adding a table means adding one line here.

Block kinds
    Value       one cell filled from one value of a query result (the KPI tiles)
    Label       one cell of fixed text (the KPI names, section headings), so a fresh sheet is complete without hand-typed labels
    Stamp       one cell filled with the time the run started
    Table       a result written with its header row, optionally with a title cell above it
    Transposed  a result written one column per row, label first (the monthly trend block)

A Table marked open_ended can grow or shrink between runs (a list whose length depends on the data). Its old
range is cleared before it is rewritten, so a shorter result never leaves stale rows underneath.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Union

from . import a1


@dataclass(frozen=True)
class Value:
    cell: str
    query: str
    row: int = 0
    col: int = 0


@dataclass(frozen=True)
class Label:
    cell: str
    text: str


@dataclass(frozen=True)
class Stamp:
    cell: str


@dataclass(frozen=True)
class Table:
    anchor: str
    query: str
    title_cell: str | None = None
    title: str | None = None
    open_ended: bool = False


@dataclass(frozen=True)
class Transposed:
    anchor: str
    query: str


Block = Union[Value, Label, Stamp, Table, Transposed]

EXECUTIVE_SUMMARY: list[Block] = [
    Label("A1", "Summary of Business"), Label("B3", "KPI"), Label("C3", "Value"),
    Label("B4", "Total Revenue"), Label("B5", "Total Payments"), Label("B6", "Avg Payment"), Label("B7", "Total Rentals"),
    Label("B8", "Total Customers"), Label("B9", "Active Customers"), Label("B10", "Total Films"), Label("B11", "Currently Unreturned"),
    Label("B12", "Last Updated"), Label("A14", "Monthly Report"), Label("A23", "Days Insights"), Label("A34", "Returns Tracking"),
    Label("B36", "KPI"), Label("C36", "Value"), Label("B37", "Late Returns count"), Label("B38", "Avg Days Overdue"),
    Value("C4", "executive_summary", 0, 0),   # Total Revenue
    Value("C5", "executive_summary", 0, 1),   # Total Payments
    Value("C6", "executive_summary", 0, 2),   # Avg Payment
    Value("C7", "total_rentals"),
    Value("C8", "total_customers", 0, 0),     # Total Customers
    Value("C9", "total_customers", 0, 1),     # Active Customers
    Value("C10", "total_films"),
    Value("C11", "unreturned"),
    Stamp("C12"),                             # Last Updated
    Transposed("B16", "monthly_revenue"),
    Table("B25", "rentals_by_dow"),
    Value("C37", "late_returns", 0, 0),       # Late Returns
    Value("C38", "late_returns", 0, 1),       # Avg Days Overdue
]

OPERATIONS: list[Block] = [
    Table("B3", "staff_performance", "A1", "Staff Performance"),
    Table("B10", "overdue_by_category", "A8", "Late Return Rate by Category"),
    Table("H3", "inventory_utilization", "H1", "Top 20 Films by Rentals per Copy"),
    Table("N3", "inventory_by_store", "N1", "Inventory by Store & Category", open_ended=True),
]


def _shift_cell(cell: str, cols: int) -> str:
    return a1.cell_at(cell, 0, cols)


def shifted(blocks: list[Block], cols: int) -> list[Block]:
    """The same blocks `cols` columns to the right, so two former tabs can sit side by side on one tab."""
    out: list[Block] = []
    for b in blocks:
        if isinstance(b, (Value, Label, Stamp)):
            out.append(replace(b, cell=_shift_cell(b.cell, cols)))
        elif isinstance(b, Transposed):
            out.append(replace(b, anchor=_shift_cell(b.anchor, cols)))
        else:
            out.append(replace(b, anchor=_shift_cell(b.anchor, cols), title_cell=_shift_cell(b.title_cell, cols) if b.title_cell else None))
    return out


OVERVIEW_OFFSET = 9  # Operations starts in column J: the Executive Summary needs A:G, column H and I stay empty

SHEETS: dict[str, list[Block]] = {
    "Overview": EXECUTIVE_SUMMARY + shifted(OPERATIONS, OVERVIEW_OFFSET),
    "Revenue & Finance": [
        Table("B3", "revenue_by_category", "A1", "Revenue by Category"),
        Table("B23", "rentals_by_rating", "A21", "Rentals & Revenue by Rating"),
        Table("F3", "revenue_by_store", "F1", "Revenue by Store"),
        Table("F9", "store_comparison", "F7", "Store Comparison"),
        Table("M3", "revenue_by_month_category", "M1", "Revenue by Month & Category", open_ended=True),
    ],
    "Film Catalogue": [
        Table("B3", "top_20_films", "A1", "Top 20 Films by Rentals"),
        Table("B28", "film_length_distribution", "A26", "Film Length Distribution"),
        Table("B37", "avg_duration_by_category", "A35", "Avg Rental Duration by Category"),
        Table("H3", "top_actors", "H1", "Top 20 Actors by Rentals"),
        Table("M3", "films_never_stocked", "M1", "Films Never Stocked (No Inventory Copies)", open_ended=True),
    ],
    "Customers & Geography": [
        Table("B3", "top_customers_by_spend", "A1", "Top 20 Customers by Spend"),
        Table("G3", "top_customers_by_rentals", "G1", "Top 20 Customers by Rentals"),
        Table("K3", "customer_segments", "K1", "Customer Segments by Rental Volume"),
        Table("P3", "customers_by_country", "P1", "Customers by Country", open_ended=True),
        Table("T3", "top_cities_by_customers", "T1", "Top 20 Cities by Customers"),
    ],
}


def used_queries() -> set[str]:
    return {b.query for blocks in SHEETS.values() for b in blocks if not isinstance(b, (Stamp, Label))}
