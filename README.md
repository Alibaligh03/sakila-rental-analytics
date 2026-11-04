# Sakila Video Rental: Automated Analytics Pipeline

[![CI](https://github.com/Alibaligh03/sakila-rental-analytics/actions/workflows/ci.yml/badge.svg)](https://github.com/Alibaligh03/sakila-rental-analytics/actions/workflows/ci.yml)

A reporting pipeline built the way a scheduled production job should be built. Every morning it runs a set of
SQL queries in parallel, **checks that the numbers reconcile**, and keeps a spreadsheet up to date: one new column per
day on the daily tabs (and if the job missed a few days, it works out which ones and fills exactly those), month-to-date
tabs that compare like for like with earlier months, per-store tabs, segmentation tabs and the original analytics
dashboard. It survives failures without losing finished work, and it runs with **no credentials at all** against a local
workbook, or publishes to a live Google Sheet.

> [View the live dashboard on Google Sheets](https://docs.google.com/spreadsheets/d/18LUaEHCHd-Qk94PBTIhGY_WW1EEm_OJbuLIBshSj6_g/edit?gid=0#gid=0)

```
 read last-filled date ─► size the lookback ─► run 56 queries in parallel ─► merge / check / clean ─► write tabs
 (its stamp cell)         (gap + 1 day floor)  per-query cache, failure     split variants merged     daily backfill (insert a
                                               isolation, heartbeat         21 checks                  column per missing day),
                                                                                                       month-to-date, per store,
                                                                                                       segmentation, analytics
```

---

## Quick start (no credentials needed)

```bash
git clone https://github.com/Alibaligh03/sakila-rental-analytics.git
cd sakila-rental-analytics
pip install -r requirements.txt
python -m sakila_reporting
```

Open `output/<today>/dashboard.html` in a browser, and `output/<today>/workbook.html` to see the tabs the run wrote. The
tabs live in a local workbook (`output/workbook/workbook.json`) that **keeps its state between runs**, which is what lets the
daily backfill be demonstrated without Google:

```bash
python -m sakila_reporting --as-of 2005-08-20     # first run: writes 2005-08-19, the day before the as-of date
python -m sakila_reporting --as-of 2005-08-24     # the job "missed" four days: it finds them and fills exactly those
```

```
Daily Digest's D28 ('') isn't a recognized date; writing only 2005-08-19
...
'Daily' updated for 2005-08-20
'Daily' updated for 2005-08-21
'Daily' updated for 2005-08-22
'Daily' updated for 2005-08-23
check PASS    daily stores add up to the daily total                  4 periods: the stores add up to the total
check PASS    daily rows fall inside the lookback window              4 daily frames: every day is inside the 4-day lookback window
Finished in 1.2s: success
```

### Why an "as-of date"?

Sakila is a historical dataset: its rentals fall in eight-day bursts between 2005-05-24 and 2005-08-23, so "today" has no
data. The pipeline takes the day it pretends to run on (`--as-of`, default `2005-08-24`) and works out everything relative to
it, exactly as a production job works relative to the current date. Because the data is sparse, a few blocks are legitimately
empty in some windows (for example "first-time customers" in the default 8-day window: everyone has rented before).

## Publishing to Google Sheets

1. Create a Google Cloud **service account**, download its JSON key, and share your target sheet with the
   service account's e-mail address (Editor). Use a dedicated account for this project.
2. Copy `.env.example` to `.env` and set `GOOGLE_CREDENTIALS_PATH` and `GOOGLE_SHEET_ID`. The `.env` file and the
   JSON key are git-ignored; nothing sensitive is in the source.
3. Preview exactly what would be written, with no network calls:
   ```bash
   python -m sakila_reporting --dry-run     # writes output/<date>/sheet_payloads.json
   ```
4. Publish:
   ```bash
   python -m sakila_reporting --publish
   ```

The sheet layout is described in `sakila_reporting/layout.py`: adding a table to the dashboard is one line.

## The tabs

Five tabs. Everything that tracks the business over time is on **one tab, stacked in blocks, like the pages of a printed digest**; the
original analytics dashboard keeps its four tabs.

| Tab | What it holds |
|---|---|
| **Daily Digest** | the KPI blocks below, one under the other, each with a title band |
| **Overview** | the executive summary (KPIs, monthly trend, rentals by day of week, late returns) with staff, late-return rates and inventory beside it |
| **Revenue & Finance**, **Film Catalogue**, **Customers & Geography** | the rest of the 27-query analytics dashboard |

The blocks of **Daily Digest** (columns: a margin, *Domain*, *KPI*, then the figures):

| Rows | Block | How it is written |
|---|---|---|
| 1-23 | **Daily**: one column per day, newest on the left | a column is **inserted** at D for every missing day; the date of the last filled day is the stamp in D3 |
| 26-49 | **Daily by Store**: two columns per day, the date **merged across the pair** with the store names under it; lines fall between days only, so a day's two stores read as one unit | same, matched by store name; the stamp is D28 |
| 52-78 | **Rental Insights**: this month so far, the same days last month and **% Change** in one table; the three earlier months in a second table after a gap column | fixed cells, looked up by month |
| 81-108 | **Rental Insights (STORE 1 & STORE 2)**: MTD, SPLM and % Change each merged over the two stores, whose names repeat on a pale row underneath; lines fall between the periods only | fixed cells, looked up by month and store |
| 111- | **Segments**: customers by rentals per month, top countries per month, rentals by film rating, each table under its own title band | cleared, then rewritten (their length varies, so they come last) |

Each block owns its rows, so adding a day to the daily block moves only that block's cells (an `insertRange` over its rows) and leaves
every other block where it is.

### Design

The look follows a daily KPI digest, in purple: Lato 10 with centred values, no gridlines and **nothing frozen**, a bold header band and
a title band over each block, thin vertical borders, compact figures (`2.52K`, `1.30M`), and two-decimal percentages.

- **Daily blocks:** every day is coloured **green or red** by whether it beat the previous day, and the date header is **blue on
  Saturdays and yellow on Sundays**.
- **Monthly blocks:** plain white inside; only the **% Change** column is coloured, its text green above zero and red below.
- **Stores:** a pale-yellow bold row of store names under the dates (daily) or under each merged period heading (monthly); in both, the
  vertical lines fall between days / periods and never between a day's two stores.

The colours are conditional-format rules, not painted cells, and they start one column to the left of the data so that a column
inserted at D falls inside them: the backfill inserts a column every day, and a painted colour would stay behind where a rule keeps
following the data.

```bash
python -m sakila_reporting --publish --create-tabs --format-tabs     # (re)apply the design; safe to repeat, never duplicates a rule
```

The design is built from the same layout the data is written with (`sakila_reporting/formatting.py` and `sakila_reporting/digest.py`,
pure functions that are unit tested), so it cannot drift from the data. The local workbook has no visual design.

---

## What makes it reliable

### The daily backfill

The tab itself is the only record of what has been done, so the code is defensive about everything that could leave it
wrong:

- **Gap detection.** The date of the last filled day sits in the stamp cell of each daily block. It is read *before* the queries are
  built, because it sizes their lookback window (floor 1 day, 2 if the cell is unreadable, capped at 90 so a garbage date
  cannot become a huge query). The cell may be ISO text, a `dd/mm/yyyy` a person typed, or the serial number Sheets returns.
- **One column per missing day, oldest first,** inserted at column D so the newest day is always on the left; every
  formula in the shifted columns is re-pointed (the workbook stand-in implements this, as Sheets does).
- **The insert is never retried.** It is one atomic request (move the block's cells right, copy the neighbour's formats, merge the
  date pair), but if its reply is lost the code cannot know whether it happened. After a failed insert the stamp is read again: a stamp that
  went from a date to blank proves the columns had already moved, so the blank column is removed; otherwise nothing is
  deleted, because deleting a column that holds a real day would destroy data.
- **Empty days: a real zero, or not stamped at all.** A day with no rows is written as zeros only if a *later* day in the same
  catch-up has rows (then it really had no activity; Sakila has quiet stretches between its bursts of rentals). If nothing later has
  rows either, the data may simply not be loaded yet, and stamping it would mark the day covered while leaving it blank for ever: the
  inserted columns are removed and the run stops, leaving the stamp on the last complete day. A query that *failed* never produces
  zeros: its cells stay blank for a later run.
- **Fresh dicts on every write attempt.** gspread rewrites each item's `range` in place, so re-sending the same list fails every
  retry with a malformed range. The same applies to the analytic tabs and the read-back.
- **ISO dates only.** Sent as `USER_ENTERED` to a sheet whose locale is month-first, `01/09/2005` is read as the 9th of January.

### The queries and the checks

- **Date windows are bound parameters, not string-built SQL.** `as_of`, the lookback, the five month starts, and the
  like-for-like day-of-month cut-off are passed to SQLite as named parameters.
- **Like-for-like month windows** (`daily` mode): this month's days 1..(today-1) are compared with the same days of each of the
  previous four months, so a month in progress is never compared with a whole month. `first-of-month` mode uses the five whole
  calendar months before the as-of month with no cut-off. Both are selected with `--mode`.
- **Month blocks are placed by month, never by row position.** A month with no rentals simply has no row in the result; reading by
  position would shift every later month one column to the left. Per-store columns are matched by store name for the same reason.
- **Two ways to run the per-store month queries,** `--query-mode split` (this month and last month as two queries, merged) or
  `cte` (one query over the whole window); a test proves they give identical numbers.
- **21 checks run on every execution**: 17 on the analytics queries (revenue breakdowns agree, executive revenue explained to the
  cent, customer spend, city counts, actor counts, sort order, ...) and 4 on the windowed families (per-store frames add up to
  the total, on-time + late = returned, every daily row is inside the lookback window).

### Two run modes: `parity` (default) and `--strict`

| | `parity` (default) | `--strict` |
|---|---|---|
| a failed query | its block stays empty; everything else is still written | same |
| a failed data check | reported loudly, exit code 1, **writes still happen** | **blocks every write** (`--force` overrides) |
| a Sheets error that survives the retries | the run stops, the cache is kept so the rerun resumes | that tab is reported failed, the other tabs are still written |
| written numbers | not read back | read back and compared |
| Sheets retries (set with `--retry-mode`) | any API error, fixed delay, 10 attempts | whichever mode you pick; `transient` = 429/5xx only, exponential backoff |

Parity mirrors how a long-running unattended job is usually operated: a partial update is better than none, and a person
looks at the exit code. Strict is what to choose when a wrong number is worse than a late one. The trade-off is explicit and
switchable instead of hidden.

### Failure isolation, caching, safe publishing

Queries run in a 5-worker thread pool, each with its own connection. One failing query is reported and the rest still finish.
Each successful result is cached in its own file, written atomically and tagged with the run date **and a fingerprint of the
date windows**, so a rerun with a different as-of date or lookback can never reuse stale results. The cache is cleared only after
a run in which no query failed and the writes completed. A heartbeat line shows whether a slow query is still working, and open
connections are closed at exit. Analytic tabs are written with one atomic `batch_update`; lists whose length can change have
their old range cleared first.

**Portable.** The run date is fixed once at the start, so a run that crosses midnight cannot mix dates. `.env` files saved with
a byte-order mark are read correctly (otherwise the first variable silently reads as unset). Console output is forced to UTF-8.

## Command line

| Command | Effect |
|---|---|
| `python -m sakila_reporting` | queries, checks, local workbook (`output/workbook/workbook.json`), CSVs and HTML in `output/<date>/` |
| `--as-of YYYY-MM-DD` | the day the run pretends to happen (also `AS_OF_DATE`) |
| `--mode daily` or `first-of-month` | like-for-like month-to-date windows, or whole calendar months (also `RUN_MODE`) |
| `--query-mode split` or `cte` | how the per-store month queries run (also `QUERY_MODE`) |
| `--publish` | write to Google Sheets instead of the local workbook (needs credentials; `--create-tabs` makes missing tabs, `--format-tabs` applies the design) |
| `--strict`, `--force`, `--no-verify` | strict mode, override a failed check, skip the read-back |
| `--retry-mode all` or `transient` | retry any API error, or only 429/5xx with backoff |
| `--only NAME ...` | run only some queries (file names in `sql/`) |
| `--no-cache`, `--workers N`, `--workbook PATH`, `--output-dir DIR`, `-v` | see `--help` |

Exit code `0` means success, `1` a query, a check or a write failed, `2` a configuration problem.

## Tests

```bash
pip install -r requirements-dev.txt
ruff check .
pytest
```

About 200 tests, run against the bundled database and a workbook stand-in that behaves like Google Sheets (dates become serial
numbers, formulas are evaluated and re-pointed when columns are inserted, `batch_update` rewrites its input in place) and can be
told to fail. They cover: every query and its sort order; each reconciliation check passing on real data and failing when the data
is tampered with; **an independent pandas recomputation of the daily, per-store and month-to-date families**; gap detection and
the backfill (multi-day fills, a failed insert, a partially committed insert, an empty day, a failed write, a retry that needs
fresh dicts); the retry policies; failure isolation and cache rules; parity vs strict end to end; a second run that catches up on
missed days; the exact sheet layout against a golden file; the command line; and the configuration rules. CI runs lint and tests
on Python 3.10 to 3.12, then runs the two-run backfill demo and keeps the output as a downloadable artifact.

> The Google Sheets path is exercised against the stand-in, not a live sheet. Publishing for real needs your own credentials
> (see above); try `--dry-run` or the local workbook first.

## Project structure

```
sakila-rental-analytics/
├── sakila_reporting/
│   ├── cli.py, pipeline.py  #   command line; the run, step by step
│   ├── digest.py            #   where every block of the Daily Digest tab sits (rows, labels, titles)
│   ├── gap.py, backfill.py  #   gap detection; one inserted column per missing day in a block, with its failure handling
│   ├── tabs.py, windows.py  #   monthly blocks with % Change, segment tables; the date windows
│   ├── formatting.py        #   the look: bands, number formats, widths, green/red and weekday colours
│   ├── fetch.py, cache.py   #   parallel queries, failure isolation, heartbeat; per-query atomic cache
│   ├── checks.py            #   the 21 reconciliation checks
│   ├── layout.py, payloads.py #  analytics dashboard: where each result goes, and the exact cell updates
│   ├── sheets.py, retry.py  #   Google Sheets publisher and the two retry policies
│   ├── workbook.py          #   local workbook that behaves like a spreadsheet (so everything runs without Google)
│   ├── offline.py           #   CSV files and the self-contained HTML dashboard
│   ├── registry.py          #   loads sql/*.sql (disabled / variant headers, ORDER BY)
│   └── config.py, db.py, a1.py, logging_utils.py
├── sql/                     # 61 queries: 27 analytics + the daily / month-to-date / segmentation families
├── tests/                   # tests and the golden sheet payloads
├── sqlite-sakila.db         # Sakila sample database
├── .env.example             # settings template (the real .env is git-ignored)
└── .github/workflows/ci.yml
```

## Dataset

The **Sakila** database is a sample dataset originally created by MySQL, representing a DVD rental store: films,
actors, customers, inventory, rentals, payments and stores in a normalised schema.

- 1,000 films across 16 categories
- 599 customers across multiple countries
- 16,044 rentals and 16,049 payments
- 2 store locations

## Key SQL techniques

- Multi-table `JOIN`s across 6+ tables
- Aggregations with `GROUP BY`, `HAVING`
- `CASE WHEN` bucketing and conditional counts
- Correlated subqueries for per-category averages
- Date arithmetic with `julianday()` and `strftime()`
- `NOT EXISTS` to find catalogue films with no inventory copy
- Per-customer correlated subqueries and pre-aggregated joins to avoid fan-out, with independent verification queries in the checks

## Changes in v2: issues found and fixed

Every query was audited against an independent recalculation (pandas, from the raw tables). Real problems were found
and fixed; each has a regression test and, where it can recur, a check that runs on every execution:

| Query / block | Problem | Fix |
|---|---|---|
| Customer Segments | rentals and payments were both joined onto the customer, so every payment was repeated once per rental: *Avg Total Spend* was 1,389 / 2,798 / 4,720 / 8,380 against a true 76 / 108 / 141 / 193 | per-customer correlated subqueries |
| Top 20 Cities by Customers | customers were counted once per payment: the top city showed 50 customers, the real maximum is 2 | spend is pre-aggregated per customer before joining; ties broken by revenue |
| Top 20 Customers by Spend | `ORDER BY` named a column that does not exist, so it listed customers 1 to 20, not the top spenders | sorts by `"Total Spent"` |
| Revenue by Category, Revenue by Month & Category, Staff Performance | the same non-existent `ORDER BY` name: unsorted | sort by the real column |
| Top 20 Actors | *Films in Catalogue* counted only rented films (37 shown, 39 real) | counted from `film_actor` in its own subquery |
| "Dead Inventory (Never Rented)" | the 42 films listed are not in inventory at all (only 1 of 4,581 copies was never rented), so the label was wrong | renamed *Films Never Stocked*; query uses `NOT EXISTS` |
| Executive revenue vs the breakdowns | 67,416.51 against 67,406.56 with no explanation: 5 payments have no rental | explained and verified to the cent on every run |
| Top-N lists | ties at the cutoff (16 films tie at 30 rentals) made the list depend on scan order | explicit tie-breakers |
| Rental counts after `LEFT JOIN payment` | would double count a rental that ever had two payments | `COUNT(DISTINCT rental_id)` |
| Header text, console | "Finically" typo; the original script crashed on its first line when output was redirected on Windows | fixed |

Definitions worth knowing (documented in the SQL): store revenue is attributed to the store of the staff member who
handled the rental, which differs from the store that owns the copy for about half of the rentals; a return is late
when the elapsed time, counting the time of day, exceeds the film's rental duration.

## Changes in v3: production-style daily reporting

v3 turns the analytics script into the shape a scheduled reporting job takes in practice. Nothing in v2's analytics changed;
what was added:

| Area | Change |
|---|---|
| Daily tabs | gap detection from the last-filled date; one inserted column per missing day, oldest first; partial-commit cleanup; never stamping an empty day |
| Month tabs | like-for-like month-to-date windows (and whole-month mode); per-store tabs; columns placed by month and store, not by row position |
| Segmentation | customer, country and rating split tables, each under its own title band |
| Queries | 34 new queries with bound date parameters; `split` and `cte` variants that provably agree |
| Run | local workbook that keeps its state between runs; `--as-of`, `--mode`, `--query-mode`, `--strict`; parity (default) and strict behaviours side by side |
| Retries | `all` (any API error, fixed delay) and `transient` (429/5xx, exponential backoff); the insert is never retried; fresh dicts per attempt |
| Tests | from about 100 to about 200; the new families are verified against an independent pandas recomputation |
| Layout | 14 tabs reduced to 5: one **Daily Digest** tab with the daily, per-store, monthly and segment blocks stacked, plus the analytics dashboard (Executive Summary and Operations share **Overview**) |
| Design | purple digest-style formatting, green/red against the previous day, a coloured **% Change** column, blue Saturday / yellow Sunday headers, no frozen panes; built from the layout and unit tested |

Two bugs in this repository's own publisher and backfill were found while building the workbook stand-in, and are fixed with
tests: the publisher re-sent the same update dicts that gspread rewrites in place (so a retry, or the read-back, would have seen
malformed ranges), and the per-store backfill crashed on a completely empty side query instead of leaving its cells blank.
