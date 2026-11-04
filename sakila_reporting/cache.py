"""Per-query result cache, so a run that dies half way does not repeat the work that already finished.

Design rules (each one exists because the alternative loses data or serves stale numbers):

* One small file per query, never one shared file. With a shared file, whichever write was in flight when the
  process died decides how much finished work survives. With separate files, a query's result is safe the moment
  its own file exists.
* Written to a temporary name and renamed into place (os.replace is atomic), so a file is fully there or not there.
* Only successes are cached. A failed query is retried on the next run instead of an empty placeholder being
  mistaken for a result.
* The run date is part of the file name. Files from another day are ignored and deleted, so yesterday's numbers
  can never reappear in today's report.
* The cache is cleared only after the whole run succeeded.

The files are pickles written and read by this program in a local, git-ignored folder; do not point the cache
at a folder that other people or processes can write to.
"""
from __future__ import annotations

import os
import pickle
from datetime import date
from pathlib import Path

import pandas as pd

from .logging_utils import LOG


class QueryCache:
    def __init__(self, cache_dir: Path, tag: date | str):
        """`tag` is the run date, optionally followed by a fingerprint of the windows the results were computed for, so results
        for one as-of date are never served for another."""
        self.dir = Path(cache_dir)
        self.tag = tag.isoformat() if hasattr(tag, "isoformat") else str(tag)

    def _path(self, name: str) -> Path:
        return self.dir / f"{self.tag}__{name}.pkl"

    def load(self) -> dict[str, pd.DataFrame]:
        """Today's cached results. Stale files from other days and leftover temp files are removed."""
        if not self.dir.exists():
            return {}
        results: dict[str, pd.DataFrame] = {}
        for tmp in self.dir.glob("*.tmp"):
            tmp.unlink(missing_ok=True)
        for f in self.dir.glob("*.pkl"):
            date_part, sep, name = f.stem.partition("__")
            if not sep:
                continue
            if date_part != self.tag:
                f.unlink(missing_ok=True)
                continue
            try:
                with open(f, "rb") as fh:
                    results[name] = pickle.load(fh)
            except Exception as exc:  # corrupt or unreadable: just fetch it again
                LOG.warning("Could not read cached '%s' (%s); it will be fetched again", name, exc.__class__.__name__)
        return results

    def save(self, name: str, df: pd.DataFrame) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            final = self._path(name)
            tmp = final.with_suffix(".tmp")
            with open(tmp, "wb") as fh:
                pickle.dump(df, fh)
            os.replace(tmp, final)
        except Exception as exc:  # a cache problem must never fail the report
            LOG.warning("Could not cache '%s' (%s); continuing without it", name, exc.__class__.__name__)

    def clear(self) -> None:
        if self.dir.exists():
            for f in self.dir.glob("*.pkl"):
                f.unlink(missing_ok=True)
