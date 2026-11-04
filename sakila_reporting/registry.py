"""Loads the SQL files in sql/ into Query objects.

Each file is one query; its name is the file stem and its first comment line is a one-line description.
Optional header line:
    -- variant: cte | split    one of two interchangeable ways to get the same result; QUERY_MODE picks which set is loaded
The registry also reads each query's ORDER BY so the pipeline can verify the result really is in that order.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Query:
    name: str
    sql: str
    description: str
    sort_by: str | None = None  # the first ORDER BY item, when it is one of the query's own column aliases
    descending: bool = False
    variant: str | None = None  # None = always loaded; "cte" / "split" = loaded only in that QUERY_MODE


_ALIAS = re.compile(r'\bAS\s+"([^"]+)"', re.IGNORECASE)
_ORDER_BY = re.compile(r"\bORDER\s+BY\b(.*?)(?:\bLIMIT\b|$)", re.IGNORECASE | re.DOTALL)
_QUOTED = re.compile(r'"([^"]+)"')
_HEADER = re.compile(r"^--\s*(variant)\s*:\s*(\w+)\s*$", re.IGNORECASE | re.MULTILINE)


def column_aliases(sql: str) -> set[str]:
    return set(_ALIAS.findall(sql))


def order_by_identifiers(sql: str) -> list[str]:
    """Every double-quoted identifier used in the (last) ORDER BY clause."""
    clauses = _ORDER_BY.findall(sql)
    return _QUOTED.findall(clauses[-1]) if clauses else []


def broken_order_by(sql: str) -> list[str]:
    """Quoted ORDER BY names that are not a column alias of the query.

    SQLite does not raise on these: an unknown double-quoted name silently becomes a constant string, so the
    ORDER BY does nothing and the rows come back in arbitrary order. This is the bug the lint test guards against.
    """
    aliases = column_aliases(sql)
    return [name for name in order_by_identifiers(sql) if name not in aliases]


def _first_sort(sql: str) -> tuple[str | None, bool]:
    clauses = _ORDER_BY.findall(sql)
    if not clauses:
        return None, False
    first = clauses[-1].split(",")[0].strip()
    quoted = _QUOTED.match(first)
    if not quoted or quoted.group(1) not in column_aliases(sql):
        return None, False
    return quoted.group(1), first.upper().endswith("DESC")


def parse_query(name: str, text: str) -> Query:
    lines = text.strip().splitlines()
    description = lines[0].lstrip("-").strip() if lines and lines[0].startswith("--") else name
    description = description.split(":", 1)[1].strip() if ":" in description else description
    header = {key.lower(): value.lower() for key, value in _HEADER.findall(text)}
    sort_by, descending = _first_sort(text)
    return Query(name=name, sql=text.strip(), description=description, sort_by=sort_by, descending=descending, variant=header.get("variant"))


def load_queries(sql_dir: Path, *, variant: str = "split") -> dict[str, Query]:
    files = sorted(Path(sql_dir).glob("*.sql"))
    if not files:
        raise FileNotFoundError(f"No .sql files found in {sql_dir}")
    queries = {f.stem: parse_query(f.stem, f.read_text(encoding="utf-8")) for f in files}
    return {name: q for name, q in queries.items()
            if q.variant is None or q.variant == variant}
