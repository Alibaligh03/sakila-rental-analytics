"""The workbook stand-in, the retry policy, the date windows, the query registry and the settings."""
from datetime import date

import pytest

from sakila_reporting.config import ConfigError, load_dotenv, load_settings, parse_as_of
from sakila_reporting.registry import load_queries
from sakila_reporting.retry import with_retry
from sakila_reporting.windows import first_of_month, window_params
from sakila_reporting.workbook import APIError, LocalWorkbook, WorksheetNotFound


# ---- workbook ----
@pytest.fixture
def ws():
    return LocalWorkbook().add_worksheet("T")


def put(ws, rng, values):
    ws.batch_update([{"range": rng, "values": values}], value_input_option="USER_ENTERED")


def test_iso_text_becomes_a_real_date_like_sheets_does(ws):
    put(ws, "C10", [["2005-08-20"]])
    assert ws.acell("C10", "UNFORMATTED_VALUE").value == 38584 and ws.acell("C10").value == "2005-08-20"


def test_formulas_evaluate_and_divide_by_zero_like_sheets(ws):
    put(ws, "A1", [[10, 4, "=A1/B1", "=A1/C1"]])
    assert ws.acell("C1", "UNFORMATTED_VALUE").value == 2.5
    assert ws.acell("D1", "UNFORMATTED_VALUE").value == 4  # a formula may read another formula
    put(ws, "A2", [[10, 0, "=A2/B2"]])
    assert ws.acell("C2").value == "#DIV/0!"


def test_inserting_a_column_re_points_formulas(ws):
    put(ws, "C11", [[5]])
    put(ws, "C14", [[20]])
    put(ws, "C18", [["=C14/C11"]])
    ws.insert_cols([[]], col=3)
    assert ws.cells[(18, 4)] == ("f", "=D14/D11") and ws.acell("D18", "UNFORMATTED_VALUE").value == 4


def test_iferror_gives_a_blank_instead_of_an_error_like_sheets_does(ws):
    put(ws, "A1", [[10, 0, '=IFERROR(A1/B1-1,"")', '=IFERROR(A1/A1-1,"")', "=IFERROR(A1/2,0)"]])
    assert ws.acell("C1", "UNFORMATTED_VALUE").value == "" and ws.acell("D1", "UNFORMATTED_VALUE").value == 0
    assert ws.acell("E1", "UNFORMATTED_VALUE").value == 5


def test_inserting_cells_moves_only_the_rows_given_and_repoints_the_formulas_that_point_into_them(ws):
    put(ws, "C3", [["2005-08-22"]])
    put(ws, "C4", [[5]])
    put(ws, "C30", [[111]])  # another block, further down
    put(ws, "C11", [["=C4*2"]])
    block = {"sheetId": ws.id, "startRowIndex": 2, "endRowIndex": 23, "startColumnIndex": 2, "endColumnIndex": 3}
    ws.workbook.batch_update({"requests": [{"insertRange": {"range": block, "shiftDimension": "COLUMNS"}}]})
    assert ws.acell("D3").value == "2005-08-22" and ws.acell("C3").value == "" and ws.acell("C30").value == 111  # the block moved, the other did not
    assert ws.cells[(11, 4)] == ("f", "=D4*2")


def test_merges_and_unknown_requests(ws):
    ws.workbook.batch_update({"requests": [{"mergeCells": {"range": {"sheetId": ws.id, "startRowIndex": 27, "endRowIndex": 28, "startColumnIndex": 3,
                                                                  "endColumnIndex": 5}, "mergeType": "MERGE_ALL"}}]})
    assert ws.merges == [(28, 4, 28, 5)]
    with pytest.raises(APIError):
        ws.workbook.batch_update({"requests": [{"explode": {}}]})


def test_re_sending_the_same_dicts_fails_like_gspread_does(ws):
    updates = [{"range": "A1", "values": [[1]]}]
    ws.batch_update(updates, value_input_option="USER_ENTERED")
    with pytest.raises(APIError):
        ws.batch_update(updates, value_input_option="USER_ENTERED")  # the range was rewritten in place to 'T'!A1


def test_workbook_round_trips_through_its_file(tmp_path):
    book = LocalWorkbook(tmp_path / "w.json")
    sheet = book.add_worksheet("T")
    put(sheet, "A1", [["x", 2, "=B1*2"]])
    put(sheet, "A2", [["2005-08-20"]])
    book.save()
    again = LocalWorkbook(tmp_path / "w.json").worksheet("T")
    assert again.acell("C1", "UNFORMATTED_VALUE").value == 4 and again.acell("A2").value == "2005-08-20"
    with pytest.raises(WorksheetNotFound):
        again.workbook.worksheet("missing")


def test_batch_clear_only_touches_its_own_columns(ws):
    put(ws, "A1", [[1, 2, 3]])
    ws.batch_clear(["A:B"])
    assert ws.acell("A1").value == "" and ws.acell("C1").value == 3


# ---- retry ----
class Boom(Exception):
    pass


def flaky(statuses):
    calls = []

    def fn():
        calls.append(1)
        if statuses:
            raise APIError(statuses.pop(0))
        return "ok"

    return fn, calls


def test_mode_all_retries_any_api_error_at_a_fixed_delay():
    fn, calls, waits = *flaky([403, 404]), []
    assert with_retry(fn, retries=5, delay=7.0, sleep=waits.append, mode="all") == "ok"
    assert waits == [7.0, 7.0] and len(calls) == 3


def test_mode_all_gives_up_after_the_last_attempt():
    fn, calls = flaky([500] * 10)
    with pytest.raises(APIError):
        with_retry(fn, retries=3, delay=0, sleep=lambda s: None, mode="all")
    assert len(calls) == 3


def test_mode_transient_raises_permanent_errors_at_once_and_backs_off():
    fn, calls = flaky([403])
    with pytest.raises(APIError):
        with_retry(fn, retries=5, delay=2.0, sleep=lambda s: None, mode="transient")
    assert len(calls) == 1
    fn, calls, waits = *flaky([429, 503, 500]), []
    assert with_retry(fn, retries=6, delay=2.0, sleep=waits.append, mode="transient") == "ok"
    assert waits == [2.0, 4.0, 8.0]


def test_non_api_errors_are_never_retried_and_a_bad_mode_is_rejected():
    calls = []

    def fn():
        calls.append(1)
        raise Boom()

    with pytest.raises(Boom):
        with_retry(fn, retries=5, delay=0, sleep=lambda s: None, mode="all")
    assert len(calls) == 1
    with pytest.raises(ValueError):
        with_retry(fn, mode="sometimes")


# ---- windows ----
def test_first_of_month_crosses_year_boundaries():
    assert first_of_month(date(2005, 2, 10), 0) == date(2005, 2, 1)
    assert first_of_month(date(2005, 2, 10), 2) == date(2004, 12, 1)
    assert first_of_month(date(2005, 1, 31), 13) == date(2003, 12, 1)


def test_daily_windows_are_month_to_date_and_like_for_like():
    p = window_params(date(2005, 8, 24), "daily", 3)
    assert (p["m5_start"], p["m2_start"], p["win_end"], p["day_cutoff"]) == ("2005-04-01", "2005-07-01", "2005-08-24", 24)
    assert (p["mtd_start"], p["mtd_end"], p["splm_start"], p["splm_end"]) == ("2005-08-01", "2005-08-24", "2005-07-01", "2005-08-01")
    assert p["lookback_days"] == 3


def test_first_of_month_windows_are_whole_months_before_the_as_of_month():
    p = window_params(date(2005, 8, 1), "first-of-month", 2)
    assert (p["m5_start"], p["win_end"], p["day_cutoff"]) == ("2005-03-01", "2005-08-01", 32)
    assert (p["mtd_start"], p["mtd_end"], p["splm_start"], p["splm_end"]) == ("2005-07-01", "2005-08-01", "2005-06-01", "2005-07-01")


def test_an_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        window_params(date(2005, 8, 24), "weekly", 2)


# ---- registry ----
def test_the_variant_header_decides_which_queries_load(tmp_path):
    (tmp_path / "a.sql").write_text("-- a: always\nSELECT 1 AS x", encoding="utf-8")
    (tmp_path / "c_cte.sql").write_text("-- c\n-- variant: cte\nSELECT 1 AS x", encoding="utf-8")
    (tmp_path / "c_split.sql").write_text("-- c\n-- variant: split\nSELECT 1 AS x", encoding="utf-8")
    assert set(load_queries(tmp_path)) == {"a", "c_split"}
    assert set(load_queries(tmp_path, variant="cte")) == {"a", "c_cte"}


# ---- settings ----
def test_as_of_date_and_modes_are_validated(tmp_path):
    (tmp_path / "sqlite-sakila.db").write_bytes(b"")
    assert parse_as_of("2005-08-20") == date(2005, 8, 20) and parse_as_of(None) == date(2005, 8, 24)
    with pytest.raises(ConfigError, match="AS_OF_DATE"):
        parse_as_of("20/08/2005")
    with pytest.raises(ConfigError, match="RUN_MODE"):
        load_settings(tmp_path, env={"RUN_MODE": "weekly"})
    with pytest.raises(ConfigError, match="QUERY_MODE"):
        load_settings(tmp_path, env={"QUERY_MODE": "both"})
    with pytest.raises(ConfigError, match="SHEETS_RETRY_MODE"):
        load_settings(tmp_path, env={"SHEETS_RETRY_MODE": "never"})
    s = load_settings(tmp_path, env={"RUN_MODE": "FIRST-OF-MONTH", "AS_OF_DATE": "2005-07-01"})
    assert (s.mode, s.as_of) == ("first-of-month", date(2005, 7, 1))


def test_a_byte_order_mark_does_not_hide_the_first_variable(tmp_path):
    env = tmp_path / ".env"
    env.write_bytes("﻿GOOGLE_SHEET_ID=abc\nAS_OF_DATE=2005-08-20\n".encode())
    assert load_dotenv(env) == {"GOOGLE_SHEET_ID": "abc", "AS_OF_DATE": "2005-08-20"}
