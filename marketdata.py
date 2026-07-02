#!/usr/bin/env python3
"""
marketdata.py
-------------
Shared, reusable building blocks used across the platform's ~35 analysis modules —
so the data-loading, universe-filtering, ticker-normalising and cross-sectional
statistics live in ONE place instead of being copy-pasted. Modules import from here
rather than redefining these helpers.

GLOSSARY (each block = one function; see the docstrings for detail):

  DATA ACCESS
    SEED               path to the cleaned_long parquet cache
    market_list()      list every market code with a parquet (e.g. US, JP, KR …)
    wide(market)       -> {"Close","High","Low","Volume"} wide frames (Date × Symbol)
    close_volume(mkt)  -> (close, volume) wide frames  (the common two-frame case)

  UNIVERSE
    liquid_symbols(close, vol)   the tradeable top-40% by median dollar-volume
    clean_key(ticker)            bare, upper-cased symbol for cross-source joins
    market_proxy(close, syms)    equal-weight daily return of a (liquid) universe

  CROSS-SECTIONAL STATS
    zscore(series)               standardise a cross-section to mean 0 / sd 1
    information_coefficient(s,f) correlation of a signal with forward returns (the IC)
    monotonicity(curve, col)     +1 = a quantile curve rises perfectly Q1->Qn
    trend_corr(x)                scale-free trend of a series = corr with time ∈[-1,1]
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

SEED = os.path.expanduser("~/Downloads/code/python_files/cache_seed")

# universe-filter defaults (a stock is "liquid" if in the top (1−LIQ_QUANTILE) by
# trailing median dollar-volume, with at least MIN_HISTORY of price history).
LIQ_QUANTILE = 0.60
MIN_HISTORY = 250


# ── data access ───────────────────────────────────────────────────────────────
def market_list() -> list:
    """Every market code that has a cleaned_long_<code>.parquet in the cache."""
    if not os.path.isdir(SEED):
        return []
    return [f.split("cleaned_long_")[1].split(".")[0]
            for f in sorted(os.listdir(SEED)) if f.startswith("cleaned_long_")]


def _parquet(market: str) -> str:
    return os.path.join(SEED, f"cleaned_long_{market}.parquet")


def wide(market: str, fields=("Close", "High", "Low", "Volume")):
    """Load a market's long parquet and pivot to wide (Date × Symbol) frames, one per
    OHLCV field. Returns a dict {field: DataFrame} or None if the parquet is missing."""
    p = _parquet(market)
    if not os.path.exists(p):
        return None
    px = pd.read_parquet(p)
    return {f: px.pivot_table(index="Date", columns="Symbol", values=f, aggfunc="last").astype(float)
            for f in fields}


def close_volume(market: str):
    """The common two-frame case: (close, volume) wide frames, or (None, None)."""
    w = wide(market, fields=("Close", "Volume"))
    return (w["Close"], w["Volume"]) if w else (None, None)


# ── universe ──────────────────────────────────────────────────────────────────
def liquid_symbols(close: pd.DataFrame, vol: pd.DataFrame,
                   quantile: float = LIQ_QUANTILE, min_history: int = MIN_HISTORY) -> list:
    """The tradeable universe: names in the top (1−quantile) by trailing median
    dollar-volume with >= min_history of price history (drops penny/illiquid junk)."""
    dv = (close * vol).tail(252).median()
    hist_ok = close.notna().sum() >= min_history
    cut = dv.quantile(quantile)
    return [s for s in close.columns if dv.get(s, 0) >= cut and hist_ok.get(s, False)]


def clean_key(ticker) -> str:
    """Normalise a ticker to a bare, upper-cased symbol (drops the .NS/.T/.BO suffix)
    so the same company joins across data sources."""
    return str(ticker).split(".")[0].upper()


def market_proxy(close: pd.DataFrame, symbols=None, clip: float = 0.5) -> pd.Series:
    """Equal-weight daily return of a universe (defaults to all columns), with each
    day's returns clipped to ±clip so penny-stock glitches don't poison the mean."""
    cols = symbols if symbols is not None else list(close.columns)
    return close[cols].pct_change(fill_method=None).clip(-clip, clip).mean(axis=1)


# ── cross-sectional statistics ────────────────────────────────────────────────
def zscore(s: pd.Series) -> pd.Series:
    """Standardise a cross-section to mean 0 / sd 1 (±inf → NaN; 0s if degenerate)."""
    s = pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan)
    sd = s.std(ddof=0)
    return (s - s.mean()) / sd if sd and not np.isnan(sd) else pd.Series(0.0, index=s.index)


def information_coefficient(signal, fwd_ret) -> float:
    """The IC: Pearson correlation of a signal with the realised forward return.
    Positive = the signal predicts higher returns."""
    s = pd.to_numeric(pd.Series(list(signal)), errors="coerce")
    r = pd.to_numeric(pd.Series(list(fwd_ret)), errors="coerce")
    j = pd.concat([s.reset_index(drop=True), r.reset_index(drop=True)], axis=1).dropna()
    if len(j) < 10 or j.iloc[:, 0].std() == 0 or j.iloc[:, 1].std() == 0:
        return np.nan
    return float(j.iloc[:, 0].corr(j.iloc[:, 1]))


def monotonicity(curve: pd.DataFrame, col: str) -> float:
    """+1 = the quantile curve in `col` rises perfectly from Q1 to Qn (clean effect)."""
    if curve is None or len(curve) < 3 or col not in curve:
        return np.nan
    ranks = pd.Series(curve[col].values).rank().values
    ideal = np.arange(1, len(curve) + 1)
    return float(np.corrcoef(ranks, ideal)[0, 1])


def trend_corr(x) -> float:
    """Scale-free trend of a series = correlation with time ∈ [−1,1] (rising = +)."""
    a = np.asarray(x, dtype=float)
    a = a[np.isfinite(a)]
    if len(a) < 3 or np.std(a) == 0:
        return np.nan
    return float(np.corrcoef(a, np.arange(len(a)))[0, 1])
