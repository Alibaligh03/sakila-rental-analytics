"""Settings come from the environment (optionally a local .env file), never from the source code.

Nothing secret is stored in the repository: the Google credentials path and the sheet id are read from
GOOGLE_CREDENTIALS_PATH and GOOGLE_SHEET_ID, and are only needed when publishing to Google Sheets.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping

from .retry import MODES as RETRY_MODES

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Sakila is a historical dataset: its rentals fall in eight-day bursts between 2005-05-24 and 2005-08-23 (plus a few days in
# 2006-02). The as-of date plays the role of "today"; this default sits just after the last continuous burst, where both the
# daily backfill and the like-for-like month windows have data.
DEFAULT_AS_OF = date(2005, 8, 24)
RUN_MODES = ("daily", "first-of-month")
QUERY_MODES = ("split", "cte")


class ConfigError(Exception):
    """A setting is missing or invalid. The command line turns this into exit code 2."""


def load_dotenv(path: Path) -> dict[str, str]:
    """Minimal .env reader: KEY=VALUE lines, '#' comments, optional quotes. Returns {} if the file is absent.

    Read as utf-8-sig: a file saved with a byte-order mark (Notepad's "UTF-8 with BOM", PowerShell's Out-File) would otherwise
    leave the mark glued to the FIRST key, so that one variable silently reads as unset.
    """
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


@dataclass(frozen=True)
class Settings:
    db_path: Path
    sql_dir: Path
    cache_dir: Path
    output_dir: Path
    workbook_path: Path
    credentials_path: Path | None = None
    sheet_id: str | None = None
    as_of: date = DEFAULT_AS_OF
    mode: str = "daily"
    query_mode: str = "split"
    max_workers: int = 5
    heartbeat_seconds: float = 120.0
    retry_mode: str = "all"
    sheets_retries: int = 10
    sheets_retry_delay: float = 10.0

    def require_publish_settings(self) -> None:
        """Publishing needs credentials and a sheet id; fail early with a message that says what to set."""
        if not self.credentials_path:
            raise ConfigError("GOOGLE_CREDENTIALS_PATH is not set (path to a service-account JSON key). See .env.example.")
        if not self.credentials_path.exists():
            raise ConfigError(f"Google credentials file not found: {self.credentials_path}")
        if not self.sheet_id:
            raise ConfigError("GOOGLE_SHEET_ID is not set (the id of the Google Sheet to publish to). See .env.example.")


def _path(value: str | None, root: Path, default: str) -> Path:
    p = Path(value or default)
    return p if p.is_absolute() else root / p


def _number(env: Mapping[str, str], key: str, default: float, cast) -> float:
    raw = env.get(key)
    if raw in (None, ""):
        return default
    try:
        value = cast(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number, got {raw!r}") from exc
    if value <= 0:
        raise ConfigError(f"{key} must be greater than 0, got {raw!r}")
    return value


def _choice(env: Mapping[str, str], key: str, default: str, choices: tuple[str, ...]) -> str:
    value = (env.get(key) or default).strip().lower()
    if value not in choices:
        raise ConfigError(f"{key} must be one of {', '.join(choices)}; got {value!r}")
    return value


def parse_as_of(text: str | None) -> date:
    if not text:
        return DEFAULT_AS_OF
    try:
        return date.fromisoformat(text.strip())
    except ValueError as exc:
        raise ConfigError(f"AS_OF_DATE must look like 2005-08-24, got {text!r}") from exc


def load_settings(root: Path = PROJECT_ROOT, env: Mapping[str, str] | None = None) -> Settings:
    """Real environment variables win over the .env file."""
    merged = {**load_dotenv(root / ".env"), **(os.environ if env is None else env)}
    creds = merged.get("GOOGLE_CREDENTIALS_PATH")
    db_path = _path(merged.get("SAKILA_DB_PATH"), root, "sqlite-sakila.db")
    if not db_path.exists():
        raise ConfigError(f"Database not found: {db_path} (set SAKILA_DB_PATH)")
    output_dir = _path(merged.get("OUTPUT_DIR"), root, "output")
    return Settings(
        db_path=db_path,
        sql_dir=_path(merged.get("SQL_DIR"), root, "sql"),
        cache_dir=_path(merged.get("CACHE_DIR"), root, ".query_cache"),
        output_dir=output_dir,
        workbook_path=_path(merged.get("WORKBOOK_PATH"), root, str(output_dir / "workbook" / "workbook.json")),
        credentials_path=_path(creds, root, creds) if creds else None,
        sheet_id=merged.get("GOOGLE_SHEET_ID") or None,
        as_of=parse_as_of(merged.get("AS_OF_DATE")),
        mode=_choice(merged, "RUN_MODE", "daily", RUN_MODES),
        query_mode=_choice(merged, "QUERY_MODE", "split", QUERY_MODES),
        max_workers=int(_number(merged, "MAX_WORKERS", 5, int)),
        heartbeat_seconds=float(_number(merged, "HEARTBEAT_SECONDS", 120.0, float)),
        retry_mode=_choice(merged, "SHEETS_RETRY_MODE", "all", RETRY_MODES),
        sheets_retries=int(_number(merged, "SHEETS_RETRIES", 10, int)),
        sheets_retry_delay=float(_number(merged, "SHEETS_RETRY_DELAY", 10.0, float)),
    )
