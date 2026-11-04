"""Command line entry point: python -m sakila_reporting [--publish] [--as-of DATE] [--mode daily|first-of-month]

Default run: queries -> checks -> the local workbook (output/workbook/workbook.json), CSV files and an HTML dashboard in
output/<date>/. No credentials needed, and the workbook keeps its state between runs, so the daily backfill really does catch
up on missed days the next time you run it with a later --as-of.
--publish: write to a Google Sheet instead (needs GOOGLE_CREDENTIALS_PATH and GOOGLE_SHEET_ID).

Exit codes: 0 = everything succeeded; 1 = a query failed, a check failed, or writing had a problem; 2 = bad configuration.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from .config import QUERY_MODES, RUN_MODES, ConfigError, load_settings, parse_as_of
from .logging_utils import configure_logging
from .pipeline import Options, run
from .retry import MODES as RETRY_MODES


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m sakila_reporting", description="Sakila rental analytics pipeline.")
    target = p.add_mutually_exclusive_group()
    target.add_argument("--dry-run", action="store_true", help="write to the local workbook only (this is already the default; the flag is kept for clarity)")
    target.add_argument("--publish", action="store_true", help="write to Google Sheets instead of the local workbook")
    p.add_argument("--as-of", help="the day the run pretends to happen, YYYY-MM-DD (default: AS_OF_DATE or 2005-08-24)")
    p.add_argument("--mode", choices=RUN_MODES, help="daily: month-to-date like-for-like windows. first-of-month: whole calendar months")
    p.add_argument("--query-mode", choices=QUERY_MODES, help="split: the per-store month-to-date queries run as two parts and are merged. cte: one query each")
    p.add_argument("--strict", action="store_true", help="a failed check blocks every write, each tab is written in isolation, written numbers are read back")
    p.add_argument("--force", action="store_true", help="with --strict: write even though a check failed")
    p.add_argument("--no-verify", action="store_true", help="with --strict: skip the read-back")
    p.add_argument("--no-cache", action="store_true", help="ignore and do not write the per-query cache")
    p.add_argument("--only", nargs="+", metavar="QUERY", help="run only these queries (names of the files in sql/)")
    p.add_argument("--create-tabs", action="store_true", help="with --publish: create the missing tabs (never touches existing ones)")
    p.add_argument("--format-tabs", action="store_true", help="with --publish: (re)apply the design: header bands, number formats, widths, weekend colours")
    p.add_argument("--workers", type=int, help="parallel queries (default: MAX_WORKERS or 5)")
    p.add_argument("--retry-mode", choices=RETRY_MODES, help="all: retry any API error (default). transient: only 429 and 5xx, with backoff")
    p.add_argument("--workbook", help="path of the local workbook file (default: output/workbook/workbook.json)")
    p.add_argument("--output-dir", help="where CSVs, the HTML dashboard and the workbook go (default: output/)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    log = configure_logging(args.verbose)
    run_started = datetime.now()  # fixed once: a run that crosses midnight must not mix dates
    log.info("Run started %s", run_started.strftime("%Y-%m-%d %H:%M:%S"))

    try:
        settings = load_settings()
        if args.output_dir:
            out = Path(args.output_dir)
            settings = replace(settings, output_dir=out, workbook_path=out / "workbook" / "workbook.json")
        if args.workbook:
            settings = replace(settings, workbook_path=Path(args.workbook))
        if args.workers:
            settings = replace(settings, max_workers=args.workers)
        if args.retry_mode:
            settings = replace(settings, retry_mode=args.retry_mode)
        if args.publish:
            settings.require_publish_settings()
        options = Options(publish=args.publish, strict=args.strict, force=args.force, verify=not args.no_verify, no_cache=args.no_cache, only=args.only,
                          create_tabs=args.create_tabs, format_tabs=args.format_tabs, mode=args.mode,
                          query_mode=args.query_mode, as_of=parse_as_of(args.as_of) if args.as_of else None)
    except (ConfigError, FileNotFoundError) as exc:
        log.error("Configuration problem: %s", exc)
        return 2

    try:
        result = run(settings, options, run_started)
    except ValueError as exc:  # for example an unknown --only name
        log.error("%s", exc)
        return 2
    except Exception as exc:  # a Sheets error that survived the retries: the run stops here and the cache is kept for the rerun
        log.error("The run stopped: %s: %s", exc.__class__.__name__, exc)
        return 1

    checks_ok = all(c.passed for c in result.checks)
    log.info("Finished in %.1fs: %s", (datetime.now() - run_started).total_seconds(),
             "success" if result.exit_code == 0 else
             f"FAILED ({len(result.report.failures)} query failure(s), checks {'ok' if checks_ok else 'failed'}, "
             f"writes {'ok' if result.publish_ok else 'failed'})")
    return result.exit_code
