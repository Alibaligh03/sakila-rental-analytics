"""The command line end to end, the offline output, and the configuration rules."""
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from sakila_reporting import cli, pipeline
from sakila_reporting.checks import CheckResult
from sakila_reporting.config import ConfigError, load_dotenv, load_settings
from sakila_reporting.db import run_sql
from sakila_reporting.offline import render_dashboard, write_csvs
from sakila_reporting.workbook import LocalWorkbook, LocalWorksheet

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for key in ("GOOGLE_CREDENTIALS_PATH", "GOOGLE_SHEET_ID", "SAKILA_DB_PATH", "MAX_WORKERS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(ROOT)


def run(tmp_path, *args):
    return cli.main(["--output-dir", str(tmp_path / "out"), "--no-cache", *args])


def test_default_run_writes_csvs_and_the_dashboard_without_any_credentials(tmp_path):
    assert run(tmp_path) == 0
    day = next(p for p in (tmp_path / "out").iterdir() if p.name != "workbook")
    assert len(list((day / "csv").glob("*.csv"))) == 56  # the 27 analytic queries + the 29 windowed ones (split variant)
    assert "21 of 21 data checks passed" in (day / "dashboard.html").read_text(encoding="utf-8")
    assert (tmp_path / "out" / "workbook" / "workbook.json").exists()


def test_dry_run_writes_the_exact_updates_and_never_touches_google(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "open_spreadsheet", lambda s: pytest.fail("a dry run must not open the spreadsheet"))
    assert run(tmp_path, "--dry-run") == 0
    day = next(p for p in (tmp_path / "out").iterdir() if p.name != "workbook")
    payloads = json.loads((day / "sheet_payloads.json").read_text(encoding="utf-8"))
    assert sum(len(p["updates"]) for p in payloads.values()) == 51 + 19  # 51 data updates + the Executive Summary's 19 fixed labels


def test_publish_without_credentials_is_a_configuration_error(tmp_path):
    assert run(tmp_path, "--publish") == 2


def test_an_unknown_query_name_is_a_usage_error(tmp_path):
    assert run(tmp_path, "--only", "no_such_query") == 2


@pytest.fixture
def google_env(tmp_path, monkeypatch):
    key = tmp_path / "key.json"
    key.write_text("{}")
    monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", str(key))
    monkeypatch.setenv("GOOGLE_SHEET_ID", "sheet-id-for-tests")


@pytest.fixture
def fake_google(tmp_path, monkeypatch, google_env):
    """A workbook standing in for the Google spreadsheet, so --publish runs end to end without a network."""
    book = LocalWorkbook(tmp_path / "google.json")
    monkeypatch.setattr(pipeline, "open_spreadsheet", lambda settings: book)
    return book


def test_publish_writes_every_tab(tmp_path, fake_google):
    assert run(tmp_path, "--publish", "--create-tabs") == 0
    titles = {w.title for w in fake_google.worksheets()}
    assert titles == {"Overview", "Revenue & Finance", "Film Catalogue", "Customers & Geography", "Daily Digest"}
    assert fake_google.worksheet("Daily Digest").acell("D3", "FORMATTED_VALUE").value == "2005-08-23"  # yesterday, relative to the as-of date


def test_strict_mode_reads_the_numbers_back(tmp_path, fake_google, monkeypatch):
    reads = []
    original = LocalWorksheet.batch_get

    def spy(self, ranges, value_render_option="FORMATTED_VALUE"):
        reads.append(self.title)
        return original(self, ranges, value_render_option)

    monkeypatch.setattr(LocalWorksheet, "batch_get", spy)
    assert run(tmp_path, "--publish", "--create-tabs", "--strict") == 0
    assert {"Overview", "Revenue & Finance", "Film Catalogue", "Customers & Geography"} <= set(reads)


def test_parity_mode_writes_even_when_a_check_fails(tmp_path, fake_google, monkeypatch):
    monkeypatch.setattr(pipeline, "run_checks", lambda *a: [CheckResult("forced failure", False, "demo")])
    assert run(tmp_path, "--publish", "--create-tabs") == 1  # the run reports the failure ...
    assert fake_google.worksheet("Daily Digest").acell("D3").value == "2005-08-23"  # ... but, like the production script, has written the sheet


def test_a_failed_check_blocks_publishing_in_strict_mode_unless_forced(tmp_path, fake_google, monkeypatch):
    monkeypatch.setattr(pipeline, "run_checks", lambda *a: [CheckResult("forced failure", False, "demo")])
    assert run(tmp_path, "--publish", "--create-tabs", "--strict") == 1
    assert fake_google.worksheet("Daily Digest").acell("D3").value in ("", None)
    assert run(tmp_path, "--publish", "--create-tabs", "--strict", "--force") == 1  # forced through, but the run still reports the failed check
    assert fake_google.worksheet("Daily Digest").acell("D3").value == "2005-08-23"


def test_a_readback_mismatch_fails_a_strict_run(tmp_path, fake_google, monkeypatch):
    original = LocalWorksheet.batch_get

    def wrong(self, ranges, value_render_option="FORMATTED_VALUE"):
        out = original(self, ranges, value_render_option)
        if value_render_option == "UNFORMATTED_VALUE" and self.title == "Revenue & Finance":
            for block in out:
                if block and isinstance(block[-1][-1], (int, float)):
                    block[-1][-1] += 1
        return out

    monkeypatch.setattr(LocalWorksheet, "batch_get", wrong)
    assert run(tmp_path, "--publish", "--create-tabs", "--strict") == 1


def test_the_cache_is_kept_after_a_failed_query_and_cleared_after_a_good_run(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    real_fetch = pipeline.fetch_all

    def flaky(db, sql, params=None):
        if sql.lstrip().startswith("-- rating_split"):
            raise sqlite3.OperationalError("boom")
        return run_sql(db, sql, params)

    monkeypatch.setattr(pipeline, "fetch_all", lambda *a, **kw: real_fetch(*a, runner=flaky, retries=0, **kw))
    assert cli.main(["--output-dir", str(tmp_path / "o1"), "--workbook", str(tmp_path / "w1.json")]) == 1
    assert len(list((tmp_path / "cache").glob("*.pkl"))) >= 55
    monkeypatch.undo()
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    assert cli.main(["--output-dir", str(tmp_path / "o2"), "--workbook", str(tmp_path / "w2.json")]) == 0
    assert list((tmp_path / "cache").glob("*.pkl")) == []


def test_redirected_output_does_not_crash_on_windows_codepages(tmp_path):
    """The original script died on its first emoji when stdout was redirected. Run the real thing with piped output."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONIOENCODING"}
    env["OUTPUT_DIR"] = str(tmp_path)
    proc = subprocess.run([sys.executable, "-m", "sakila_reporting", "--no-cache"], cwd=ROOT, env=env, capture_output=True)
    assert proc.returncode == 0, proc.stdout[-500:] + proc.stderr[-500:]


# ---- offline ----
def test_dashboard_escapes_html_in_messages():
    html = render_dashboard({}, [CheckResult("<b>x</b>", False, "<script>alert(1)</script>")], datetime(2026, 1, 1), {"q": "<img src=x>"})
    assert "<script>" not in html and "&lt;script&gt;" in html and "&lt;img" in html and "FAIL" in html


def test_dashboard_shows_the_headline_numbers(results):
    html = render_dashboard(results, [CheckResult("c", True, "ok")], datetime(2026, 1, 1), {})
    assert "$ 67,416.51" in html and "16,044" in html and "Financially Active Customers" in html and "<svg" in html


def test_csv_files_round_trip(tmp_path):
    frames = {"a": pd.DataFrame({"x": [1, 2]}), "b": pd.DataFrame({"y": ["p"]})}
    paths = write_csvs(frames, tmp_path / "csv")
    assert sorted(p.name for p in paths) == ["a.csv", "b.csv"]
    assert pd.read_csv(tmp_path / "csv" / "a.csv")["x"].tolist() == [1, 2]


# ---- config ----
def test_dotenv_parsing(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\nGOOGLE_SHEET_ID='abc'\nMAX_WORKERS = 3\n\nBROKEN LINE\n", encoding="utf-8")
    assert load_dotenv(env) == {"GOOGLE_SHEET_ID": "abc", "MAX_WORKERS": "3"}
    assert load_dotenv(tmp_path / "missing") == {}


def test_real_environment_overrides_the_env_file(tmp_path):
    (tmp_path / ".env").write_text("MAX_WORKERS=2\n")
    (tmp_path / "sqlite-sakila.db").write_bytes(b"")
    assert load_settings(tmp_path, env={"MAX_WORKERS": "5"}).max_workers == 5
    assert load_settings(tmp_path, env={}).max_workers == 2


@pytest.mark.parametrize("value", ["zero", "0", "-3"])
def test_invalid_numbers_are_rejected(tmp_path, value):
    (tmp_path / "sqlite-sakila.db").write_bytes(b"")
    with pytest.raises(ConfigError):
        load_settings(tmp_path, env={"MAX_WORKERS": value})


def test_missing_database_is_a_clear_error(tmp_path):
    with pytest.raises(ConfigError, match="Database not found"):
        load_settings(tmp_path, env={})


def test_publishing_needs_both_credentials_and_a_sheet_id(tmp_path):
    (tmp_path / "sqlite-sakila.db").write_bytes(b"")
    with pytest.raises(ConfigError, match="GOOGLE_CREDENTIALS_PATH"):
        load_settings(tmp_path, env={}).require_publish_settings()
    with pytest.raises(ConfigError, match="not found"):
        load_settings(tmp_path, env={"GOOGLE_CREDENTIALS_PATH": "nope.json"}).require_publish_settings()
    key = tmp_path / "k.json"
    key.write_text("{}")
    with pytest.raises(ConfigError, match="GOOGLE_SHEET_ID"):
        load_settings(tmp_path, env={"GOOGLE_CREDENTIALS_PATH": str(key)}).require_publish_settings()


def test_percent_columns_show_as_percentages_in_the_dashboard(results):
    html = render_dashboard(results, [CheckResult("c", True, "ok")], datetime(2026, 1, 1), {})
    assert "55.67%" in html  # Sports late-return rate: stored as the fraction 0.5567, shown like the sheet's percent format
