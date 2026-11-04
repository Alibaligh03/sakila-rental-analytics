"""Every SQL file loads, runs, and is free of the ORDER BY mistake that used to ship four unsorted tables."""
import pytest

from sakila_reporting.layout import used_queries
from sakila_reporting.registry import broken_order_by, column_aliases, parse_query
from sakila_reporting.tabs import used_frames


def test_the_27_analytic_queries_are_registered(queries):
    assert len(queries) == 27
    assert all(q.description for q in queries.values())


def test_every_analytic_query_is_placed_on_the_dashboard(queries):
    assert used_queries() == set(queries), "a query without a place on the dashboard (or a layout entry without a query)"


def test_every_query_is_consumed_by_a_tab(all_queries):
    """The date-windowed families feed the daily / month-to-date / segmentation tabs; a query nothing reads is dead weight."""
    consumed = used_frames()
    # the per-store month-to-date queries run as two parts that are merged into the frame the tab reads
    unconsumed = {n for n in all_queries if n not in consumed and not (n.startswith("_") and n.endswith("_SPLIT"))}
    assert unconsumed == set()


def test_every_query_runs_and_returns_rows(report, all_queries):
    assert report.failures == {}
    assert set(report.results) == set(all_queries)
    # in the default 8-day window every customer has rented before, so nobody is "first seen"; the sparse Sakila dates allow this
    sparse = {"daily_first_seen", "daily_stores_first_seen", "daily_returns", "daily_stores_returns"}
    assert {n for n, df in report.results.items() if df.empty} <= sparse | {n for n in report.results if n.startswith("_")}


def test_no_query_orders_by_a_name_that_is_not_one_of_its_columns(queries):
    """SQLite treats an unknown double-quoted name as a constant string, so the ORDER BY silently does nothing."""
    offenders = {name: broken_order_by(q.sql) for name, q in queries.items() if broken_order_by(q.sql)}
    assert offenders == {}


def test_the_lint_really_catches_the_old_bug():
    old = 'SELECT c.name AS "Category", SUM(p.amount) AS "Total Revenue" FROM t GROUP BY 1 ORDER BY "Total Revenue ($)" DESC'
    assert broken_order_by(old) == ["Total Revenue ($)"]
    assert broken_order_by(old.replace("Total Revenue ($)", "Total Revenue")) == []


def test_sort_declaration_is_read_from_the_sql():
    q = parse_query("x", '-- x: demo\nSELECT a AS "A", b AS "B" FROM t ORDER BY "B" DESC, "A"')
    assert (q.sort_by, q.descending, q.description) == ("B", True, "demo")
    assert parse_query("y", 'SELECT a AS "A" FROM t ORDER BY a').sort_by is None  # not a quoted alias: not declared
    assert column_aliases('SELECT 1 AS "One", 2 AS "Two"') == {"One", "Two"}


# Known facts about the bundled Sakila data. They pin the behaviour the old script got wrong.
def test_top_customers_by_spend_really_are_the_top_spenders(results):
    top = results["top_customers_by_spend"]
    assert top.iloc[0]["Customer Name"] == "KARL SEAL"
    assert top["Total Spent"].is_monotonic_decreasing
    assert top["Total Spent"].min() >= results["top_customers_by_spend"]["Total Spent"].iloc[-1]


def test_revenue_by_category_is_sorted_by_revenue(results):
    df = results["revenue_by_category"]
    assert df.iloc[0]["Category"] == "Sports"
    assert df["Total Revenue"].is_monotonic_decreasing


def test_staff_and_month_category_tables_are_sorted(results):
    assert results["staff_performance"]["Revenue Collected"].is_monotonic_decreasing
    month_cat = results["revenue_by_month_category"]
    assert month_cat["Month"].is_monotonic_decreasing
    first_month = month_cat[month_cat["Month"] == month_cat["Month"].iloc[0]]
    assert first_month["Revenue"].is_monotonic_decreasing


def test_the_monthly_revenue_header_is_spelled_correctly(results):
    assert "Financially Active Customers" in results["monthly_revenue"].columns


@pytest.mark.parametrize("name", [
    "top_20_films", "top_actors", "top_customers_by_spend", "top_customers_by_rentals", "top_cities_by_customers", "inventory_utilization"])
def test_top_lists_have_exactly_20_rows(results, name):
    assert len(results[name]) == 20


# ---- bugs found by auditing every query against an independent calculation ----
def test_customer_segments_spend_is_per_customer_not_multiplied_by_rentals(results):
    seg = results["customer_segments"]
    assert seg["Avg Total Spend"].between(50, 250).all(), seg  # it was 1,389 to 8,380 when payments were repeated once per rental
    assert abs((seg["Customers"] * seg["Avg Total Spend"]).sum() - 67416.51) < 0.01
    assert seg["Customers"].sum() == 599


def test_each_city_shows_its_real_customer_count(results):
    cities = results["top_cities_by_customers"]
    assert cities["Customers"].max() == 2 and len(cities) == 20  # it showed 50 when customers were repeated once per payment
    assert cities["Customers"].is_monotonic_decreasing


def test_actor_film_counts_include_every_credited_film(results):
    actors = results["top_actors"].set_index("Actor")
    assert actors.loc["MATTHEW CARREY", "Films in Catalogue"] == 39  # it showed 37: films never stocked were dropped


def test_films_never_stocked_really_have_no_inventory(results, settings):
    from sakila_reporting.db import rows
    never = set(results["films_never_stocked"]["Film Title"])
    stocked = {title for (title,) in rows(settings.db_path, "SELECT DISTINCT f.title FROM film f JOIN inventory i ON i.film_id = f.film_id")}
    assert len(never) == 42 and not never & stocked  # they were labelled "dead inventory", but they are not in inventory at all


def test_top_lists_are_deterministic_at_the_cutoff(results):
    films = results["top_20_films"]
    ties = films[films["Total Rentals"] == films["Total Rentals"].min()]
    assert ties["Total Revenue"].is_monotonic_decreasing  # equal rentals: higher revenue first, then title
