"""Publishing the analytic tabs to a spreadsheet: one atomic write per sheet, optionally followed by a read-back.

The retry policy lives in retry.py (see there for the two modes and for why insert_cols must never be retried as a whole).
Each sheet is written with one batch_update, which is atomic on Google's side: a sheet is either fully updated or not
changed. With verification on, the numeric cells are read back afterwards and compared with what was sent.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable

from . import a1
from .config import Settings
from .logging_utils import LOG
from .payloads import SheetPayload
from .retry import TRANSIENT_STATUS, http_status, is_transient, with_retry  # noqa: F401  (re-exported: tests and callers import them from here)


class PublishError(Exception):
    pass


@dataclass
class PublishResult:
    sheet: str
    updates: int
    cells: int
    cleared: list[str] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)
    verified_cells: int = 0
    dry_run: bool = False


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def verify_numbers(updates: list[dict], fetched: list[list[list]]) -> tuple[int, list[str]]:
    """Compare every numeric cell that was sent with what the sheet now holds. Returns (cells compared, mismatches).

    Text is not compared: with USER_ENTERED, Sheets may legitimately re-interpret a string (for example '2006-02'
    as a date), so only numbers are an exact, format-independent test.
    """
    if len(fetched) != len(updates):
        return 0, [f"the read-back returned {len(fetched)} ranges for {len(updates)} updates"]
    compared, mismatches = 0, []
    for update, got in zip(updates, fetched, strict=True):
        for r, row in enumerate(update["values"]):
            for c, expected in enumerate(row):
                if not _is_number(expected):
                    continue
                address = a1.cell_at(update["range"], r, c)
                actual = got[r][c] if r < len(got) and c < len(got[r]) else None
                compared += 1
                if not (_is_number(actual) and math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9)):
                    mismatches.append(f"{address}: sent {expected!r}, sheet has {actual!r}")
    return compared, mismatches


class SheetsPublisher:
    def __init__(self, spreadsheet, *, retries: int = 10, delay: float = 10.0, sleep: Callable[[float], None] = time.sleep,
                 verify: bool = True, mode: str = "all"):
        self.spreadsheet, self.retries, self.delay, self.sleep, self.verify, self.mode = spreadsheet, retries, delay, sleep, verify, mode

    def _call(self, fn, *args, **kwargs):
        return with_retry(fn, *args, retries=self.retries, delay=self.delay, sleep=self.sleep, mode=self.mode, **kwargs)

    def publish(self, payload: SheetPayload) -> PublishResult:
        result = PublishResult(payload.sheet, len(payload.updates), payload.cell_count)
        if not payload.updates:
            LOG.warning("Nothing to publish for '%s' (all of its queries are missing or empty)", payload.sheet)
            return result
        worksheet = self._call(self.spreadsheet.worksheet, payload.sheet)
        if payload.clear_ranges:  # lists that can shrink: remove the old values first so no stale rows are left behind
            self._call(worksheet.batch_clear, payload.clear_ranges)
            result.cleared = list(payload.clear_ranges)
        # fresh copies on every attempt: gspread rewrites each item's 'range' in place (it adds the sheet name), so re-sending the
        # same dicts fails every retry with a malformed range, and the read-back below would be handed the rewritten ranges
        self._call(lambda: worksheet.batch_update([dict(u) for u in payload.updates], value_input_option="USER_ENTERED"))
        if self.verify:
            rects = [a1.rect(u["range"], len(u["values"]), max(len(r) for r in u["values"])) for u in payload.updates]
            fetched = self._call(worksheet.batch_get, rects, value_render_option="UNFORMATTED_VALUE")
            result.verified_cells, result.mismatches = verify_numbers(payload.updates, [list(v) for v in fetched])
        return result


class DryRunPublisher:
    """Same interface as SheetsPublisher but makes no network calls; used by --dry-run."""

    def publish(self, payload: SheetPayload) -> PublishResult:
        return PublishResult(payload.sheet, len(payload.updates), payload.cell_count, cleared=list(payload.clear_ranges), dry_run=True)


def open_spreadsheet(settings: Settings):
    """Authenticate with a service account and open the target sheet. Imported lazily: only publishing needs Google."""
    settings.require_publish_settings()
    import gspread
    from google.oauth2.service_account import Credentials

    credentials = Credentials.from_service_account_file(str(settings.credentials_path), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    client = gspread.authorize(credentials)
    return with_retry(client.open_by_key, settings.sheet_id, retries=settings.sheets_retries, delay=settings.sheets_retry_delay)
