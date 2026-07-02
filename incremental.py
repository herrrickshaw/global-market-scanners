#!/usr/bin/env python3
"""
incremental.py
--------------
Partition-incremental result refresh (SAFe F9.1): daily runs were recomputing
full history every time. These pure pandas helpers make refresh cheap — diff a
new partition against the stored base, and merge only the changed keys/dates
instead of rebuilding everything. Kept dependency-free (no duckdb) so they're
unit-testable and importable anywhere; warehouse.py wires them to the DuckDB
tables and parquet partitions.
"""

from __future__ import annotations

import pandas as pd


def partition_diff(prev: pd.DataFrame, curr: pd.DataFrame, key) -> dict:
    """Rows added / removed / changed between two partitions, by `key` column(s).
    A row is 'changed' if the key exists in both but any non-key value differs."""
    keys = [key] if isinstance(key, str) else list(key)
    p = prev.set_index(keys) if len(prev) else prev
    c = curr.set_index(keys) if len(curr) else curr
    pset, cset = set(p.index) if len(p) else set(), set(c.index) if len(c) else set()
    added = sorted(cset - pset, key=str)
    removed = sorted(pset - cset, key=str)
    common = cset & pset
    changed = []
    if common:
        cols = [col for col in curr.columns if col not in keys]
        for k in common:
            pv, cv = p.loc[k, cols], c.loc[k, cols]
            if not pv.equals(cv):
                changed.append(k)
    return {"added": added, "removed": removed, "changed": sorted(changed, key=str),
            "n_added": len(added), "n_removed": len(removed), "n_changed": len(changed)}


def incremental_merge(base: pd.DataFrame, new: pd.DataFrame, key,
                      drop_removed: bool = False) -> pd.DataFrame:
    """Return base with `new` upserted by key: changed/added keys take new values;
    keys only in base are kept unless drop_removed. Order: base order, appended new."""
    keys = [key] if isinstance(key, str) else list(key)
    if not len(base):
        return new.copy()
    if not len(new):
        return base.copy()
    b = base.set_index(keys)
    n = new.set_index(keys)
    merged = b.copy()
    merged.loc[n.index.intersection(b.index)] = n.loc[n.index.intersection(b.index)]
    only_new = n.loc[n.index.difference(b.index)]
    merged = pd.concat([merged, only_new])
    if drop_removed:
        merged = merged.loc[merged.index.intersection(n.index)]
    return merged.reset_index()


def append_new_dates(base: pd.DataFrame, new: pd.DataFrame, date_col: str = "Date",
                     key_col: str | None = None) -> pd.DataFrame:
    """Time-series delta: append only rows in `new` whose date is newer than the
    latest date already in `base` (per key if key_col given) — the OHLC pattern."""
    if not len(base):
        return new.copy()
    if key_col:
        cutoffs = base.groupby(key_col)[date_col].max().to_dict()
        mask = new.apply(
            lambda r: pd.Timestamp(r[date_col]) > pd.Timestamp(
                cutoffs.get(r[key_col], pd.Timestamp.min)), axis=1)
    else:
        cutoff = pd.Timestamp(base[date_col].max())
        mask = pd.to_datetime(new[date_col]) > cutoff
    return pd.concat([base, new[mask]], ignore_index=True)
