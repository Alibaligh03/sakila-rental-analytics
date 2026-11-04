"""Failure isolation, the retry rule and the cache rules."""
import pickle
import sqlite3
import time
from datetime import date

import pandas as pd
import pytest

from sakila_reporting.cache import QueryCache
from sakila_reporting.fetch import fetch_all
from sakila_reporting.registry import Query


def q(name):
    return Query(name=name, sql=f"SELECT '{name}' AS n", description=name)


QUERIES = {n: q(n) for n in ("alpha", "beta", "gamma")}


def fake_runner(fail=(), calls=None):
    def run(db, sql, params=None):
        name = sql.split("'")[1]
        if calls is not None:
            calls.append(name)
        if name in fail:
            raise ValueError(f"boom in {name}")
        return pd.DataFrame({"n": [name]})
    return run


def test_one_failing_query_does_not_stop_the_others(tmp_path):
    report = fetch_all(QUERIES, tmp_path, None, runner=fake_runner(fail={"beta"}), heartbeat_seconds=999)
    assert set(report.results) == {"alpha", "gamma"}
    assert list(report.failures) == ["beta"] and "boom in beta" in report.failures["beta"]
    assert not report.ok


def test_only_successes_are_cached(tmp_path):
    cache = QueryCache(tmp_path / "c", date(2026, 1, 1))
    fetch_all(QUERIES, tmp_path, cache, runner=fake_runner(fail={"beta"}), heartbeat_seconds=999)
    assert sorted(p.name for p in (tmp_path / "c").glob("*.pkl")) == ["2026-01-01__alpha.pkl", "2026-01-01__gamma.pkl"]


def test_a_rerun_only_repeats_what_failed(tmp_path):
    cache = QueryCache(tmp_path / "c", date(2026, 1, 1))
    fetch_all(QUERIES, tmp_path, cache, runner=fake_runner(fail={"beta"}), heartbeat_seconds=999)
    calls = []
    report = fetch_all(QUERIES, tmp_path, cache, runner=fake_runner(calls=calls), heartbeat_seconds=999)
    assert calls == ["beta"] and report.ok and report.from_cache == {"alpha", "gamma"}


def test_unknown_query_names_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="nope"):
        fetch_all(QUERIES, tmp_path, None, only=["alpha", "nope"], runner=fake_runner())


def test_only_runs_the_selected_queries(tmp_path):
    calls = []
    fetch_all(QUERIES, tmp_path, None, only=["gamma"], runner=fake_runner(calls=calls))
    assert calls == ["gamma"]


def test_a_locked_database_is_retried_with_backoff(tmp_path):
    attempts, waits = [], []

    def flaky(db, sql, params=None):
        attempts.append(1)
        if len(attempts) < 3:
            raise sqlite3.OperationalError("database is locked")
        return pd.DataFrame({"n": [1]})

    report = fetch_all({"alpha": q("alpha")}, tmp_path, None, runner=flaky, retries=3, retry_delay=1.0, sleep=waits.append, heartbeat_seconds=999)
    assert report.ok and len(attempts) == 3 and waits == [1.0, 2.0]


def test_a_sql_error_is_not_retried(tmp_path):
    attempts = []

    def broken(db, sql, params=None):
        attempts.append(1)
        raise sqlite3.OperationalError("no such table: nope")

    report = fetch_all({"alpha": q("alpha")}, tmp_path, None, runner=broken, retries=3, sleep=lambda s: None, heartbeat_seconds=999)
    assert len(attempts) == 1 and "no such table" in report.failures["alpha"]


def test_the_heartbeat_reports_while_a_query_is_slow(tmp_path, caplog):
    def slow(db, sql, params=None):
        time.sleep(0.35)
        return pd.DataFrame({"n": [1]})

    import logging
    logging.getLogger("sakila_reporting").propagate = True
    with caplog.at_level(logging.INFO, logger="sakila_reporting"):
        fetch_all({"alpha": q("alpha")}, tmp_path, None, runner=slow, heartbeat_seconds=0.1)
    assert any("Heartbeat" in r.message for r in caplog.records)


# ---- cache ----
def test_cache_round_trip(tmp_path):
    cache = QueryCache(tmp_path, date(2026, 1, 1))
    cache.save("alpha", pd.DataFrame({"a": [1, 2]}))
    assert cache.load()["alpha"]["a"].tolist() == [1, 2]


def test_files_from_other_days_are_deleted_not_served(tmp_path):
    QueryCache(tmp_path, date(2026, 1, 1)).save("alpha", pd.DataFrame({"a": [1]}))
    assert QueryCache(tmp_path, date(2026, 1, 2)).load() == {}
    assert list(tmp_path.glob("*.pkl")) == []


def test_leftover_temp_files_and_corrupt_files_are_ignored(tmp_path):
    (tmp_path / "2026-01-01__half.tmp").write_bytes(b"partial")
    (tmp_path / "2026-01-01__bad.pkl").write_bytes(b"not a pickle")
    cache = QueryCache(tmp_path, date(2026, 1, 1))
    cache.save("good", pd.DataFrame({"a": [1]}))
    assert list(cache.load()) == ["good"] and not list(tmp_path.glob("*.tmp"))


def test_the_write_is_atomic_so_no_temp_file_is_left_behind(tmp_path):
    cache = QueryCache(tmp_path, date(2026, 1, 1))
    cache.save("alpha", pd.DataFrame({"a": [1]}))
    assert [p.suffix for p in tmp_path.iterdir()] == [".pkl"]
    with open(tmp_path / "2026-01-01__alpha.pkl", "rb") as fh:
        assert pickle.load(fh)["a"].tolist() == [1]


def test_clear_removes_everything(tmp_path):
    cache = QueryCache(tmp_path, date(2026, 1, 1))
    cache.save("alpha", pd.DataFrame({"a": [1]}))
    cache.clear()
    assert list(tmp_path.glob("*.pkl")) == []
