"""Writes the results without Google: one CSV per query and a single self-contained HTML dashboard.

This is what lets anyone run the project with no credentials and see the output. The HTML has no external files,
scripts or fonts, so it opens straight from disk.
"""
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from .checks import CheckResult
from .layout import SHEETS, Table
from .payloads import SheetPayload

CSS = """
body{font:14px/1.45 system-ui,Segoe UI,Arial,sans-serif;margin:0;background:#f6f7f9;color:#1b2430}
main{max-width:1180px;margin:0 auto;padding:24px 20px 60px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:34px 0 10px;border-bottom:2px solid #dfe3e8;padding-bottom:6px}
h3{font-size:14px;margin:18px 0 6px;color:#3a4a5c}.sub{color:#5b6877;margin:0 0 18px}
.kpis{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px}
.kpi{background:#fff;border:1px solid #dfe3e8;border-radius:8px;padding:12px 14px}.kpi b{display:block;font-size:20px}.kpi span{color:#5b6877;font-size:12px}
table{border-collapse:collapse;background:#fff;border:1px solid #dfe3e8;font-size:13px;margin-bottom:6px}
th{background:#1f3b57;color:#fff;text-align:left;padding:5px 10px;font-weight:600}td{padding:4px 10px;border-top:1px solid #eef0f3}
td.n{text-align:right;font-variant-numeric:tabular-nums}.scroll{overflow-x:auto;max-height:420px;overflow-y:auto}
.ok{color:#117a37;font-weight:600}.bad{color:#b3261e;font-weight:600}.skip{color:#8a6d00;font-weight:600}
svg text{font:11px system-ui,Arial,sans-serif;fill:#3a4a5c}
"""

KPIS = [
    ("Total revenue", "executive_summary", 0, "$ {:,.2f}"), ("Payments", "executive_summary", 1, "{:,.0f}"),
    ("Average payment", "executive_summary", 2, "$ {:,.2f}"),
    ("Rentals", "total_rentals", 0, "{:,.0f}"), ("Customers", "total_customers", 0, "{:,.0f}"), ("Active customers", "total_customers", 1, "{:,.0f}"),
    ("Films", "total_films", 0, "{:,.0f}"), ("Currently unreturned", "unreturned", 0, "{:,.0f}"),
    ("Late returns", "late_returns", 0, "{:,.0f}"), ("Avg days overdue", "late_returns", 1, "{:,.2f}"),
]


def _fmt(value) -> str:
    if isinstance(value, bool) or value is None or (isinstance(value, float) and pd.isna(value)):
        return "" if value is None or isinstance(value, float) else str(value)
    if isinstance(value, int) or (hasattr(value, "item") and isinstance(value.item(), int)):
        return f"{int(value):,}"
    if isinstance(value, float) or (hasattr(value, "item") and isinstance(value.item(), float)):
        return f"{float(value):,.2f}"
    return html.escape(str(value))


def _table(df: pd.DataFrame) -> str:
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in df.columns)
    percent = {c for c in df.columns if "(%)" in str(c)}  # these columns hold fractions (0.5567), like the sheet's percent format
    rows = []
    for _, row in df.iterrows():
        cells = []
        for column, v in row.items():
            numeric = isinstance(v, (int, float)) and not isinstance(v, bool) or hasattr(v, "item") and not isinstance(v, str)
            text = f"{float(v) * 100:.2f}%" if column in percent and numeric else _fmt(v)
            cells.append(f'<td class="{"n" if numeric else ""}">{text}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f'<div class="scroll"><table><tr>{head}</tr>{"".join(rows)}</table></div>'


def _bar_chart(df: pd.DataFrame) -> str:
    """Revenue per month as inline SVG bars, oldest month on the left."""
    data = df.sort_values("Month")[["Month", "Revenue"]]
    top = float(data["Revenue"].max()) or 1.0
    w, h, pad, bar = 560, 190, 28, 60
    gap = (w - 2 * pad - bar * len(data)) / max(len(data) - 1, 1)
    parts = [f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="Revenue per month">']
    for i, (month, revenue) in enumerate(zip(data["Month"], data["Revenue"], strict=True)):
        x, bh = pad + i * (bar + gap), (h - 50) * float(revenue) / top
        parts.append(f'<rect x="{x:.1f}" y="{h - 30 - bh:.1f}" width="{bar}" height="{bh:.1f}" fill="#4a7fb5"/>')
        parts.append(f'<text x="{x + bar / 2:.1f}" y="{h - 14}" text-anchor="middle">{html.escape(str(month))}</text>')
        parts.append(f'<text x="{x + bar / 2:.1f}" y="{h - 34 - bh:.1f}" text-anchor="middle">{float(revenue):,.0f}</text>')
    return "".join(parts) + "</svg>"


def write_csvs(results: dict[str, pd.DataFrame], directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, df in sorted(results.items()):
        path = directory / f"{name}.csv"
        df.to_csv(path, index=False)
        written.append(path)
    return written


def render_dashboard(results: dict[str, pd.DataFrame], checks: list[CheckResult], run_started: datetime, failures: dict[str, str]) -> str:
    passed = sum(1 for c in checks if c.passed and not c.skipped)
    kpis = "".join(
        f'<div class="kpi"><b>{fmt.format(float(results[q].iloc[0, col]))}</b><span>{html.escape(label)}</span></div>'
        for label, q, col, fmt in KPIS if q in results and not results[q].empty)
    check_rows = "".join(
        f'<tr><td class="{"skip" if c.skipped else "ok" if c.passed else "bad"}">{"SKIPPED" if c.skipped else "PASS" if c.passed else "FAIL"}</td>'
        f"<td>{html.escape(c.name)}</td><td>{html.escape(c.detail)}</td></tr>" for c in checks)
    sections = []
    for sheet, blocks in SHEETS.items():
        body = []
        for block in blocks:
            if isinstance(block, Table) and block.query in results and not results[block.query].empty:
                body.append(f"<h3>{html.escape(block.title or block.query.replace('_', ' ').title())}</h3>{_table(results[block.query])}")
        if sheet == "Overview" and "monthly_revenue" in results and not results["monthly_revenue"].empty:
            body.insert(0, f"<h3>Monthly revenue</h3>{_bar_chart(results['monthly_revenue'])}{_table(results['monthly_revenue'])}")
        sections.append(f"<h2>{html.escape(sheet)}</h2>{''.join(body)}")
    problems = "".join(f"<li>{html.escape(n)}: {html.escape(m)}</li>" for n, m in failures.items())
    problem_html = f'<h2 class="bad">Failed queries</h2><ul>{problems}</ul>' if problems else ""
    return (f"<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>Sakila rental analytics</title><style>{CSS}</style><main>"
            f"<h1>Sakila rental analytics</h1><p class='sub'>Generated {run_started:%Y-%m-%d %H:%M:%S} from the bundled Sakila database &middot; "
            f"{passed} of {len([c for c in checks if not c.skipped])} data checks passed</p>"
            f"<div class='kpis'>{kpis}</div>{problem_html}"
            f"<h2>Data checks</h2><table><tr><th>Result</th><th>Check</th><th>Detail</th></tr>{check_rows}</table>"
            f"{''.join(sections)}</main></html>")


def write_dashboard(path: Path, results, checks, run_started, failures) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_dashboard(results, checks, run_started, failures), encoding="utf-8")
    return path


def write_payloads(path: Path, payloads: dict[str, SheetPayload]) -> Path:
    """The exact cell updates a publish would send, for inspection (used by --dry-run)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {s: {"updates": p.updates, "clear_ranges": p.clear_ranges, "skipped": p.skipped} for s, p in payloads.items()}
    path.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    return path
