#!/usr/bin/env python3
"""
feature_cache.py
----------------
ML feature-matrix cache (SAFe F9.2): ml_viability recomputed the full feature
matrix for every ticker on every run. This memoises the computed features to
parquet, keyed by a content hash of the input OHLC — so a re-run with unchanged
data reloads instantly and only genuinely new/changed series are recomputed.

Pure, dependency-light (pandas + hashlib) and unit-testable; ml_viability calls
`cached_features(...)` in place of a raw compute.
"""

from __future__ import annotations

import hashlib
import os

import pandas as pd

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feature_cache")


def frame_fingerprint(df: pd.DataFrame, cols=("Close", "High", "Low", "Volume")) -> str:
    """Stable short hash of the relevant columns + the last date — changes iff the
    input data that features depend on changes."""
    use = [c for c in cols if c in df.columns]
    h = hashlib.sha256()
    h.update(str(len(df)).encode())
    if len(df):
        h.update(str(df.index[-1]).encode())
        vals = df[use].to_numpy()
        h.update(pd.util.hash_pandas_object(pd.DataFrame(vals), index=False).values.tobytes())
    return h.hexdigest()[:16]


def cache_key(ticker: str, df: pd.DataFrame) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in str(ticker))
    return f"{safe}__{frame_fingerprint(df)}"


def _path(key: str) -> str:
    return os.path.join(CACHE_DIR, f"{key}.parquet")


def load(key: str):
    p = _path(key)
    return pd.read_parquet(p) if os.path.exists(p) else None


def save(key: str, features: pd.DataFrame) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    p = _path(key)
    features.to_parquet(p, compression="snappy")
    return p


def cached_features(ticker: str, df: pd.DataFrame, compute_fn) -> pd.DataFrame:
    """Return cached features for (ticker, df) or compute+store them. `compute_fn`
    is called as compute_fn(df) and must return a DataFrame."""
    key = cache_key(ticker, df)
    hit = load(key)
    if hit is not None:
        return hit
    feats = compute_fn(df)
    save(key, feats)
    return feats
