"""Runs the queries in parallel without letting one failure take the others down.

* Every query runs in its own thread with its own read-only connection.
* A failing query is recorded and reported; the rest still finish and are cached.
* Only transient 'database is locked / busy' errors are retried. A syntax error or a missing table is not going
  to fix itself, so retrying it would only hide the problem.
* A heartbeat line is logged while queries run, so a slow query and a hung one are distinguishable.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd

from .cache import QueryCache
from .db import run_sql
from .logging_utils import LOG
from .registry import Query

TRANSIENT_MARKERS = ("locked", "busy")


@dataclass
class FetchReport:
    results: dict[str, pd.DataFrame] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)
    from_cache: set[str] = field(default_factory=set)
    seconds: dict[str, float] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.failures


def _is_transient(exc: Exception) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and any(m in str(exc).lower() for m in TRANSIENT_MARKERS)


def _run_with_retry(runner, db_path: Path, sql: str, params, retries: int, delay: float, sleep: Callable[[float], None]) -> pd.DataFrame:
    for attempt in range(retries + 1):
        try:
            return runner(db_path, sql, params)
        except Exception as exc:
            if attempt < retries and _is_transient(exc):
                LOG.warning("Transient database error (%s); retrying in %.1fs (attempt %d)", exc, delay * 2**attempt, attempt + 1)
                sleep(delay * 2**attempt)
                continue
            raise
    raise AssertionError("unreachable")


def fetch_all(
    queries: dict[str, Query],
    db_path: Path,
    cache: QueryCache | None,
    *,
    max_workers: int = 6,
    heartbeat_seconds: float = 15.0,
    only: list[str] | None = None,
    retries: int = 2,
    retry_delay: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
    runner: Callable[..., pd.DataFrame] = run_sql,
    params: dict | None = None,
) -> FetchReport:
    selected = dict(queries)
    if only:
        unknown = sorted(set(only) - set(queries))
        if unknown:
            raise ValueError(f"Unknown query name(s): {', '.join(unknown)}")
        selected = {name: queries[name] for name in only}

    report = FetchReport()
    cached = cache.load() if cache else {}
    for name in selected:
        if name in cached:
            report.results[name] = cached[name]
            report.from_cache.add(name)
    if report.from_cache:
        LOG.info("Loaded %d cached result(s) from an earlier run today: %s", len(report.from_cache), ", ".join(sorted(report.from_cache)))

    pending = {name: q for name, q in selected.items() if name not in report.results}
    if not pending:
        return report

    running: set[str] = set()
    lock = threading.Lock()
    stop = threading.Event()

    def heartbeat() -> None:
        while not stop.wait(heartbeat_seconds):
            with lock:
                LOG.info("Heartbeat: %d running (%s), %d of %d finished", len(running), ", ".join(sorted(running)) or "-",
                         len(report.results) + len(report.failures) - len(report.from_cache), len(pending))

    def work(query: Query) -> tuple[str, pd.DataFrame, float]:
        with lock:
            running.add(query.name)
        started = time.time()
        try:
            LOG.info("Started:  %s", query.name)
            df = _run_with_retry(runner, db_path, query.sql, params, retries, retry_delay, sleep)
            return query.name, df, time.time() - started
        finally:
            with lock:
                running.discard(query.name)

    beat = threading.Thread(target=heartbeat, daemon=True)
    beat.start()
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(work, q): name for name, q in pending.items()}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    _, df, seconds = future.result()
                except Exception as exc:
                    with lock:
                        report.failures[name] = f"{exc.__class__.__name__}: {exc}"
                    LOG.error("Failed:   %s -> %s", name, report.failures[name])
                    continue
                with lock:
                    report.results[name] = df
                    report.seconds[name] = seconds
                if cache:
                    cache.save(name, df)  # successes only; a failure is never cached
                LOG.info("Finished: %s (%s rows in %.2fs)", name, f"{len(df):,}", seconds)
    finally:
        stop.set()
    return report
