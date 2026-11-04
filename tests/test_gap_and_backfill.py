"""Gap detection and the daily backfill: one column per missing day, newest on the left, in two blocks that share one tab, with every
failure mode of the sheet write handled the way the tab needs it (the tab itself is the only record of what has been done)."""
from datetime import date

import pandas as pd
import pytest

from sakila_reporting import backfill, digest, gap, tabs
from sakila_reporting.pipeline import clean_frames, merge_split_frames
from sakila_reporting.workbook import APIError, LocalWorkbook

QUIET = {"retries": 2, "delay": 0.0, "sleep": lambda s: None, "mode": "all"}
TARGETS = [(digest.TAB, "D3"), (digest.TAB, "D28")]


def put(ws, cell, value):
    ws.batch_update([{"range": cell, "values": [[value]]}], value_input_option="USER_ENTERED")


@pytest.fixture
def frames(report):
    return clean_frames(merge_split_frames(report.results, "split"))


@pytest.fixture
def book():
    wb = LocalWorkbook()
    tabs.ensure_tabs(wb, [digest.TAB], QUIET)
    return wb


@pytest.fixture
def ws(book):
    return book.worksheet(digest.TAB)


# ---- reading the last-filled date
@pytest.mark.parametrize("value,expected", [
    ("2005-08-20", date(2005, 8, 20)), ("20/08/2005", date(2005, 8, 20)), (38584, date(2005, 8, 20)), (38584.0, date(2005, 8, 20)),
    ("", None), (None, None), ("not a date", None), (True, None)])
def test_parse_sheet_date(value, expected):
    assert gap.parse_sheet_date(value) == expected


def test_lookback_is_the_size_of_the_gap_with_a_floor_of_one(book, ws):
    put(ws, "D3", "2005-08-20")
    put(ws, "D28", "2005-08-22")
    assert gap.compute_lookback_days(book, TARGETS, date(2005, 8, 23), QUIET) == 3  # the biggest gap in any block
    put(ws, "D3", "2005-08-23")
    put(ws, "D28", "2005-08-23")
    assert gap.compute_lookback_days(book, TARGETS, date(2005, 8, 23), QUIET) == 1  # up to date: still look back one day


def test_lookback_defaults_to_two_days_when_a_stamp_is_unreadable_and_caps_garbage(book, ws):
    assert gap.compute_lookback_days(book, TARGETS, date(2005, 8, 23), QUIET) == 2  # blank stamps: the very first run
    put(ws, "D3", "1990-01-01")
    put(ws, "D28", "2005-08-22")
    assert gap.compute_lookback_days(book, TARGETS, date(2005, 8, 23), QUIET) == gap.MAX_DAYS


def test_lookback_falls_back_when_the_spreadsheet_cannot_be_reached():
    class Broken:
        def worksheet(self, name):
            raise APIError(403, "no access")

    assert gap.compute_lookback_days(Broken(), TARGETS, date(2005, 8, 23), {**QUIET, "retries": 1}) == gap.DEFAULT_DAYS


def test_missing_days_are_listed_oldest_first_and_truncated_to_the_lookback(ws):
    put(ws, "D3", "2005-08-20")
    assert gap.get_missing_days(ws, "D3", date(2005, 8, 23), 3, QUIET) == [date(2005, 8, 21), date(2005, 8, 22), date(2005, 8, 23)]
    assert gap.get_missing_days(ws, "D3", date(2005, 8, 23), 2, QUIET) == [date(2005, 8, 22), date(2005, 8, 23)]  # only what the queries covered
    put(ws, "D3", "2005-08-23")
    assert gap.get_missing_days(ws, "D3", date(2005, 8, 23), 3, QUIET) == []
    put(ws, "D3", "")
    assert gap.get_missing_days(ws, "D3", date(2005, 8, 23), 3, QUIET) == [date(2005, 8, 23)]


def test_filter_by_day_survives_real_datetimes():
    df = pd.DataFrame({"Day": pd.to_datetime(["2005-08-23", "2005-08-22"]), "Rentals": [1, 2]})
    assert gap.filter_by_day(df, date(2005, 8, 22))["Rentals"].tolist() == [2]
    assert gap.filter_by_day(pd.DataFrame(), date(2005, 8, 22)).empty


# ---- the daily block
def test_a_gap_is_filled_one_column_per_day_newest_on_the_left(ws, frames):
    put(ws, "D3", "2005-08-20")
    missing = [date(2005, 8, 21), date(2005, 8, 22), date(2005, 8, 23)]
    result = backfill.backfill_block(ws, backfill.DAILY, frames, missing, QUIET)
    assert result.written == missing and result.stopped is None
    assert [ws.acell(f"{c}3").value for c in "DEFG"] == ["2005-08-23", "2005-08-22", "2005-08-21", "2005-08-20"]
    assert ws.acell("D4").value == 598 and ws.acell("E4").value == 626  # rentals per day, from the daily query
    assert ws.cells[(11, 4)] == ("f", "=IFERROR(D7/D4,0)") and ws.cells[(11, 5)] == ("f", "=IFERROR(E7/E4,0)")  # each column's ratios point at its own
    assert ws.acell("G3").value == "2005-08-20"  # the pre-existing day moved right, untouched


def test_adding_a_day_to_one_block_leaves_the_other_block_exactly_where_it_was(ws, frames):
    put(ws, "D28", "2005-08-22")
    put(ws, "D30", 111)
    put(ws, "E30", 222)
    put(ws, "D3", "2005-08-22")
    backfill.backfill_block(ws, backfill.DAILY, frames, [date(2005, 8, 23)], QUIET)
    assert (ws.acell("D28").value, ws.acell("D30").value, ws.acell("E30").value) == ("2005-08-22", 111, 222)  # not shifted
    assert ws.acell("D3").value == "2005-08-23" and ws.acell("E3").value == "2005-08-22"


def test_the_store_block_writes_two_columns_per_day_merged_under_one_date(ws, frames):
    put(ws, "D28", "2005-08-22")
    result = backfill.backfill_block(ws, backfill.DAILY_STORES, frames, [date(2005, 8, 23)], QUIET)
    assert result.written == [date(2005, 8, 23)]
    assert ws.acell("D28").value == "2005-08-23" and ws.acell("E28").value == ""  # the date is written once, into the first column of the pair
    assert (28, 4, 28, 5) in ws.merges  # and merged across the pair
    assert [ws.acell(f"{c}29").value for c in "DE"] == ["STORE 1", "STORE 2"]  # the store names sit under the date, in capitals
    assert (ws.acell("D30").value, ws.acell("E30").value) == (299, 299)
    assert ws.acell("F28").value == "2005-08-22"  # two columns were inserted, so the old day is now in F


def test_a_store_with_no_rentals_that_day_gets_zeros_not_the_other_stores_numbers():
    day = date(2005, 8, 23)
    main = pd.DataFrame({"Day": ["2005-08-23"], "Store": ["Store 2"], "Rentals": [7], "Active Customers": [5], "Payments": [7], "Revenue": [30.0],
                         "Avg Payment": [4.3], "Unique Films Rented": [6], "Unique Categories Rented": [4], "Unreturned Opened": [0],
                         "Largest Payment": [9.99], "Paying Customers": [5]})
    by_range = {u["range"]: u["values"] for u in backfill.daily_stores_updates(day, {"daily_stores": main})}
    assert by_range["D30:D36"][0] == [0] and by_range["E30:E36"][0] == [7]  # rentals: Store 1 had none, Store 2 had 7


class Recorder:
    """A sheet that records the requests the insert sends."""
    id = 9

    def __init__(self):
        self.sent = []
        self.spreadsheet = self

    def batch_update(self, body):
        self.sent.append(body["requests"])


def test_inserting_a_day_is_one_request_that_moves_one_block_and_copies_the_neighbours_formats():
    daily, stores = Recorder(), Recorder()
    backfill.insert_day(daily, backfill.DAILY)
    backfill.insert_day(stores, backfill.DAILY_STORES)
    assert [list(r)[0] for r in daily.sent[0]] == ["insertRange", "copyPaste"]
    assert [list(r)[0] for r in stores.sent[0]] == ["insertRange", "copyPaste", "mergeCells"]
    moved = daily.sent[0][0]["insertRange"]
    assert moved["shiftDimension"] == "COLUMNS"
    assert moved["range"] == {"sheetId": 9, "startRowIndex": 2, "endRowIndex": 23, "startColumnIndex": 3, "endColumnIndex": 4}
    assert stores.sent[0][0]["insertRange"]["range"]["endColumnIndex"] == 5 and stores.sent[0][0]["insertRange"]["range"]["startRowIndex"] == 27
    assert stores.sent[0][1]["copyPaste"]["pasteType"] == "PASTE_FORMAT"
    assert stores.sent[0][2]["mergeCells"]["range"] == {"sheetId": 9, "startRowIndex": 27, "endRowIndex": 28, "startColumnIndex": 3, "endColumnIndex": 5}


def test_a_failed_insert_that_changed_nothing_deletes_nothing(ws, frames):
    put(ws, "D3", "2005-08-22")
    ws.faults["insert_range"] = [APIError(500)]
    result = backfill.backfill_block(ws, backfill.DAILY, frames, [date(2005, 8, 23)], QUIET)
    assert result.stopped == "insert failed" and ws.calls.count("insert_range") == 1  # never retried
    assert "delete_range" not in ws.calls  # columns that hold a real day must not be removed
    assert ws.acell("D3").value == "2005-08-22"


def test_an_insert_whose_reply_was_lost_is_cleaned_up_so_the_next_run_can_resume(ws, frames):
    put(ws, "D3", "2005-08-22")
    ws.faults["insert_range"] = [(APIError(500), "commit")]  # the cells moved, then the call failed
    result = backfill.backfill_block(ws, backfill.DAILY, frames, [date(2005, 8, 23)], QUIET)
    assert result.stopped == "insert failed" and "delete_range" in ws.calls
    assert ws.acell("D3").value == "2005-08-22"  # the stamp is readable again, so the next run resumes from the right day


def test_trailing_days_with_no_source_data_are_never_stamped(ws, frames):
    """If nothing later has rows either, the data may just not be loaded yet: stamping would mark the day covered while blank, for ever."""
    put(ws, "D3", "2005-08-20")
    not_loaded = {**frames, "daily": frames["daily"][~frames["daily"]["Day"].isin(["2005-08-22", "2005-08-23"])]}
    result = backfill.backfill_block(ws, backfill.DAILY, not_loaded, [date(2005, 8, 21), date(2005, 8, 22), date(2005, 8, 23)], QUIET)
    assert result.written == [date(2005, 8, 21)] and result.stopped == "no data for the day"
    assert ws.acell("D3").value == "2005-08-21"  # stops on the last complete day; the empty column was removed
    assert ws.acell("E3").value == "2005-08-20"


def test_a_day_with_no_rentals_between_days_that_have_some_is_a_real_zero_day(ws, frames):
    """Sakila has quiet stretches. A day with no rows but later days WITH rows really had no activity, so it is written as zeros."""
    put(ws, "D3", "2005-08-20")
    quiet = {**frames, "daily": frames["daily"][frames["daily"]["Day"] != "2005-08-22"]}
    result = backfill.backfill_block(ws, backfill.DAILY, quiet, [date(2005, 8, 21), date(2005, 8, 22), date(2005, 8, 23)], QUIET)
    assert result.written == [date(2005, 8, 21), date(2005, 8, 22), date(2005, 8, 23)] and result.stopped is None
    assert [ws.acell(f"{c}3").value for c in "DEF"] == ["2005-08-23", "2005-08-22", "2005-08-21"]
    assert [ws.acell(f"E{r}").value for r in (4, 7, 14)] == [0, 0, 0]  # the quiet day: rentals, revenue and open rentals are real zeros
    assert ws.acell("E11", "UNFORMATTED_VALUE").value == 0  # its ratios are 0, not #DIV/0!


def test_a_failed_write_removes_the_columns_just_inserted(ws, frames):
    put(ws, "D3", "2005-08-22")
    ws.faults["batch_update"] = [APIError(500), APIError(500)]  # both attempts fail
    result = backfill.backfill_block(ws, backfill.DAILY, frames, [date(2005, 8, 23)], QUIET)
    assert result.stopped == "write failed" and result.written == []
    assert ws.acell("D3").value == "2005-08-22"


def test_a_retried_write_uses_fresh_range_dicts(ws, frames):
    """gspread rewrites each item's 'range' in place. A fault that fires AFTER the write rewrote the dicts reproduces it: the retry only
    succeeds because every attempt gets fresh copies."""
    put(ws, "D3", "2005-08-22")
    ws.faults["batch_update"] = [(APIError(500), "commit")]
    before = ws.calls.count("batch_update")
    result = backfill.backfill_block(ws, backfill.DAILY, frames, [date(2005, 8, 23)], QUIET)
    assert result.written == [date(2005, 8, 23)] and ws.calls.count("batch_update") - before == 2  # the failed attempt, then the retry
    assert ws.acell("D4").value == 598


def test_nothing_missing_is_a_no_op(ws, frames):
    result = backfill.backfill_block(ws, backfill.DAILY, frames, [], QUIET)
    assert result.written == [] and "insert_range" not in ws.calls


def test_a_failed_side_query_leaves_its_cells_blank_instead_of_crashing(ws, frames):
    """Failure isolation: a query that failed arrives as an empty frame. The day is still written from the queries that worked."""
    put(ws, "D28", "2005-08-22")
    broken = {**frames, "daily_stores_cross_store_customers": pd.DataFrame(), "daily_stores_returns": pd.DataFrame(),
              "daily_stores_first_seen": pd.DataFrame()}
    result = backfill.backfill_block(ws, backfill.DAILY_STORES, broken, [date(2005, 8, 23)], QUIET)
    assert result.written == [date(2005, 8, 23)]
    assert ws.acell("D30").value == 299 and ws.acell("D43").value == "" and ws.acell("D47").value == ""


def test_a_query_that_ran_and_found_nothing_gives_zeros_but_one_that_failed_leaves_blanks(ws, frames):
    """'Nobody was first seen that day' is a real 0; a failed query (an empty frame with no columns) must never be filled with zeros."""
    assert frames["daily_first_seen"].empty and frames["daily_first_seen"].shape[1] > 0  # it ran, in this window nobody was new
    put(ws, "D3", "2005-08-22")
    backfill.backfill_block(ws, backfill.DAILY, frames, [date(2005, 8, 23)], QUIET)
    assert (ws.acell("D18").value, ws.acell("D19").value) == (0, 0)
    failed = {**frames, "daily_first_seen": pd.DataFrame(), "daily_returns": pd.DataFrame()}
    put(ws, "D3", "2005-08-22")
    backfill.backfill_block(ws, backfill.DAILY, failed, [date(2005, 8, 23)], QUIET)
    assert ws.acell("D18").value == "" and ws.acell("D21").value == ""
