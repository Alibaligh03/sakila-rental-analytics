"""The Google Sheets publisher, against a fake spreadsheet that can rate-limit, refuse, or store the wrong numbers."""
import pytest

from sakila_reporting.payloads import SheetPayload
from sakila_reporting.sheets import SheetsPublisher, is_transient, verify_numbers, with_retry


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class APIError(Exception):
    """Same class name and shape as gspread's APIError, which is all the retry logic looks at."""

    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.response = FakeResponse(status)


class FakeWorksheet:
    def __init__(self, title, log, fail_first=None, store_wrong=False):
        self.title, self.log, self.fail_first, self.store_wrong = title, log, list(fail_first or []), store_wrong
        self.cells = {}

    def _maybe_fail(self, op):
        self.log.append(op)
        if self.fail_first:
            raise APIError(self.fail_first.pop(0))

    def batch_clear(self, ranges):
        self._maybe_fail("clear")

    def batch_update(self, updates, value_input_option=None):
        self._maybe_fail("update")
        assert value_input_option == "USER_ENTERED"
        self.last_updates = updates

    def batch_get(self, ranges, value_render_option=None):
        self.log.append("get")
        assert value_render_option == "UNFORMATTED_VALUE"
        out = []
        for u in self.last_updates:
            block = [list(row) for row in u["values"]]
            if self.store_wrong and block and isinstance(block[-1][-1], (int, float)):
                block[-1][-1] = block[-1][-1] + 1
            out.append(block)
        return out


class FakeSpreadsheet:
    def __init__(self, **worksheet_args):
        self.log, self.args = [], worksheet_args

    def worksheet(self, name):
        self.log.append(f"worksheet:{name}")
        self.ws = FakeWorksheet(name, self.log, **self.args)
        return self.ws


def payload(**kw):
    return SheetPayload("Revenue & Finance", updates=[{"range": "B3", "values": [["Category", "Revenue"], ["Sports", 5314.21], ["Travel", 3549.64]]}], **kw)


def publisher(spreadsheet, waits=None, **kw):
    kw.setdefault("mode", "transient")
    return SheetsPublisher(spreadsheet, retries=4, delay=2.0, sleep=(waits.append if waits is not None else lambda s: None), **kw)


def test_a_rate_limit_is_retried_with_growing_waits_then_succeeds():
    waits, sheet = [], FakeSpreadsheet(fail_first=[429, 503])
    result = publisher(sheet, waits).publish(payload())
    assert waits == [2.0, 4.0] and result.updates == 1 and sheet.log.count("update") == 3


@pytest.mark.parametrize("status", [400, 403, 404])
def test_configuration_errors_are_raised_at_once_not_retried(status):
    sheet = FakeSpreadsheet(fail_first=[status])
    with pytest.raises(APIError):
        publisher(sheet).publish(payload())
    assert sheet.log.count("update") == 1  # one attempt only


def test_giving_up_after_the_last_attempt_raises():
    sheet = FakeSpreadsheet(fail_first=[429] * 10)
    with pytest.raises(APIError):
        publisher(sheet).publish(payload())
    assert sheet.log.count("update") == 4


def test_only_api_errors_are_considered_transient():
    assert is_transient(APIError(429)) and is_transient(APIError(500)) and not is_transient(APIError(403))
    assert not is_transient(ValueError("429"))
    calls = []

    def boom():
        calls.append(1)
        raise ValueError("not an api error")

    with pytest.raises(ValueError):
        with_retry(boom, retries=5, sleep=lambda s: None)
    assert len(calls) == 1


def test_a_list_that_can_shrink_is_cleared_before_it_is_rewritten():
    sheet = FakeSpreadsheet()
    publisher(sheet).publish(payload(clear_ranges=["M3:O"]))
    assert sheet.log[:3] == ["worksheet:Revenue & Finance", "clear", "update"]


def test_no_clear_is_sent_when_there_is_nothing_to_clear():
    sheet = FakeSpreadsheet()
    publisher(sheet).publish(payload())
    assert "clear" not in sheet.log


def test_readback_confirms_the_numbers_landed():
    result = publisher(FakeSpreadsheet()).publish(payload())
    assert result.mismatches == [] and result.verified_cells == 2  # the two revenue figures; text is not compared


def test_readback_catches_a_number_that_was_stored_wrong():
    result = publisher(FakeSpreadsheet(store_wrong=True)).publish(payload())
    assert len(result.mismatches) == 1 and result.mismatches[0].startswith("C5:")  # the block starts at B3: header row 3, last data row 5


def test_readback_can_be_switched_off():
    sheet = FakeSpreadsheet()
    publisher(sheet, verify=False).publish(payload())
    assert "get" not in sheet.log


def test_an_empty_payload_makes_no_api_calls():
    sheet = FakeSpreadsheet()
    result = publisher(sheet).publish(SheetPayload("Operations", skipped=["staff_performance"]))
    assert result.updates == 0 and sheet.log == []


def test_verify_numbers_treats_missing_cells_and_text_correctly():
    updates = [{"range": "B3", "values": [["x", 1.5], ["y", 2]]}]
    assert verify_numbers(updates, [[["x", 1.5], ["y", 2]]]) == (2, [])
    compared, bad = verify_numbers(updates, [[["x", 1.5]]])  # the second row never arrived
    assert compared == 2 and len(bad) == 1 and "C4" in bad[0]
    assert verify_numbers(updates, [[["x", "1.5"], ["y", 2]]])[1] != []  # a number that came back as text is a mismatch
