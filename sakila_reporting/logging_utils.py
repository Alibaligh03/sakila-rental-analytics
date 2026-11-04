"""Logging that survives redirected output.

On Windows, Python falls back to the system code page when stdout is redirected (a log file, a scheduled task),
and any non-ASCII character in a message then raises UnicodeEncodeError before any work happens. Forcing UTF-8 on
the standard streams, with errors="replace" as a last resort, makes a scheduled run behave like an interactive one.
"""
from __future__ import annotations

import logging
import sys

LOG = logging.getLogger("sakila_reporting")


def force_utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # stream can legitimately be missing (pythonw) or not reconfigurable
            pass


def configure_logging(verbose: bool = False) -> logging.Logger:
    force_utf8_streams()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    LOG.handlers[:] = [handler]
    LOG.setLevel(logging.DEBUG if verbose else logging.INFO)
    LOG.propagate = False
    return LOG
