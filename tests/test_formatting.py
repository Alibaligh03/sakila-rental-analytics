"""The design layer: pure request builders, plus applying them to a spreadsheet that records what it is sent."""
import pandas as pd
import pytest

from sakila_reporting import digest
from sakila_reporting import formatting as f
from sakila_reporting.layout import SHEETS
from sakila_reporting.pipeline import clean_frames, merge_split_frames
from sakila_reporting.tabs import SEGMENT_SPECS
from sakila_reporting.workbook import APIError, LocalWorkbook

ALL_TABS = [*SHEETS, digest.TAB]


@pytest.fixture
def frames(report):
    return clean_frames(merge_split_frames(report.results, "split"))


def of(reqs, kind):
    return [r[kind] for r in reqs if kind in r]


def start_cells(reqs, fill):
    """(row, column) of the top-left cell of every range painted with this background, zero-based."""
    return {(r["range"]["startRowIndex"], r["range"]["startColumnIndex"]) for r in of(reqs, "repeatCell")
            if r["cell"]["userEnteredFormat"].get("backgroundColor") == fill}


def rules(reqs):
    return [r["rule"] for r in of(reqs, "addConditionalFormatRule")]


def formula(rule):
    return rule["booleanRule"]["condition"]["values"][0]["userEnteredValue"]


def digest_reqs(frames):
    return f.digest_requests(5, frames)


def test_grid_ranges_are_zero_based_and_exclusive_at_the_end():
    assert f.grid(7, "B3:D19") == {"sheetId": 7, "startRowIndex": 2, "endRowIndex": 19, "startColumnIndex": 1, "endColumnIndex": 4}
    assert f.grid(7, "C10") == {"sheetId": 7, "startRowIndex": 9, "endRowIndex": 10, "startColumnIndex": 2, "endColumnIndex": 3}


@pytest.mark.parametrize("name,values,kind", [
    ("Rentals", [3, 4], "int"), ("Total Revenue", [1.5, 2], "money"), ("Late Return Rate (%)", [0.5, 0.25], "pct"),
    ("Avg Days Overdue", [2, 3], "dec"), ("Customer", ["A", "B"], None), ("Rentals", [1, ""], "int"), ("Rentals", [], None)])
def test_each_column_gets_the_number_format_its_name_and_values_call_for(name, values, kind):
    assert f.kind_of(name, values) == kind


def test_every_cell_format_request_names_exactly_the_fields_it_sets(frames):
    """A mask that is wider than the payload would silently reset other formatting; one that is narrower would ignore it."""
    tabs = {t: {"id": i, "rows": 1000, "cols": 90, "rules": 0} for i, t in enumerate(ALL_TABS, start=1)}
    for repeat in of(f.design_requests(tabs, frames), "repeatCell"):
        fields = {x.split(".", 1)[1] for x in repeat["fields"].split(",")} if repeat["fields"] != "userEnteredFormat" else set()
        assert fields == set(repeat["cell"]["userEnteredFormat"]), repeat


def test_nothing_is_frozen_on_any_tab(frames):
    tabs = {t: {"id": i, "rows": 1000, "cols": 90, "rules": 0} for i, t in enumerate(ALL_TABS, start=1)}
    props = of(f.design_requests(tabs, frames), "updateSheetProperties")
    assert len(props) == len(ALL_TABS)
    assert all(p["properties"]["gridProperties"]["frozenRowCount"] == 0 and p["properties"]["gridProperties"]["frozenColumnCount"] == 0 for p in props)
    assert all(p["properties"]["gridProperties"]["hideGridlines"] for p in props)


def test_every_block_has_a_purple_title_band_starting_in_column_a(frames):
    starts = start_cells(digest_reqs(frames), f.PURPLE)
    for row in (digest.D_TITLE, digest.S_TITLE, digest.M_TITLE, digest.MS_TITLE, digest.SEG_TITLE):
        assert (row - 1, 0) in starts
    for row in (digest.D_DATE, digest.S_DATE, digest.M_HEAD, digest.MS_HEAD):  # and each block's header row below it
        assert (row - 1, 1) in starts


def test_the_daily_header_is_blue_on_saturday_and_yellow_on_sunday_by_rules_that_follow_the_dates(frames):
    weekday = [r for r in rules(digest_reqs(frames)) if "WEEKDAY" in formula(r)]
    assert sorted((formula(r), r["ranges"][0]["startRowIndex"] + 1) for r in weekday) == [
        ("=AND(ISNUMBER(C$28),WEEKDAY(C$28)=1)", 28), ("=AND(ISNUMBER(C$28),WEEKDAY(C$28)=7)", 28),
        ("=AND(ISNUMBER(C$3),WEEKDAY(C$3)=1)", 3), ("=AND(ISNUMBER(C$3),WEEKDAY(C$3)=7)", 3)]  # Sheets: Sunday 1 ... Saturday 7; a blank date is no weekday
    colours = {formula(r)[-2]: r["booleanRule"]["format"]["backgroundColor"] for r in weekday}
    assert colours == {"7": f.BLUE, "1": f.YELLOW}


def test_colour_rules_start_one_column_left_of_the_data_so_an_inserted_column_is_inside_them(frames):
    """The backfill inserts at column D. A rule that began AT D would be pushed right and leave the new column uncoloured."""
    checked = 0
    for rule in rules(digest_reqs(frames)):
        if "WEEKDAY" in formula(rule) or "ISNUMBER" in formula(rule):
            assert rule["ranges"][0]["startColumnIndex"] == 2
            checked += 1
    assert checked == 8


def test_each_day_is_coloured_green_or_red_against_the_previous_one_and_blanks_stay_white(frames):
    beats = sorted(formula(r) for r in rules(digest_reqs(frames)) if "ISNUMBER" in formula(r) and "WEEKDAY" not in formula(r))
    assert beats == ["=AND(ISNUMBER(C30),ISNUMBER(E30),C30<E30)", "=AND(ISNUMBER(C30),ISNUMBER(E30),C30>E30)",  # per store: two columns to the right
                     "=AND(ISNUMBER(C4),ISNUMBER(D4),C4<D4)", "=AND(ISNUMBER(C4),ISNUMBER(D4),C4>D4)"]          # daily: the next column
    fills = [r["booleanRule"]["format"]["backgroundColor"] for r in rules(digest_reqs(frames)) if "ISNUMBER" in formula(r) and "WEEKDAY" not in formula(r)]
    assert all(c in (f.GREEN, f.RED) for c in fills)


def test_the_store_block_has_lines_between_days_but_not_between_a_days_two_stores():
    reqs = f.day_borders(5, 29, 49, 3, 2)
    assert all("innerVertical" not in r["updateBorders"] for r in reqs)  # no line between Store 1 and Store 2 of one day
    pairs = [(r["updateBorders"]["range"]["startColumnIndex"], r["updateBorders"]["range"]["endColumnIndex"]) for r in reqs[1:]]
    assert pairs == [(3, 5), (5, 7), (7, 9)]  # one boundary per day: D:E, F:G, H:I
    assert len(f.day_borders(5, 4, 23, 3, 1)) == 1 and "innerVertical" in f.day_borders(5, 4, 23, 3, 1)[0]["updateBorders"]  # daily: a line per column


def test_the_sunday_header_has_dark_text_so_it_stays_readable_on_yellow(frames):
    sunday = next(r for r in rules(digest_reqs(frames)) if formula(r).endswith("=1)"))
    assert sunday["booleanRule"]["format"]["textFormat"]["foregroundColor"] == f.INK


def test_the_monthly_blocks_are_plain_except_for_the_colour_of_the_percent_change_text(frames):
    """No fill anywhere inside the monthly tables; only the % Change text turns green above zero and red below it."""
    monthly = [r for r in rules(digest_reqs(frames)) if r["ranges"][0]["startRowIndex"] + 1 >= digest.M_HEAD]
    assert monthly and all("backgroundColor" not in r["booleanRule"]["format"] for r in monthly)
    seen = sorted((r["booleanRule"]["condition"]["type"], r["ranges"][0]["startColumnIndex"] + 1, r["ranges"][0]["startRowIndex"] + 1) for r in monthly)
    assert seen == [("NUMBER_GREATER", 6, 55), ("NUMBER_GREATER", 8, 85), ("NUMBER_LESS", 6, 55), ("NUMBER_LESS", 8, 85)]  # F; and H:I, one per store
    assert {r["booleanRule"]["format"]["textFormat"]["foregroundColor"]["green"] for r in monthly} == {f.UP["green"], f.DOWN["green"]}


def test_the_monthly_store_block_merges_each_period_over_its_two_stores(frames):
    merged = of(digest_reqs(frames), "mergeCells")
    merges = {(m["range"]["startRowIndex"] + 1, m["range"]["startColumnIndex"] + 1, m["range"]["endColumnIndex"]) for m in merged}
    titles = {(row, 1, 3) for row in (digest.D_TITLE, digest.S_TITLE, digest.M_TITLE, digest.MS_TITLE, digest.SEG_TITLE)}  # the title bands: A:C
    assert merges == titles | {(83, 4, 5), (83, 6, 7), (83, 8, 9)}  # MTD, SPLM and % Change each merged over the two stores; daily date pairs: the backfill


def test_large_numbers_are_compact_and_percentages_keep_two_decimals(frames):
    patterns = {(r["range"]["startRowIndex"] + 1, r["range"]["startColumnIndex"] + 1): r["cell"]["userEnteredFormat"]["numberFormat"]["pattern"]
                for r in of(digest_reqs(frames), "repeatCell") if "numberFormat" in r["cell"]["userEnteredFormat"]}
    assert patterns[(7, 4)] == f.COMPACT_DEC and patterns[(4, 4)] == f.COMPACT_INT and patterns[(20, 4)] == "0.00%"  # revenue, rentals, new-customer share
    assert patterns[(56, 6)] == "0.00%" and patterns[(59, 4)] == f.COMPACT_DEC  # % Change; monthly revenue
    assert f.COMPACT_INT.startswith('[>=1000000]0.00,,"M";[>=1000]0.00,"K"')


def test_the_earlier_months_table_is_as_wide_as_the_months_that_have_columns(frames):
    def header_spans(earlier):
        reqs = f.design_requests({digest.TAB: {"id": 4, "rows": 1000, "cols": 200, "rules": 0}}, frames, earlier=earlier)
        return sorted((r["range"]["startColumnIndex"], r["range"]["endColumnIndex"]) for r in of(reqs, "repeatCell")
                      if r["range"]["startRowIndex"] == digest.M_HEAD - 1 and r["cell"]["userEnteredFormat"].get("backgroundColor") == f.PURPLE)
    assert header_spans(3) == [(1, 6), (7, 10)]  # B:F, then H:J
    assert header_spans(1) == [(1, 6), (7, 8)]   # B:F, then H alone
    assert header_spans(0) == [(1, 6)]           # no earlier months at all: no second table


def test_every_segment_table_has_its_own_title_band_and_header_row(frames):
    layout = digest.segment_layout(frames, SEGMENT_SPECS)
    assert len(layout) >= 4
    starts = start_cells(digest_reqs(frames), f.PURPLE)
    for _, _, row in layout:
        assert (row - 1, 0) in starts and (row, 1) in starts  # the title band in column A, the header row under it from column B


def test_the_overview_puts_the_operations_tables_beside_the_executive_summary(frames):
    reqs = f.dashboard_requests(1, SHEETS["Overview"], frames)
    headers = {(row + 1, col + 1) for row, col in start_cells(reqs, f.PURPLE)}
    assert (3, 11) in headers  # the staff-performance header starts in K3 (column 11)
    assert (3, 2) in headers  # the Executive Summary's KPI header is still B3


class Recorder:
    """A spreadsheet that records the requests it is sent, shaped like gspread's."""

    def __init__(self, tabs, fail=False):
        self.tabs, self.fail, self.sent = tabs, fail, []

    def fetch_sheet_metadata(self, params=None):
        return {"sheets": [{"properties": {"sheetId": i, "title": t, "gridProperties": {"rowCount": 1000, "columnCount": 200}},
                            "conditionalFormats": [{}] * n_rules} for i, (t, n_rules) in enumerate(self.tabs.items(), start=1)]}

    def worksheet(self, title):
        return self

    def get(self, rng, value_render_option=None):
        """The date rows: three days in the daily block, three in the store block (each date merged over two columns, so every second cell is blank)."""
        return [[38583, 38582, 38581]] if rng.endswith("3") or "D3:" in rng else [[38583, "", 38582, "", 38581]]

    def batch_update(self, body):
        if self.fail:
            raise APIError(403, "no access")
        self.sent.append(body)


def test_applying_the_design_resets_each_tab_first_so_running_it_twice_changes_nothing(frames):
    book = Recorder({digest.TAB: 2})  # the tab already has two colour rules
    assert f.apply_design(book, frames, {"retries": 1, "delay": 0}) is True
    requests = book.sent[0]["requests"]
    assert [r["deleteConditionalFormatRule"]["index"] for r in requests if "deleteConditionalFormatRule" in r] == [0, 0]  # removed, not duplicated
    assert sum(1 for r in requests if "addConditionalFormatRule" in r) == 12  # 4 + 4 for the daily blocks, 2 + 2 for the monthly ones
    assert len([r for r in requests if "repeatCell" in r and r["repeatCell"]["fields"] == "userEnteredFormat"]) == 1  # one clear-all-formats, first


def test_a_tab_that_does_not_exist_is_simply_skipped(frames):
    book = Recorder({"Some Other Tab": 0})
    assert f.apply_design(book, frames, {"retries": 1, "delay": 0}) is True and book.sent == []


def test_a_design_failure_is_reported_but_never_raised(frames):
    assert f.apply_design(Recorder({digest.TAB: 0}, fail=True), frames, {"retries": 1, "delay": 0}) is False


def test_the_local_workbook_has_no_visual_design(frames):
    assert f.apply_design(LocalWorkbook(), frames) is False


def test_an_empty_result_leaves_its_table_unformatted(frames):
    frames = {**frames, "top_20_films": pd.DataFrame()}
    reqs = f.dashboard_requests(2, SHEETS["Film Catalogue"], frames)
    formatted = {(r["range"]["startRowIndex"], r["range"]["startColumnIndex"]) for r in of(reqs, "repeatCell")}
    assert (2, 1) not in formatted  # B3, where the empty table would have started


def test_a_tab_that_is_too_narrow_is_widened_before_it_is_formatted(frames):
    """The digest tab is formatted 31 days ahead, which is more columns than a new tab has; formatting a missing column is an API error."""
    reqs = f.design_requests({digest.TAB: {"id": 4, "rows": 1000, "cols": 30, "rules": 0}}, frames, days=(31, 31))
    append = of(reqs, "appendDimension")
    assert len(append) == 1 and reqs[0] == {"appendDimension": append[0]}  # first, before anything that needs the columns
    assert 30 + append[0]["length"] == f.columns_needed(reqs[1:])
    wide = f.design_requests({digest.TAB: {"id": 4, "rows": 1000, "cols": 200, "rules": 0}}, frames, days=(31, 31))
    assert of(wide, "appendDimension") == []


def test_the_month_column_of_every_segment_table_is_shown_as_a_month_not_a_serial_number(frames):
    layout = digest.segment_layout(frames, SEGMENT_SPECS)
    formats = [r for r in of(digest_reqs(frames), "repeatCell") if r["cell"]["userEnteredFormat"].get("numberFormat", {}).get("pattern") == "mmm yyyy"]
    assert {r["range"]["startRowIndex"] for r in formats} == {row + 1 for _, _, row in layout}  # one per table, from the row under its header
    assert all(r["range"]["startColumnIndex"] == 1 for r in formats)


def test_only_the_columns_that_hold_days_are_formatted_and_a_blank_date_is_not_a_saturday(frames):
    assert f.day_counts(Recorder({digest.TAB: 0}), {"retries": 1, "delay": 0}) == (3, 3)
    three = f.design_requests({digest.TAB: {"id": 4, "rows": 1000, "cols": 200, "rules": 0}}, frames, days=(3, 3))
    one = f.design_requests({digest.TAB: {"id": 4, "rows": 1000, "cols": 200, "rules": 0}}, frames, days=(1, 1))
    def header_width(reqs):
        return max(r["range"]["endColumnIndex"] for r in of(reqs, "repeatCell") if r["range"]["startRowIndex"] == 2 and r["range"]["endColumnIndex"] > 3)

    header_width = {"three": header_width(three), "one": header_width(one)}
    assert header_width == {"three": 6, "one": 4}  # D3:F3 for three days, D3 for one: no purple stubs beyond the data
    assert all("ISNUMBER" in formula(r) for r in rules(three) if "WEEKDAY" in formula(r))
