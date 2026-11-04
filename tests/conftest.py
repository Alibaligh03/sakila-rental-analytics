import re

import pytest

from sakila_reporting.config import load_settings
from sakila_reporting.fetch import fetch_all
from sakila_reporting.registry import load_queries
from sakila_reporting.windows import window_params

BOUND = re.compile(r":(as_of|lookback_days|m5_start|m2_start|win_end|day_cutoff|mtd_start|mtd_end|splm_start|splm_end)")


@pytest.fixture(scope="session")
def settings():
    return load_settings(env={})  # ignores the machine's environment: tests always use the bundled database


@pytest.fixture(scope="session")
def params(settings):
    """The date windows every parameterised query is bound to: the default as-of date, daily mode, an 8-day lookback."""
    return window_params(settings.as_of, "daily", 8)


@pytest.fixture(scope="session")
def all_queries(settings):
    """Every query that runs by default: the original analytic ones plus the daily / month-to-date / segmentation families."""
    return load_queries(settings.sql_dir)


@pytest.fixture(scope="session")
def queries(all_queries):
    """Only the original analytic queries (the ones without date windows)."""
    return {name: q for name, q in all_queries.items() if not BOUND.search(q.sql)}


@pytest.fixture(scope="session")
def report(settings, all_queries, params):
    return fetch_all(all_queries, settings.db_path, None, heartbeat_seconds=999, params=params)


@pytest.fixture
def results(report):
    """A fresh copy of the real results for tests that tamper with them."""
    return {name: df.copy() for name, df in report.results.items()}
