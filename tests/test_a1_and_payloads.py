"""Cell addresses, and the sheet payloads: pinned against a golden file that was verified, update by update,
against what the original script wrote (the only differences are the four sort fixes and the header typo)."""
import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sakila_reporting import a1
from sakila_reporting.layout import SHEETS, Table, Transposed, Value
from sakila_reporting.payloads import build_payloads, clean_frame, frame_to_values, to_python

GOLDEN = Path(__file__).parent / "golden" / "sheet_payloads.json"
RUN = datetime(2026, 1, 15, 12, 0, 0)


@pytest.mark.parametrize("n,letters", [(1, "A"), (2, "B"), (26, "Z"), (27, "AA"), (52, "AZ"), (53, "BA"), (702, "ZZ"), (703, "AAA")])
def test_column_letters_round_trip(n, letters):
    assert a1.col_to_letters(n) == letters and a1.letters_to_col(letters) == n


def test_ranges():
    assert a1.rect("B3", 17, 3) == "B3:D19"
    assert a1.rect("C4", 1, 1) == "C4"
    assert a1.cell_at("B16", 2, 3) == "E18"
    assert a1.open_bottom("M3", 3) == "M3:O"
    with pytest.raises(ValueError):
        a1.split_cell("3B")


def test_values_are_plain_python_and_dates_are_formatted():
    assert to_python(np.int64(5)) == 5 and type(to_python(np.int64(5))) is int
    assert to_python(np.float64(1.5)) == 1.5
    assert to_python(pd.Timestamp("2026-03-09")) == "09/03/2026"
    assert to_python("text") == "text"


def test_infinity_and_missing_values_never_reach_the_sheet():
    df = clean_frame(pd.DataFrame({"a": [1.0, np.inf, np.nan], "b": ["x", None, "z"]}))
    assert frame_to_values(df) == [["a", "b"], [1.0, "x"], [0, ""], ["", "z"]]


def same(a, b):
    if isinstance(a, list):
        return isinstance(b, list) and len(a) == len(b) and all(same(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, float):
        return math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)
    return a == b


def test_payloads_match_the_golden_file(results):
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    built = build_payloads(results, RUN)
    assert set(built) == set(golden) == set(SHEETS)
    for sheet, expected in golden.items():
        assert [u["range"] for u in built[sheet].updates] == [u["range"] for u in expected["updates"]], sheet
        assert same([u["values"] for u in built[sheet].updates], [u["values"] for u in expected["updates"]]), sheet
        assert built[sheet].clear_ranges == expected["clear_ranges"], sheet


def test_the_dashboard_layout_has_not_moved(results):
    """These anchors are the contract with the sheet: the Executive Summary in columns A:G, the former Operations tab beside it from column J."""
    built = build_payloads(results, RUN)
    data = [u["range"] for u in built["Overview"].updates if not (len(u["values"]) == 1 and len(u["values"][0]) == 1 and isinstance(u["values"][0][0], str)
                                                                 and u["range"].rstrip("0123456789") in ("A", "B", "C") and u["range"] != "C12")]
    assert data == ["C4", "C5", "C6", "C7", "C8", "C9", "C10", "C11", "C12", "B16", "B25", "C37", "C38",
                    "J1", "K3", "J8", "K10", "Q1", "Q3", "W1", "W3"]
    assert [u["range"] for u in built["Revenue & Finance"].updates] == ["A1", "B3", "A21", "B23", "F1", "F3", "F7", "F9", "M1", "M3"]
    assert sum(len(p.updates) for p in built.values()) == 51 + 19  # the 51 data updates, plus the 19 fixed labels of the Executive Summary


def test_the_timestamp_cell_uses_the_run_start(results):
    c12 = next(u for u in build_payloads(results, RUN)["Overview"].updates if u["range"] == "C12")
    assert c12["values"] == [["2026-01-15 12:00:00"]]


def test_the_monthly_block_is_written_one_metric_per_row(results):
    update = next(u for u in build_payloads(results, RUN)["Overview"].updates if u["range"] == "B16")
    assert [row[0] for row in update["values"]] == results["monthly_revenue"].columns.tolist()
    assert len(update["values"]) == 6 and all(len(row) == 6 for row in update["values"])


def test_a_missing_query_leaves_its_blocks_untouched_instead_of_blanking_them(results):
    del results["top_actors"]
    results["films_never_stocked"] = results["films_never_stocked"].iloc[0:0]
    payload = build_payloads(results, RUN)["Film Catalogue"]
    assert payload.skipped == ["top_actors", "films_never_stocked"]
    assert all(u["range"] not in ("H1", "H3", "M1", "M3") for u in payload.updates)
    assert payload.clear_ranges == []  # nothing cleared for a block that is not rewritten


def test_open_ended_lists_clear_their_old_range_first(results):
    built = build_payloads(results, RUN)
    assert built["Revenue & Finance"].clear_ranges == ["M3:O"]
    assert built["Film Catalogue"].clear_ranges == ["M3:P"]
    assert built["Customers & Geography"].clear_ranges == ["P3:Q"]
    assert built["Overview"].clear_ranges == ["W3:AA"]  # the former Operations tab's N3:R, moved 9 columns right


def test_open_ended_blocks_are_the_lists_whose_length_can_change():
    open_ended = {b.query for blocks in SHEETS.values() for b in blocks if isinstance(b, Table) and b.open_ended}
    assert open_ended == {"revenue_by_month_category", "films_never_stocked", "customers_by_country", "inventory_by_store"}
    assert not any(isinstance(b, (Value, Transposed)) and getattr(b, "open_ended", False) for blocks in SHEETS.values() for b in blocks)
