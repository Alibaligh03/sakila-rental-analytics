"""Retry policy for Google Sheets calls.

Two policies, because there are two defensible designs:

mode="all"        (default) retry any APIError, a fixed delay between attempts, 10 attempts. Simple and forgiving: a flaky
                  connection, a rate limit and a brief outage all just wait and try again. The cost is that a permanent
                  error (403 no access, 404 wrong sheet) also waits out every attempt before it is reported.
mode="transient"  retry only HTTP 408, 429 and 5xx, with exponential backoff; anything else is raised at once.

Only SINGLE-REQUEST calls (worksheet lookup, acell, batch_update, batch_clear, delete_columns) may be wrapped. A call
that makes several requests internally, such as insert_cols (an insertDimension followed by a values append), must NOT be
retried as a whole: a retry would repeat the part that already succeeded and could insert more than one stray column.
"""
from __future__ import annotations

import time
from typing import Callable

from .logging_utils import LOG

TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}
MODES = ("all", "transient")


def http_status(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None)


def is_api_error(exc: Exception) -> bool:
    return exc.__class__.__name__ == "APIError"


def is_transient(exc: Exception) -> bool:
    return is_api_error(exc) and http_status(exc) in TRANSIENT_STATUS


def is_retryable(exc: Exception, mode: str = "all") -> bool:
    if mode == "all":
        return is_api_error(exc)
    return is_transient(exc)


def with_retry(fn: Callable, *args, retries: int = 10, delay: float = 10.0, sleep: Callable[[float], None] = time.sleep, mode: str = "all", **kwargs):
    if mode not in MODES:
        raise ValueError(f"retry mode must be one of {MODES}, got {mode!r}")
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if attempt < retries - 1 and is_retryable(exc, mode):
                wait = delay if mode == "all" else min(delay * 2**attempt, 60.0)
                LOG.warning("API error%s, retrying in %.0f seconds... (attempt %d)", f" {http_status(exc)}" if http_status(exc) else "", wait, attempt + 1)
                sleep(wait)
                continue
            raise
