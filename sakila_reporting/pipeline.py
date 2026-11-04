"""One run, start to finish, in the order that matters:

 1. open the target workbook (a local one by default, a Google spreadsheet with --publish) and make sure the tabs exist
 2. read each daily tab's last-filled date and work out how many days the daily queries must look back (BEFORE they are built)
 3. bind the date windows and run every query in parallel, with the per-query cache and failure isolation
 4. merge the split query variants, check the numbers, clean the frames
 5. write the tabs: the two daily tabs (one column per missing day), the month-to-date tabs, the full-replace tabs, the
    analytic tabs
 6. clear the cache if every query succeeded

Two behaviours, selected with --strict:

 parity (default)  a failed query leaves its block empty and the run carries on to write everything that did succeed; the
                   checks report problems loudly but do not stop the writes; a Sheets error that survives the retries stops the
                   run (and leaves the cache in place so a rerun resumes).
 strict            a failed check blocks every write; each tab's write is isolated so one tab failing does not stop the others;
                   the numbers written are read back and compared.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
import pandas as pd

from . import backfill, digest, formatting, gap, tabs
from .cache import QueryCache
from .checks import all_passed, run_checks, run_family_checks
from .config import Settings
from .fetch import FetchReport, fetch_all
from .logging_utils import LOG
from .offline import write_csvs, write_dashboard, write_payloads
from .payloads import build_payloads
from .registry import load_queries
from .retry import with_retry
from .sheets import SheetsPublisher, open_spreadsheet
from .windows import window_params, yesterday
from .workbook import LocalWorkbook

DATE_COLUMNS = ("Day", "Month", "Snapshot Date")
SPLIT_SUFFIXES = ("", "_cross_store_customers", "_first_seen", "_returns", "_open_and_closed")


@dataclass
class Options:
    publish: bool = False
    strict: bool = False
    force: bool = False
    verify: bool = True
    no_cache: bool = False
    only: list[str] | None = None
    create_tabs: bool = False
    format_tabs: bool = False
    as_of: date | None = None
    mode: str | None = None
    query_mode: str | None = None


@dataclass
class RunResult:
    success: bool
    report: FetchReport
    checks: list = field(default_factory=list)
    publish_ok: bool = True
    backfills: list = field(default_factory=list)
    frames: dict = field(default_factory=dict)
    exit_code: int = 0


def fingerprint(params: dict, query_mode: str) -> str:
    """Short tag for the cache: results are only reusable for the same windows and the same query set."""
    return hashlib.sha1(json.dumps([params, query_mode], sort_keys=True).encode()).hexdigest()[:8]


def clean_frames(results: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Infinities become 0 and missing values become empty cells. The first column of the daily and month frames becomes an ISO
    'YYYY-MM-DD' string. ISO matters: sent with USER_ENTERED to a spreadsheet whose locale is month-first, a 'dd/mm/yyyy' string
    such as '01/09/2005' is ambiguous, and Sheets silently reads every first-of-month row as the 9th of January."""
    cleaned = {}
    for name, df in results.items():
        df = df.replace([np.inf, -np.inf], 0)
        df = df.astype(object).where(df.notna(), "")
        if not df.empty and df.columns[0] in DATE_COLUMNS:
            try:
                df.isetitem(0, pd.to_datetime(df.iloc[:, 0], format="mixed").dt.strftime("%Y-%m-%d"))
            except Exception as exc:
                LOG.warning("Could not format the date column for %s: %s", name, exc)
        cleaned[name] = df
    return cleaned


def merge_split_frames(results: dict[str, pd.DataFrame], query_mode: str) -> dict[str, pd.DataFrame]:
    """In split mode the per-store month-to-date queries run as two parts (this month, same period last month). Merge each pair
    into the frame the tabs expect. Only if BOTH parts are empty is the merged frame empty (and a warning says so); if one part
    has no rows, the other is kept and the empty month is written as a real 0, because the columns are placed by month."""
    if query_mode != "split":
        return dict(results)
    out = {k: v for k, v in results.items() if not k.endswith("_SPLIT")}
    for suffix in SPLIT_SUFFIXES:
        mtd, splm = results.get(f"_mtd_stores{suffix}_SPLIT"), results.get(f"_splm_stores{suffix}_SPLIT")
        if mtd is None and splm is None:
            continue
        name = f"mtd_and_splm_stores{suffix}"
        parts = [p for p in (mtd, splm) if p is not None and not p.empty]
        if parts:
            out[name] = pd.concat(parts, ignore_index=True).sort_values(["Month", "Store"], ascending=[False, True]).reset_index(drop=True)
            LOG.info("Split queries merged -> %s%s", name, "" if len(parts) == 2 else " (one part had no rows; the missing month will be written as 0)")
        else:
            out[name] = pd.DataFrame()
            LOG.warning("Both split queries returned empty; %s is empty", name)
    return out


def _retry_kwargs(settings: Settings) -> dict:
    return {"retries": settings.sheets_retries, "delay": settings.sheets_retry_delay, "mode": settings.retry_mode}


def run(settings: Settings, options: Options, run_started: datetime | None = None, sleep=None) -> RunResult:
    run_started = run_started or datetime.now()
    as_of = options.as_of or settings.as_of
    mode, query_mode = options.mode or settings.mode, options.query_mode or settings.query_mode
    retry = _retry_kwargs(settings)
    if sleep:
        retry["sleep"] = sleep
    LOG.info("As-of date %s (yesterday = %s), mode %s, query mode %s, %s", as_of, yesterday(as_of), mode, query_mode, "STRICT" if options.strict else "parity")

    # 1. the target workbook
    local = None
    if options.publish:
        spreadsheet = open_spreadsheet(settings)
    else:
        local = LocalWorkbook(settings.workbook_path)
        spreadsheet = local
    if not options.publish or options.create_tabs:
        tabs.ensure_tabs(spreadsheet, tabs.ALL_NEW_TABS, retry)

    # 2. the gap, BEFORE the queries are built: it sizes their lookback window
    lookback = gap.compute_lookback_days(spreadsheet, [(b.tab, b.stamp) for b in backfill.DAY_BLOCKS], yesterday(as_of), retry)
    params = window_params(as_of, mode, lookback)

    # 3. the queries
    queries = load_queries(settings.sql_dir, variant=query_mode)
    cache = None if options.no_cache else QueryCache(settings.cache_dir, f"{run_started.date().isoformat()}-{fingerprint(params, query_mode)}")
    report = fetch_all(queries, settings.db_path, cache, max_workers=settings.max_workers, heartbeat_seconds=settings.heartbeat_seconds,
                       only=options.only, params=params)

    # 4. merge, check, clean
    merged = merge_split_frames(report.results, query_mode)
    checks = run_checks(report.results, queries, settings.db_path) + run_family_checks(merged, params)
    for c in checks:
        (LOG.info if c.passed else LOG.error)("check %-7s %-55s %s", "SKIPPED" if c.skipped else "PASS" if c.passed else "FAIL", c.name, c.detail)
    checks_ok = all_passed(checks)
    frames = clean_frames(merged)

    out = settings.output_dir / run_started.date().isoformat()
    write_csvs(report.results, out / "csv")
    write_dashboard(out / "dashboard.html", report.results, checks, run_started, report.failures)

    result = RunResult(success=False, report=report, checks=checks, frames=frames)
    blocked = options.strict and not checks_ok and not options.force
    if blocked:
        LOG.error("Refusing to write: a data check failed (strict mode). Fix the data or use --force.")
        result.publish_ok = False
    else:
        result.publish_ok = _write_everything(spreadsheet, frames, report, as_of, mode, lookback, run_started, retry, options, result, out)
    if local is not None:
        local.save()
        (out / "workbook.html").write_text(local.to_html(), encoding="utf-8")
        LOG.info("Workbook saved to %s", settings.workbook_path)

    result.success = report.ok and result.publish_ok and (checks_ok or not options.strict)
    success_for_cache = report.ok and result.publish_ok  # the cache is cleared whenever no query failed and the writes completed
    if cache and success_for_cache:
        cache.clear()
    elif cache and not report.ok:
        LOG.warning("At least one query failed this run; leaving the cache in place so a rerun can resume instead of redoing everything.")
    result.exit_code = 0 if (report.ok and result.publish_ok and checks_ok) else 1
    return result


def _write_everything(spreadsheet, frames, report, as_of, mode, lookback, run_started, retry, options, result, out) -> bool:
    ok = True

    def attempt(label, fn):
        """strict: one tab failing must not stop the others. parity: a Sheets error that survives the retries stops the run."""
        nonlocal ok
        if not options.strict:
            return fn()
        try:
            return fn()
        except Exception as exc:
            LOG.error("Writing '%s' failed: %s: %s", label, exc.__class__.__name__, exc)
            ok = False
            return None

    # the two daily blocks (each has its own columns on the digest tab)
    for block in backfill.DAY_BLOCKS:
        def daily(block=block):
            sheet = with_retry(spreadsheet.worksheet, block.tab, **retry)
            missing = gap.get_missing_days(sheet, block.stamp, yesterday(as_of), lookback, retry)
            return backfill.backfill_block(sheet, block, frames, missing, retry)

        r = attempt(block.name, daily)
        if r is not None:
            result.backfills.append(r)

    # the monthly blocks with their headings and % Change, and the segment tables stacked below them
    attempt("Monthly", lambda: tabs.write_month_tab(spreadsheet, digest.TAB, tabs.MONTHLY, frames, retry, as_of, mode))
    attempt("Segments", lambda: tabs.write_segments(spreadsheet, digest.TAB, frames, retry))

    # the analytic tabs (the original dashboard)
    payloads = build_payloads(report.results, run_started)
    write_payloads(out / "sheet_payloads.json", payloads)
    publisher = SheetsPublisher(spreadsheet, retries=retry["retries"], delay=retry["delay"], mode=retry["mode"], verify=options.strict and options.verify,
                                **({"sleep": retry["sleep"]} if "sleep" in retry else {}))
    for payload in payloads.values():
        if payload.skipped:
            LOG.warning("'%s': no result for %s; those blocks are left untouched", payload.sheet, ", ".join(payload.skipped))
        res = attempt(payload.sheet, lambda payload=payload: _publish_analytic(spreadsheet, publisher, payload, retry))
        if res is not None:
            for m in res.mismatches:
                LOG.error("read-back mismatch in '%s': %s", res.sheet, m)
                ok = False
    # the look of the sheet (header bands, number formats, widths, weekend colours). Cosmetic, so a failure only warns.
    if options.publish and (options.create_tabs or options.format_tabs):
        formatting.apply_design(spreadsheet, frames, retry)
    return ok


def _publish_analytic(spreadsheet, publisher, payload, retry):
    from .workbook import WorksheetNotFound  # the analytic tabs may not exist in a fresh local workbook: create them, empty
    try:
        spreadsheet.worksheet(payload.sheet)
    except Exception as exc:
        if isinstance(exc, WorksheetNotFound) or exc.__class__.__name__ == "WorksheetNotFound":
            spreadsheet.add_worksheet(title=payload.sheet, rows=100, cols=30)
        else:
            raise
    result = publisher.publish(payload)
    LOG.info("published '%s': %d updates, %d cells%s", result.sheet, result.updates, result.cells,
             f", {result.verified_cells} numeric cells verified" if result.verified_cells else "")
    return result

