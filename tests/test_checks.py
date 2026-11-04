"""The reconciliation checks pass on the real data, and each one fails when the data is made wrong."""
import pandas as pd

from sakila_reporting.checks import all_passed, run_checks


def outcome(results, queries, db, name):
    return next(c for c in run_checks(results, queries, db) if c.name == name)


def test_all_checks_pass_on_the_real_data(results, queries, settings):
    outcomes = run_checks(results, queries, settings.db_path)
    assert len(outcomes) == 17
    assert all_passed(outcomes), [o for o in outcomes if not o.passed]
    assert not any(o.skipped for o in outcomes)


def test_the_9_95_revenue_gap_is_explained_by_five_payments_without_a_rental(results, queries, settings):
    c = outcome(results, queries, settings.db_path, "executive-vs-breakdown revenue gap is fully explained")
    assert c.passed and "5 payment(s)" in c.detail and "9.95" in c.detail


def test_a_dropped_row_breaks_the_revenue_reconciliation(results, queries, settings):
    results["revenue_by_category"] = results["revenue_by_category"].iloc[1:]
    assert not outcome(results, queries, settings.db_path, "revenue breakdowns agree with each other").passed
    assert not outcome(results, queries, settings.db_path, "executive-vs-breakdown revenue gap is fully explained").passed


def test_a_duplicated_join_row_is_caught(results, queries, settings):
    df = results["staff_performance"]
    results["staff_performance"] = pd.concat([df, df.iloc[[0]]], ignore_index=True)  # what a fan-out join looks like
    assert not outcome(results, queries, settings.db_path, "rental counts agree").passed


def test_an_unsorted_result_is_caught_this_is_the_old_bug(results, queries, settings):
    results["revenue_by_category"] = results["revenue_by_category"].sort_values("Category")  # the order the old SQL really returned
    c = outcome(results, queries, settings.db_path, "results are sorted as the SQL declares")
    assert not c.passed and "revenue_by_category" in c.detail


def test_an_order_by_on_a_name_that_is_not_a_column_is_caught_this_is_the_old_bug(results, queries, settings):
    from dataclasses import replace
    broken = dict(queries)
    broken["revenue_by_category"] = replace(queries["revenue_by_category"], sql=queries["revenue_by_category"].sql.replace(
        'ORDER BY "Total Revenue" DESC', 'ORDER BY "Total Revenue ($)" DESC'))
    c = outcome(results, broken, settings.db_path, "ORDER BY names are real columns")
    assert not c.passed and "revenue_by_category" in c.detail and "Total Revenue ($)" in c.detail


def test_text_months_are_checked_for_order_too(results, queries, settings):
    results["monthly_revenue"] = results["monthly_revenue"].sort_values("Month")  # ascending, but the SQL says DESC
    assert not outcome(results, queries, settings.db_path, "results are sorted as the SQL declares").passed


def test_an_empty_result_is_caught(results, queries, settings):
    results["films_never_stocked"] = results["films_never_stocked"].iloc[0:0]
    assert not outcome(results, queries, settings.db_path, "every query returned rows").passed


def test_a_wrong_count_is_caught_against_the_independent_query(results, queries, settings):
    results["unreturned"] = pd.DataFrame({"Currently Unreturned": [182]})
    assert not outcome(results, queries, settings.db_path, "unreturned rentals match an independent query").passed


def test_a_short_top_list_is_caught(results, queries, settings):
    results["top_actors"] = results["top_actors"].iloc[:19]
    assert not outcome(results, queries, settings.db_path, "top-20 lists have 20 rows").passed


def test_a_failed_query_skips_the_checks_that_need_it_instead_of_crashing(results, queries, settings):
    del results["total_rentals"]
    c = outcome(results, queries, settings.db_path, "rental counts agree")
    assert c.skipped and "total_rentals" in c.detail


# ---- the three checks added after auditing every query: each must fail when its bug is put back ----
def test_inflated_segment_spend_is_caught(results, queries, settings):
    results["customer_segments"]["Avg Total Spend"] = results["customer_segments"]["Avg Total Spend"] * 18  # what the repeated-payment join produced
    c = outcome(results, queries, settings.db_path, "customer spend per segment adds up to all payments")
    assert not c.passed and "paid" in c.detail


def test_inflated_city_customer_counts_are_caught(results, queries, settings):
    results["top_cities_by_customers"].loc[0, "Customers"] = 50
    c = outcome(results, queries, settings.db_path, "city customer counts match an independent count")
    assert not c.passed and "50" in c.detail


def test_understated_actor_film_counts_are_caught(results, queries, settings):
    results["top_actors"].loc[0, "Films in Catalogue"] -= 2
    assert not outcome(results, queries, settings.db_path, "actor film counts match the film_actor table").passed


def test_a_wrong_never_stocked_list_is_caught(results, queries, settings):
    results["films_never_stocked"] = results["films_never_stocked"].iloc[1:]
    assert not outcome(results, queries, settings.db_path, "never-stocked films match an independent query").passed
