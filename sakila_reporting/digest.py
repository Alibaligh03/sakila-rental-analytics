"""Geometry of the 'Daily Digest' tab: every KPI block of the report on one tab, stacked like the pages of a printed digest.

    rows   1- 23   Daily              one column per day (newest on the left), starting in column D
    rows  26- 49   Daily by Store     two columns per day: the date merged across the pair, the store names under it
    rows  52- 78   Monthly            this month so far | the same days last month | % Change | (gap) | three earlier months
    rows  81-108   Monthly by Store   MTD | SPLM | % Change, each merged over STORE 1 and STORE 2
    rows 111-      Segments           titled tables stacked below (their length varies, so they come last)

Columns: A is a narrow margin, B the domain, C the KPI, then the figures from D. Every block owns its own rows, so adding a day to
the daily block shifts only that block's cells and leaves everything below untouched.
"""
from __future__ import annotations

TAB = "Daily Digest"
FIRST_DAY_COL = 4  # column D

DAILY_KPIS = [
    "Rentals", "Active Customers", "Payments", "Revenue", "Avg Payment", "Unique Films Rented", "Unique Categories Rented", "Revenue per Rental",
    "Rentals per Active Customer", "Revenue per Paying Customer", "Unreturned Opened", "Largest Payment", "Paying Customers", "Cross-Store Customers",
    "New Customers At Home Store", "New Customers Cross-Store", "New Customers Share of Active", "Returned", "Returned On Time", "Returned Late",
]
MONTH_KPIS = [
    "Customers who have rented (snapshots)", "Rentals", "Active Customers", "Payments", "Revenue", "Avg Payment", "Unique Films Rented",
    "Unique Categories Rented", "Returned Rentals", "Late Returns", "Late Return Rate", "Rentals per Customer", "Revenue per Customer", "Avg Days Kept",
    "Unreturned Rentals", "Cross-Store Customers", "New Customers At Home Store", "New Customers Cross-Store", "Returned", "Returned On Time",
    "Returned Late", "Avg Days Kept (returns)", "Still Out", "Returned (opened this month)",
]

_RETURNS = {"Returned", "Returned On Time", "Returned Late", "Returned Rentals", "Late Returns", "Late Return Rate", "Avg Days Kept", "Avg Days Kept (returns)",
            "Still Out", "Returned (opened this month)", "Unreturned Opened", "Unreturned Rentals"}
_CUSTOMERS = {"Active Customers", "Paying Customers", "Cross-Store Customers", "New Customers At Home Store", "New Customers Cross-Store",
              "New Customers Share of Active", "Customers who have rented (snapshots)", "Rentals per Active Customer", "Rentals per Customer",
              "Revenue per Customer", "Revenue per Paying Customer"}
_CATALOGUE = {"Unique Films Rented", "Unique Categories Rented"}


def domain_of(kpi: str) -> str:
    group = "Returns" if kpi in _RETURNS else "Customers" if kpi in _CUSTOMERS else "Catalogue" if kpi in _CATALOGUE else "Core KPIs"
    return f"Rental · {group}"


# ---- rows (1-based)
D_TITLE, D_LEGEND, D_DATE, D_FIRST = 1, 2, 3, 4
D_LAST = D_FIRST + len(DAILY_KPIS) - 1                      # 23
S_TITLE, S_DATE, S_NAMES, S_FIRST = 26, 28, 29, 30
S_LAST = S_FIRST + len(DAILY_KPIS) - 1                      # 49
M_TITLE, M_HEAD, M_FIRST = 52, 54, 55
M_LAST = M_FIRST + len(MONTH_KPIS) - 1                      # 78
MS_TITLE, MS_HEAD, MS_NAMES, MS_FIRST = 81, 83, 84, 85   # period headings, then the store names under them
MS_LAST = MS_FIRST + len(MONTH_KPIS) - 1                    # 108
SEG_TITLE, SEG_FIRST = 111, 113

STORES = ("Store 1", "Store 2")
# plain text beside a block's title band, in capitals; the blocks' own rows are never shifted by an inserted day, so these stay put
LEGENDS = {D_TITLE: {"D": "BLUE IS SATURDAY", "F": "YELLOW IS SUNDAY", "H": "GREEN / RED = VS PREVIOUS DAY"},
           S_TITLE: {"D": "GREEN / RED = VS PREVIOUS DAY"},
           M_TITLE: {"D": "% CHANGE = MTD VS SAME DAYS LAST MONTH"},
           MS_TITLE: {"D": "% CHANGE = MTD VS SAME DAYS LAST MONTH"}}
OLD_LEGEND_CELLS = (f"B{D_LEGEND}", f"B{M_TITLE + 1}")  # an earlier layout wrote italic legends here; they are cleared
TITLES = {D_TITLE: "Rental Insights (Daily)", S_TITLE: "STORE 1 & STORE 2", M_TITLE: "Rental Insights",
          MS_TITLE: "Rental Insights (STORE 1 & STORE 2)", SEG_TITLE: "Rental Insights (Segments)"}
STORE_LABELS = tuple(s.upper() for s in STORES)  # shown in capitals, like the channel names of the digest this is modelled on


def static_cells() -> list[dict]:
    """The fixed text of the tab as value updates: block titles, legends, the Domain / KPI columns and their headings."""
    updates = [{"range": f"A{row}", "values": [[text]]} for row, text in TITLES.items()]
    updates += [{"range": cell, "values": [[""]]} for cell in OLD_LEGEND_CELLS]
    updates += [{"range": f"{col}{row}", "values": [[text]]} for row, cells in LEGENDS.items() for col, text in cells.items()]
    for head in (D_DATE, S_DATE, M_HEAD, MS_HEAD):
        updates.append({"range": f"B{head}:C{head}", "values": [["Domain", "KPI"]]})
    for first, kpis in ((D_FIRST, DAILY_KPIS), (S_FIRST, DAILY_KPIS), (M_FIRST, MONTH_KPIS), (MS_FIRST, MONTH_KPIS)):
        updates.append({"range": f"B{first}:C{first + len(kpis) - 1}", "values": [[domain_of(k), k] for k in kpis]})
    for names_row in (S_NAMES, MS_NAMES):  # the names row is part of the table: a Domain and a KPI cell like every other row
        updates.append({"range": f"B{names_row}:C{names_row}", "values": [[domain_of("Rentals"), "Store"]]})
    return updates


def segment_layout(frames: dict, specs) -> list[tuple[str, str, int]]:
    """(frame name, title, title row) for every segment table that has data, stacked from SEG_FIRST with two blank rows between them.
    The table's header row is the row under its title."""
    out, row = [], SEG_FIRST
    for frame, title in specs:
        df = frames.get(frame)
        if df is None or df.empty:
            continue
        out.append((frame, title, row))
        row += len(df) + 4  # title, header, the rows, two blank rows
    return out
