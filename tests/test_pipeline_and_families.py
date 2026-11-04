"""The pipeline's own steps and behaviours, and an independent recomputation of the windowed query families.

The family tests rebuild every number from the raw Sakila tables with pandas (no SQL from sql/), so a mistake in a query's join,
window or grouping cannot be mirrored by the test.
"""
import sqlite3
from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd
import pytest

from sakila_reporting import pipeline
from sakila_reporting.a1 import col_to_letters
from sakila_reporting.db import run_sql
from sakila_reporting.pipeline import Options, clean_frames, fingerprint, merge_split_frames, run
from sakila_reporting.workbook import APIError, LocalWorkbook


# ---- the pipeline's steps ----
def test_clean_frames_makes_iso_dates_zeroes_infinities_and_blanks_missing_values():
    df = pd.DataFrame({"Day": pd.to_datetime(["2005-08-23", "2005-08-22"]), "Ratio": [np.inf, 1.5], "Name": ["a", None]})
    out = clean_frames({"x": df})["x"]
    assert out["Day"].tolist() == ["2005-08-23", "2005-08-22"]  # ISO: 'dd/mm/yyyy' is ambiguous to a month-first spreadsheet
    assert out["Ratio"].tolist() == [0, 1.5] and out["Name"].tolist() == ["a", ""]


def month_rows(*months, store=None):
    rows = [{"Month": m, "Rentals": i + 1} for i, m in enumerate(months)]
    if store:
        rows = [{**r, "Store": s} for r in rows for s in ("Store 1", "Store 2")]
    return pd.DataFrame(rows)


def test_split_parts_are_merged_into_the_frame_the_tabs_read():
    parts = {"_mtd_stores_SPLIT": month_rows("2005-08-01", store=True), "_splm_stores_SPLIT": month_rows("2005-07-01", store=True),
             "daily": pd.DataFrame({"a": [1]})}
    merged = merge_split_frames(parts, "split")
    stores = merged["mtd_and_splm_stores"]
    assert set(merged) == {"daily", "mtd_and_splm_stores"} and stores["Month"].tolist()[0] == "2005-08-01" and len(stores) == 4


def test_one_empty_split_part_keeps_the_other_and_two_empty_parts_give_an_empty_frame():
    one = merge_split_frames({"_mtd_stores_SPLIT": month_rows("2005-08-01", store=True), "_splm_stores_SPLIT": pd.DataFrame()}, "split")
    assert len(one["mtd_and_splm_stores"]) == 2
    both = merge_split_frames({"_mtd_stores_SPLIT": pd.DataFrame(), "_splm_stores_SPLIT": pd.DataFrame()}, "split")
    assert both["mtd_and_splm_stores"].empty


def test_cte_mode_leaves_the_frames_alone():
    frames = {"mtd_and_splm_stores": month_rows("2005-08-01", store=True)}
    assert merge_split_frames(frames, "cte").keys() == frames.keys()


def test_the_cache_fingerprint_changes_with_the_windows_and_the_query_mode():
    from sakila_reporting.windows import window_params
    a = fingerprint(window_params(date(2005, 8, 24), "daily", 2), "split")
    assert a == fingerprint(window_params(date(2005, 8, 24), "daily", 2), "split")
    others = [fingerprint(window_params(date(2005, 8, 23), "daily", 2), "split"), fingerprint(window_params(date(2005, 8, 24), "daily", 5), "split"),
              fingerprint(window_params(date(2005, 8, 24), "daily", 2), "cte"), fingerprint(window_params(date(2005, 8, 24), "first-of-month", 2), "split")]
    assert len({a, *others}) == 5


# ---- whole runs ----
@pytest.fixture
def local_settings(settings, tmp_path):
    return replace(settings, output_dir=tmp_path / "out", workbook_path=tmp_path / "out" / "workbook" / "workbook.json", cache_dir=tmp_path / "cache",
                   heartbeat_seconds=999, sheets_retries=2, sheets_retry_delay=0.0)


def daily_stamps(book, row=3, width=1):
    """The dates along a block's date row, newest first: row 3 is the daily block, row 28 the per-store block (two columns per day)."""
    ws, out, col = book.worksheet("Daily Digest"), [], 4
    while ws.acell(f"{col_to_letters(col)}{row}").value not in ("", None):
        out.append(ws.acell(f"{col_to_letters(col)}{row}").value)
        col += width
    return out


def test_a_second_run_with_a_later_as_of_date_catches_up_on_the_missed_days(local_settings):
    first = run(local_settings, Options(as_of=date(2005, 8, 20), no_cache=True))
    assert first.exit_code == 0
    book = LocalWorkbook(local_settings.workbook_path)
    assert daily_stamps(book)[0] == "2005-08-19"
    second = run(local_settings, Options(as_of=date(2005, 8, 24), no_cache=True))
    assert second.exit_code == 0
    book = LocalWorkbook(local_settings.workbook_path)
    assert daily_stamps(book) == ["2005-08-23", "2005-08-22", "2005-08-21", "2005-08-20", "2005-08-19"]  # one run per gap, no duplicates, no holes
    assert daily_stamps(book, 28, 2) == ["2005-08-23", "2005-08-22", "2005-08-21", "2005-08-20", "2005-08-19"]
    third = run(local_settings, Options(as_of=date(2005, 8, 24), no_cache=True))  # nothing missing: nothing inserted
    assert third.exit_code == 0 and daily_stamps(LocalWorkbook(local_settings.workbook_path)) == daily_stamps(book)


def test_first_of_month_mode_writes_whole_months(local_settings):
    result = run(local_settings, Options(as_of=date(2005, 8, 1), mode="first-of-month", no_cache=True))
    assert result.exit_code == 0
    ws = LocalWorkbook(local_settings.workbook_path).worksheet("Daily Digest")
    assert [ws.acell(f"{c}54").value for c in "DEH"] == ["Jul 2005", "Jun 2005", "May 2005"]  # whole months, not "MTD" and "SPLM"
    july = result.frames["mtd_and_splm"].iloc[0]
    assert july["Month"] == "2005-07-01"  # column D is the last COMPLETE month, not the (empty) current one
    assert ws.acell("D56", "UNFORMATTED_VALUE").value == july["Rentals"] == 6709  # the whole of July: no day cut-off
    assert ws.acell("F56", "UNFORMATTED_VALUE").value == pytest.approx(6709 / ws.acell("E56", "UNFORMATTED_VALUE").value - 1)  # the % Change column


class FailingBook(LocalWorkbook):
    """A workbook whose digest tab refuses to be cleared (only the segment tables are cleared), like a sheet the service account lost
    access to mid-run."""

    def add_worksheet(self, title, rows=100, cols=26):
        ws = super().add_worksheet(title, rows, cols)
        if title == "Daily Digest":
            ws.faults["batch_clear"] = [APIError(403, "no access")] * 10
        return ws


@pytest.fixture
def failing_google(tmp_path, monkeypatch):
    book = FailingBook(tmp_path / "g.json")
    monkeypatch.setattr(pipeline, "open_spreadsheet", lambda s: book)
    return book


def test_a_sheets_error_that_survives_the_retries_stops_a_parity_run_and_keeps_the_cache(local_settings, failing_google, monkeypatch):
    monkeypatch.setattr(type(local_settings), "require_publish_settings", lambda self: None)
    with pytest.raises(APIError):
        run(local_settings, Options(publish=True, create_tabs=True, as_of=date(2005, 8, 24)), sleep=lambda s: None)
    assert "Revenue & Finance" not in {w.title for w in failing_google.worksheets()}  # the run stopped before the dashboard tabs that follow
    assert len(list(local_settings.cache_dir.glob("*.pkl"))) > 0  # so a rerun resumes from the cache instead of querying again


def test_in_strict_mode_one_failing_tab_does_not_stop_the_others(local_settings, failing_google, monkeypatch):
    monkeypatch.setattr(type(local_settings), "require_publish_settings", lambda self: None)
    result = run(local_settings, Options(publish=True, create_tabs=True, strict=True, as_of=date(2005, 8, 24)), sleep=lambda s: None)
    assert result.exit_code == 1 and not result.publish_ok
    assert failing_google.worksheet("Revenue & Finance").acell("A1").value == "Revenue by Category"  # written after the failure
    assert failing_google.worksheet("Daily Digest").acell("D3").value == "2005-08-23"


def test_parity_mode_carries_on_after_a_failed_query(local_settings, monkeypatch):
    real_fetch = pipeline.fetch_all

    def broken(db, sql, params=None):
        if sql.lstrip().startswith("-- country_split"):
            raise sqlite3.OperationalError("boom")
        return run_sql(db, sql, params)

    monkeypatch.setattr(pipeline, "fetch_all", lambda *a, **kw: real_fetch(*a, runner=broken, retries=0, **kw))
    result = run(local_settings, Options(as_of=date(2005, 8, 24), no_cache=True))
    assert result.exit_code == 1 and set(result.report.failures) == {"country_split"}
    book = LocalWorkbook(local_settings.workbook_path)
    sheet = book.worksheet("Daily Digest")
    assert sheet.acell("D3").value == "2005-08-23"  # everything that did succeed was written
    titles = [sheet.acell(f"A{r}").value for r in range(111, 200)]
    assert "Customers by Rentals per Month (all stores)" in titles and "Rentals by Film Rating per Month" in titles
    assert "Top Customer Countries per Month" not in titles  # the failed table is simply absent, not filled with zeros


# ---- the query families against an independent calculation ----
@pytest.fixture(scope="module")
def raw(settings):
    con = sqlite3.connect(settings.db_path)
    t = {n: pd.read_sql(f"select * from {n}", con) for n in ("payment", "rental", "inventory", "film", "film_category", "customer", "staff")}
    con.close()
    rental = t["rental"].assign(rental_date=lambda d: pd.to_datetime(d.rental_date), return_date=lambda d: pd.to_datetime(d.return_date))
    paid = t["payment"].dropna(subset=["rental_id"]).groupby("rental_id").agg(n_pay=("payment_id", "count"), amount=("amount", "sum")).reset_index()
    r = (rental.merge(t["staff"][["staff_id", "store_id"]], on="staff_id").merge(t["inventory"][["inventory_id", "film_id"]], on="inventory_id")
         .merge(t["film"][["film_id", "rental_duration"]], on="film_id").merge(paid, on="rental_id", how="left"))
    r["n_pay"], r["amount"] = r.n_pay.fillna(0), r.amount.fillna(0.0)
    r["day"], r["month"] = r.rental_date.dt.normalize(), r.rental_date.dt.strftime("%Y-%m-01")
    r["late"] = (r.return_date - r.rental_date).dt.total_seconds() / 86400 > r.rental_duration
    return r


def test_the_daily_frame_matches_pandas_for_every_day(report, raw):
    daily = report.results["daily"].set_index("Day")
    assert list(daily.index) == [f"2005-08-{d}" for d in range(23, 15, -1)]  # eight days, newest first
    for day, row in daily.iterrows():
        x = raw[raw.day == day]
        assert row["Rentals"] == len(x) and row["Active Customers"] == x.customer_id.nunique()
        assert row["Payments"] == x.n_pay.sum() and row["Revenue"] == pytest.approx(x.amount.sum())
        assert row["Unique Films Rented"] == x.film_id.nunique() and row["Unreturned Opened"] == x.return_date.isna().sum()


def test_the_per_store_daily_frame_adds_up_to_the_total_and_matches_pandas(report, raw):
    stores = report.results["daily_stores"]
    totals = stores.groupby("Day")[["Rentals", "Revenue"]].sum()
    assert totals["Rentals"].to_dict() == report.results["daily"].set_index("Day")["Rentals"].to_dict()
    for _, row in stores.iterrows():
        x = raw[(raw.day == row["Day"]) & (raw.store_id == int(row["Store"][-1]))]
        assert row["Rentals"] == len(x) and row["Revenue"] == pytest.approx(x.amount.sum())


def test_month_to_date_windows_are_like_for_like(report, raw):
    """As of 2005-08-24, every month counts only days 1..23, so August is compared with the same stretch of July, June, ..."""
    mtd = report.results["mtd_and_splm"].set_index("Month")
    inside = raw[(raw.rental_date.dt.day < 24) & (raw.rental_date >= "2005-04-01") & (raw.rental_date < "2005-08-24")]
    for month, group in inside.groupby("month"):
        assert mtd.loc[month, "Rentals"] == len(group) and mtd.loc[month, "Revenue"] == pytest.approx(group.amount.sum())
        assert mtd.loc[month, "Active Customers"] == group.customer_id.nunique()
        assert mtd.loc[month, "Late Returns"] == int(group.late[group.return_date.notna()].sum())


def test_the_per_store_month_windows_add_up_to_the_total(results):
    merged = merge_split_frames(results, "split")
    stores = merged["mtd_and_splm_stores"].groupby("Month")[["Rentals", "Revenue", "Payments"]].sum()
    total = merged["mtd_and_splm"].set_index("Month").loc[stores.index, ["Rentals", "Revenue", "Payments"]]
    pd.testing.assert_frame_equal(stores.astype(float), total.astype(float), check_exact=False)


def test_the_split_and_cte_variants_give_the_same_per_store_numbers(settings, params):
    """Two ways of writing the same result: running the two parts and merging, or one query over the whole window."""
    from sakila_reporting.fetch import fetch_all
    from sakila_reporting.registry import load_queries

    def pick(mode):
        wanted = {k: v for k, v in load_queries(settings.sql_dir, variant=mode).items() if "stores" in k and ("mtd" in k or "splm" in k)}
        return fetch_all(wanted, settings.db_path, None, heartbeat_seconds=999, params=params).results

    split, cte = merge_split_frames(pick("split"), "split"), pick("cte")
    for name in ("mtd_and_splm_stores", "mtd_and_splm_stores_returns", "mtd_and_splm_stores_first_seen"):
        a = split[name].sort_values(["Month", "Store"]).reset_index(drop=True)
        b = cte[name].sort_values(["Month", "Store"]).reset_index(drop=True)
        pd.testing.assert_frame_equal(a, b, check_dtype=False, check_exact=False)


def test_whole_months_in_first_of_month_mode_have_no_day_cutoff(settings):
    from sakila_reporting.fetch import fetch_all
    from sakila_reporting.registry import load_queries
    from sakila_reporting.windows import window_params
    q = {k: v for k, v in load_queries(settings.sql_dir).items() if k == "mtd_and_splm"}
    out = fetch_all(q, settings.db_path, None, heartbeat_seconds=999, params=window_params(date(2005, 8, 1), "first-of-month", 2)).results["mtd_and_splm"]
    con = sqlite3.connect(settings.db_path)
    july = con.execute("select count(*) from rental where rental_date >= '2005-07-01' and rental_date < '2005-08-01'").fetchone()[0]
    con.close()
    assert out.set_index("Month").loc["2005-07-01", "Rentals"] == july


def test_returns_split_is_consistent(report):
    r = report.results["daily_returns"]
    assert (r["Returned"] == r["Returned On Time"] + r["Returned Late"]).all()


def test_the_monthly_store_block_has_each_period_over_both_stores_and_a_percent_change_per_store(local_settings):
    assert run(local_settings, Options(as_of=date(2005, 8, 24), no_cache=True)).exit_code == 0
    ws = LocalWorkbook(local_settings.workbook_path).worksheet("Daily Digest")
    value = lambda cell: ws.acell(cell, "UNFORMATTED_VALUE").value  # noqa: E731
    assert [ws.acell(f"{c}83").value for c in "DFH"] == ["MTD Aug 2005", "SPLM Jul 2005", "% Change"]  # the period headings, merged over two columns
    assert [ws.acell(f"{c}84").value for c in "DEFGHI"] == ["STORE 1", "STORE 2"] * 3  # the store names repeat under each period
    assert [value(f"{c}86") for c in "DEFG"] == [2892, 2794, 1725, 1728]  # rentals: MTD store 1 / store 2, then SPLM store 1 / store 2
    assert value("H86") == pytest.approx(2892 / 1725 - 1) and value("I86") == pytest.approx(2794 / 1728 - 1)
    total = [ws.acell(f"{c}54").value for c in "DEFGHIJ"]
    # a gap column, then only the earlier months that have rentals: June has them, May and April (before Sakila's data starts) get no column
    assert total == ["MTD Aug 2005", "SPLM Jul 2005", "% Change", "", "SPLM Jun 2005", "", ""]
    assert [ws.acell(f"{c}56").value for c in "IJ"] == ["", ""] and ws.acell("H56").value == 2311  # nothing in the unused columns


def test_earlier_month_columns_exist_only_for_months_with_activity():
    from sakila_reporting.tabs import earlier_months
    months = pd.DataFrame({"Month": ["2005-08-01", "2005-07-01", "2005-06-01", "2005-05-01", "2005-04-01"], "Rentals": [9, 8, 7, 6, 5]})
    assert earlier_months({"mtd_and_splm": months}, date(2005, 8, 24), "daily") == 3
    months.loc[3, "Rentals"] = 0  # May is empty: April, which follows it, gets no column either (no gap in the earlier months)
    months.loc[4, "Rentals"] = 4
    assert earlier_months({"mtd_and_splm": months}, date(2005, 8, 24), "daily") == 1
    assert earlier_months({"mtd_and_splm": months.iloc[:2]}, date(2005, 8, 24), "daily") == 0  # nothing earlier than July at all
    assert earlier_months({}, date(2005, 8, 24), "daily") == 3  # the query failed: leave the layout alone
    whole = pd.DataFrame({"Month": ["2005-08-01", "2005-07-01", "2005-06-01", "2005-05-01", "2005-04-01"], "Rentals": [9, 8, 7, 6, 5]})
    assert earlier_months({"mtd_and_splm": whole}, date(2005, 9, 1), "first-of-month") == 3  # whole months: the slots are Aug, Jul, then Jun, May, Apr
