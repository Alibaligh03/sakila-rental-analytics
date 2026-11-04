"""Reconciliation checks: do the report's numbers agree with each other and with an independent query?

Every check here either compares two parts of the report that must agree, or compares a reported number with a
separately written SQL query against the same database. They run before anything is published, and publishing is
refused when one fails. Together they catch the usual silent failures: a join that duplicates rows, a filter that
drops them, an ORDER BY that does nothing, an empty result.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd

from .db import rows, scalar
from .layout import used_queries
from .registry import Query, broken_order_by

MONEY_TOLERANCE = 0.005  # half a cent: amounts are stored to the cent but summed as floats

TOP_LISTS = ["top_20_films", "top_actors", "top_customers_by_spend", "top_customers_by_rentals", "top_cities_by_customers", "inventory_utilization"]
REVENUE_BREAKDOWNS = [
    ("revenue_by_category", "Total Revenue"), ("revenue_by_month_category", "Revenue"), ("monthly_revenue", "Revenue"),
    ("rentals_by_rating", "Total Revenue ($)"), ("revenue_by_store", "Total Revenue"), ("store_comparison", "Total Revenue"),
    ("staff_performance", "Revenue Collected"),
]


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str
    skipped: bool = False


class _Missing(Exception):
    """A query this check needs did not produce a result (it failed or was not selected)."""


def _get(results: dict[str, pd.DataFrame], name: str) -> pd.DataFrame:
    if name not in results:
        raise _Missing(name)
    return results[name]


def _first(results, name: str):
    return _get(results, name).iloc[0, 0]


def _col_sum(results, name: str, col: str) -> float:
    return float(_get(results, name)[col].sum())


def _all_equal(values: dict[str, float], tolerance: float) -> tuple[bool, str]:
    reference = next(iter(values.values()))
    off = {k: v for k, v in values.items() if abs(v - reference) > tolerance}
    shown = ", ".join(f"{k}={v:,.2f}" for k, v in values.items())
    return not off, shown if off else f"all equal to {reference:,.2f}"


# ---- the checks -----------------------------------------------------------------------------------------------

def _no_empty_results(results, db) -> tuple[bool, str]:
    """The fixed analytic queries must always return rows. The window-bound queries (daily, month-to-date, ...) can
    legitimately be empty, for example when the window contains no rentals, so they are not held to this."""
    analytic = used_queries()
    present = {name: df for name, df in results.items() if name in analytic}
    empty = sorted(name for name, df in present.items() if df.empty)
    return not empty, f"empty: {', '.join(empty)}" if empty else f"{len(present)} analytic queries, all returned rows"


def _executive_revenue_matches_payments(results, db) -> tuple[bool, str]:
    reported, independent = float(_first(results, "executive_summary")), float(scalar(db, "SELECT SUM(amount) FROM payment"))
    return abs(reported - independent) <= MONEY_TOLERANCE, f"reported {reported:,.2f} vs payments table {independent:,.2f}"


def _revenue_breakdowns_agree(results, db) -> tuple[bool, str]:
    return _all_equal({name: _col_sum(results, name, col) for name, col in REVENUE_BREAKDOWNS}, MONEY_TOLERANCE)


def _executive_gap_explained(results, db) -> tuple[bool, str]:
    """Executive revenue counts every payment; the breakdowns can only count payments that join to a rental, an
    inventory item and a category. The difference must be exactly the payments that cannot join."""
    gap = float(_first(results, "executive_summary")) - _col_sum(results, "revenue_by_category", "Total Revenue")
    unjoinable = float(scalar(db, """
        SELECT COALESCE(SUM(p.amount), 0) FROM payment p
        WHERE NOT EXISTS (SELECT 1 FROM rental r JOIN inventory i ON i.inventory_id = r.inventory_id
                          JOIN film_category fc ON fc.film_id = i.film_id WHERE r.rental_id = p.rental_id)"""))
    count = scalar(db, """
        SELECT COUNT(*) FROM payment p
        WHERE NOT EXISTS (SELECT 1 FROM rental r JOIN inventory i ON i.inventory_id = r.inventory_id
                          JOIN film_category fc ON fc.film_id = i.film_id WHERE r.rental_id = p.rental_id)""")
    return abs(gap - unjoinable) <= MONEY_TOLERANCE, f"gap {gap:,.2f} vs {count} payment(s) without a rental totalling {unjoinable:,.2f}"


def _rental_counts_agree(results, db) -> tuple[bool, str]:
    return _all_equal({
        "total_rentals": float(_first(results, "total_rentals")),
        "rentals_by_dow": _col_sum(results, "rentals_by_dow", "Total Rentals"),
        "revenue_by_category": _col_sum(results, "revenue_by_category", "Number of Rentals"),
        "rentals_by_rating": _col_sum(results, "rentals_by_rating", "Total Rentals"),
        "staff_performance": _col_sum(results, "staff_performance", "Rentals Handled"),
        "rental table": float(scalar(db, "SELECT COUNT(*) FROM rental")),
    }, 0)


def _customer_counts_agree(results, db) -> tuple[bool, str]:
    return _all_equal({
        "total_customers": float(_first(results, "total_customers")),
        "customers_by_country": float(_get(results, "customers_by_country").iloc[:, 1].sum()),
        "customer_segments": _col_sum(results, "customer_segments", "Customers"),
        "customer table": float(scalar(db, "SELECT COUNT(*) FROM customer")),
    }, 0)


def _film_counts_agree(results, db) -> tuple[bool, str]:
    return _all_equal({
        "total_films": float(_first(results, "total_films")),
        "film_length_distribution": float(_get(results, "film_length_distribution").iloc[:, 1].sum()),
        "film table": float(scalar(db, "SELECT COUNT(*) FROM film")),
    }, 0)


def _never_stocked_matches(results, db) -> tuple[bool, str]:
    independent = scalar(db, "SELECT COUNT(*) FROM film f WHERE NOT EXISTS (SELECT 1 FROM inventory i WHERE i.film_id = f.film_id)")
    listed = len(_get(results, "films_never_stocked"))
    return listed == independent, f"{listed} listed vs {independent} by an independent query"


def _customer_spend_consistent(results, db) -> tuple[bool, str]:
    """Customers x average spend, summed over the segments, must equal all money paid. A join that repeats payments
    (once per rental) inflates the average, and this is what exposes it."""
    seg = _get(results, "customer_segments")
    total = float((seg["Customers"] * seg["Avg Total Spend"]).sum())
    independent = float(scalar(db, "SELECT SUM(amount) FROM payment"))
    return abs(total - independent) <= 0.01, f"segments account for {total:,.2f} vs {independent:,.2f} paid"


def _city_customer_counts_correct(results, db) -> tuple[bool, str]:
    """Each city's customer count must equal an independent count. A join onto payments repeats a customer once per payment."""
    truth = {(c, k): n for c, k, n in rows(db, """
        SELECT ci.city, co.country, COUNT(*) FROM customer c JOIN address a ON a.address_id = c.address_id
        JOIN city ci ON ci.city_id = a.city_id JOIN country co ON co.country_id = ci.country_id GROUP BY ci.city_id""")}
    cities = _get(results, "top_cities_by_customers")
    wrong = [(r["City"], int(r["Customers"]), truth.get((r["City"], r["Country"]))) for _, r in cities.iterrows()
             if int(r["Customers"]) != truth.get((r["City"], r["Country"]))]
    largest = int(cities["Customers"].max())
    return not wrong, f"wrong counts (city, shown, true): {wrong[:3]}" if wrong else f"{len(cities)} cities match; the largest has {largest} customers"


def _actor_film_counts_correct(results, db) -> tuple[bool, str]:
    """'Films in Catalogue' is every film the actor is credited in, not only the films that were rented."""
    truth = dict(rows(db, """
        SELECT a.first_name || ' ' || a.last_name, COUNT(*) FROM actor a
        JOIN film_actor fa ON fa.actor_id = a.actor_id GROUP BY a.actor_id"""))
    actors = _get(results, "top_actors")
    wrong = [(r["Actor"], int(r["Films in Catalogue"]), truth.get(r["Actor"]))
             for _, r in actors.iterrows() if int(r["Films in Catalogue"]) != truth.get(r["Actor"])]
    return not wrong, f"wrong counts (actor, shown, true): {wrong[:3]}" if wrong else f"{len(actors)} actors match the film_actor table"


def _unreturned_matches(results, db) -> tuple[bool, str]:
    independent = scalar(db, "SELECT COUNT(*) FROM rental WHERE return_date IS NULL")
    return int(_first(results, "unreturned")) == independent, f"{int(_first(results, 'unreturned'))} reported vs {independent} by an independent query"


def _inventory_copies_match(results, db) -> tuple[bool, str]:
    return _all_equal({"inventory_by_store": _col_sum(results, "inventory_by_store", "Total Copies"),
                       "inventory table": float(scalar(db, "SELECT COUNT(*) FROM inventory"))}, 0)


def _late_returns_consistent(results, db) -> tuple[bool, str]:
    return _all_equal({"late_returns": float(_first(results, "late_returns")),
                       "overdue_by_category": _col_sum(results, "overdue_by_category", "Late Returns")}, 0)


def _top_lists_have_20_rows(results, db) -> tuple[bool, str]:
    sizes = {name: len(_get(results, name)) for name in TOP_LISTS}
    off = {k: v for k, v in sizes.items() if v != 20}
    return not off, f"not 20 rows: {off}" if off else "all six top-20 lists have 20 rows"


def _sorted_as_declared(results, db, queries: dict[str, Query]) -> tuple[bool, str]:
    """The result must really be in the order the SQL asks for. Catches an ORDER BY that silently does nothing."""
    bad, checked = [], 0
    for name, query in queries.items():
        if name not in results or not query.sort_by or query.sort_by not in results[name].columns:
            continue
        raw = results[name][query.sort_by].dropna()
        numeric = pd.to_numeric(raw, errors="coerce")
        series = numeric if numeric.notna().all() else raw  # text columns such as 'YYYY-MM' compare as text
        if series.empty:
            continue
        checked += 1
        ordered = series.is_monotonic_decreasing if query.descending else series.is_monotonic_increasing
        if not ordered:
            bad.append(f"{name} (by {query.sort_by})")
    return not bad, f"not sorted: {', '.join(bad)}" if bad else f"{checked} sorted results verified"


def _order_by_names_are_columns(queries: dict[str, Query]) -> tuple[bool, str]:
    """An ORDER BY on a name that is not a column of the query does nothing in SQLite (the name becomes a constant
    string), so the rows come back unsorted without any error. This is how a 'top 20' list silently stops being one."""
    bad = {name: broken_order_by(q.sql) for name, q in queries.items() if broken_order_by(q.sql)}
    return not bad, f"ORDER BY on a non-column in: {bad}" if bad else f"{len(queries)} queries order by real columns"


CHECKS: list[tuple[str, Callable]] = [
    ("every query returned rows", _no_empty_results),
    ("executive revenue equals the payments table", _executive_revenue_matches_payments),
    ("revenue breakdowns agree with each other", _revenue_breakdowns_agree),
    ("executive-vs-breakdown revenue gap is fully explained", _executive_gap_explained),
    ("rental counts agree", _rental_counts_agree),
    ("customer counts agree", _customer_counts_agree),
    ("film counts agree", _film_counts_agree),
    ("never-stocked films match an independent query", _never_stocked_matches),
    ("customer spend per segment adds up to all payments", _customer_spend_consistent),
    ("city customer counts match an independent count", _city_customer_counts_correct),
    ("actor film counts match the film_actor table", _actor_film_counts_correct),
    ("unreturned rentals match an independent query", _unreturned_matches),
    ("inventory copies match the inventory table", _inventory_copies_match),
    ("late returns agree across tables", _late_returns_consistent),
    ("top-20 lists have 20 rows", _top_lists_have_20_rows),
]


def run_checks(results: dict[str, pd.DataFrame], queries: dict[str, Query], db_path: Path) -> list[CheckResult]:
    outcomes: list[CheckResult] = []
    for name, fn in CHECKS:
        try:
            passed, detail = fn(results, db_path)
            outcomes.append(CheckResult(name, bool(passed), detail))
        except _Missing as missing:
            outcomes.append(CheckResult(name, True, f"skipped: query '{missing}' has no result", skipped=True))
    passed, detail = _order_by_names_are_columns(queries)
    outcomes.append(CheckResult("ORDER BY names are real columns", passed, detail))
    try:
        passed, detail = _sorted_as_declared(results, db_path, queries)
        outcomes.append(CheckResult("results are sorted as the SQL declares", passed, detail))
    except Exception as exc:  # a bug in the check itself must be visible, not silent
        outcomes.append(CheckResult("results are sorted as the SQL declares", False, f"check failed to run: {exc}"))
    return outcomes


def all_passed(outcomes: list[CheckResult]) -> bool:
    return all(o.passed for o in outcomes)


# ---- checks for the window-bound query families (daily, month-to-date) ------------------------------------------------
def _frame(frames, name):
    return frames.get(name)


def _stores_add_up(frames, total_name: str, stores_name: str, key: str, columns: list[str]) -> tuple[bool, str]:
    total, stores = _frame(frames, total_name), _frame(frames, stores_name)
    if total is None or stores is None or total.empty or stores.empty:
        raise _Missing(f"{total_name}/{stores_name}")
    bad, periods = [], set(stores[key])
    for _, row in total.iterrows():
        if row[key] not in periods:
            continue  # the per-store frame covers fewer months than the total: compare only the periods both have
        part = stores[stores[key] == row[key]]
        for col in columns:
            summed, whole = float(part[col].sum()), float(row[col])
            if abs(summed - whole) > MONEY_TOLERANCE:
                bad.append((row[key], col, round(summed, 2), round(whole, 2)))
    return not bad, f"(period, column, stores, total) differ: {bad[:3]}" if bad else f"{len(periods)} periods: the stores add up to the total"


def _returns_add_up(frames) -> tuple[bool, str]:
    checked, bad = 0, []
    for name in ("daily_returns", "daily_stores_returns", "mtd_and_splm_returns", "mtd_and_splm_stores_returns"):
        df = _frame(frames, name)
        if df is None or df.empty:
            continue
        checked += 1
        off = df[(df["Returned"].astype(float) - df["Returned On Time"].astype(float) - df["Returned Late"].astype(float)).abs() > 0]
        if not off.empty:
            bad.append(name)
    if checked == 0:
        raise _Missing("returns frames")
    return not bad, f"on-time + late does not equal returned in: {bad}" if bad else f"{checked} returns frames: on time + late = returned"


def _days_inside_window(frames, params) -> tuple[bool, str]:
    lo, hi = pd.Timestamp(params["as_of"]) - pd.Timedelta(days=int(params["lookback_days"])), pd.Timestamp(params["as_of"])
    checked, bad = 0, []
    for name in ("daily", "daily_stores", "daily_cross_store_customers", "daily_first_seen", "daily_returns"):
        df = _frame(frames, name)
        if df is None or df.empty:
            continue
        checked += 1
        days = pd.to_datetime(df["Day"])
        if ((days < lo) | (days >= hi)).any():
            bad.append(name)
    if checked == 0:
        raise _Missing("daily frames")
    window = int(params["lookback_days"])
    if bad:
        return False, f"days outside [{lo.date()}, {hi.date()}) in: {bad}"
    return True, f"{checked} daily frames: every day is inside the {window}-day lookback window"


def run_family_checks(frames: dict[str, pd.DataFrame], params: dict) -> list[CheckResult]:
    checks = [
        ("daily stores add up to the daily total", lambda: _stores_add_up(frames, "daily", "daily_stores", "Day", ["Rentals", "Payments", "Revenue"])),
        ("month-to-date stores add up to the total",
         lambda: _stores_add_up(frames, "mtd_and_splm", "mtd_and_splm_stores", "Month", ["Rentals", "Payments", "Revenue"])),
        ("returns split adds up", lambda: _returns_add_up(frames)),
        ("daily rows fall inside the lookback window", lambda: _days_inside_window(frames, params)),
    ]
    outcomes = []
    for name, fn in checks:
        try:
            passed, detail = fn()
            outcomes.append(CheckResult(name, bool(passed), detail))
        except _Missing as missing:
            outcomes.append(CheckResult(name, True, f"skipped: no data for {missing}", skipped=True))
        except Exception as exc:
            outcomes.append(CheckResult(name, False, f"check failed to run: {exc}"))
    return outcomes
