# stock_utils.py
# ==============
# Shared utility functions — eliminates code duplication across all scan scripts.
#
# Before this module, these helpers were copy-pasted across 10–14 files:
#   _first_df()      — 14 copies
#   _row()           — 12 copies
#   _series()        — 8 copies
#   yfinance MultiIndex extraction — 10+ inline copies
#   bulk download with rate-limit retry — 10 copies
#
# Import once:  from stock_utils import first_df, row, series, extract_ticker_df,
#                                       bulk_download, fin_metric
#
# Every script now shares ONE implementation. Bug fixes apply everywhere.

from __future__ import annotations

import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

try:
    import yfinance as yf
    _YF_OK = True
except ImportError:
    _YF_OK = False


# ══════════════════════════════════════════════════════════════════════════════
# FINANCIAL STATEMENT HELPERS  (replaces _first_df / _row / _series everywhere)
# ══════════════════════════════════════════════════════════════════════════════

def first_df(ticker, *attrs) -> Optional[pd.DataFrame]:
    """Return the first non-empty DataFrame among the ticker's named attributes.

    Replaces the `_first_df` helper duplicated in 14 files.
    e.g. first_df(t, "income_stmt", "financials")
    """
    for attr in attrs:
        df = getattr(ticker, attr, None)
        if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
            return df
    return None


def row(df: Optional[pd.DataFrame], *names, col: int = 0) -> Optional[float]:
    """Safely extract a single float from a financial-statement row.

    Replaces the `_row` helper duplicated in 12 files.
    Tries each name in order; returns the value at column `col` or None.
    """
    if df is None or df.empty:
        return None
    for name in names:
        if name in df.index:
            try:
                val = df.loc[name].iloc[col]
                return float(val) if pd.notna(val) else None
            except Exception:
                pass
    return None


def series(df: Optional[pd.DataFrame], *names) -> List[float]:
    """Extract all available (non-NaN) values for a row across all periods.

    Replaces the `_series` helper duplicated in 8 files.
    """
    if df is None or df.empty:
        return []
    for name in names:
        if name in df.index:
            return [float(v) for v in df.loc[name].dropna() if pd.notna(v)]
    return []


def fin_metric(df, name_variants: tuple, col: int = 0,
               default: Optional[float] = None) -> Optional[float]:
    """Convenience wrapper: row() with an explicit default fallback."""
    val = row(df, *name_variants, col=col)
    return val if val is not None else default


# ══════════════════════════════════════════════════════════════════════════════
# YFINANCE EXTRACTION  (replaces inline `raw.xs(t, axis=1, level=1)` in 10+ files)
# ══════════════════════════════════════════════════════════════════════════════

OHLCV_COLS = ["Open", "High", "Low", "Close", "Volume"]


# ══════════════════════════════════════════════════════════════════════════════
# DATA CLEANING  (single hygiene gate for every source: yfinance, nse, bhavcopy)
# ══════════════════════════════════════════════════════════════════════════════
# Raw market data is dirty: Yahoo returns stale/forward-filled bars, zero or
# negative prices, duplicate timestamps, unsorted indices, un-split-adjusted
# jumps, and OHLC rows that violate low<=open/close<=high. Backtests and
# screeners silently corrupt when fed this. clean_ohlcv() is the ONE place every
# fetched price frame passes through, so the rules are consistent everywhere.

# Daily move beyond this (±) with no matching volume is treated as a bad print /
# un-adjusted split artifact, not a real move. 0.60 = 60%.
MAX_DAILY_MOVE = 0.60


def clean_ohlcv(df: Optional[pd.DataFrame], ticker: str = "",
                max_daily_move: float = MAX_DAILY_MOVE,
                min_bars: int = 1, verbose: bool = False) -> Optional[pd.DataFrame]:
    """Clean a single OHLCV frame. Returns None if nothing usable survives.

    Steps (in order):
      1. Keep only OHLCV columns; coerce all to numeric (bad strings -> NaN).
      2. Sort by date; drop duplicate timestamps (keep last = most-restated).
      3. Drop rows with no Close (the one column everything depends on).
      4. Forward-fill OHLC gaps from Close; fill missing Volume with 0.
      5. Drop rows with non-positive Close/High/Low (zero or negative prices).
      6. Repair OHLC integrity: High = max(O,H,L,C), Low = min(O,H,L,C).
      7. Flag & null out impossible single-day jumps (|ret| > max_daily_move
         on near-zero volume) — un-adjusted splits / bad ticks — then ffill.
      8. Drop leading/trailing all-NaN rows; enforce min_bars.

    Idempotent: cleaning already-clean data is a no-op.
    """
    if df is None or df.empty:
        return None
    df = df.copy()

    # 1. column subset + numeric coercion
    keep = [c for c in OHLCV_COLS if c in df.columns]
    df = df[keep] if keep else df
    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # 2. chronological order, de-duplicate timestamps
    try:
        df = df[~df.index.duplicated(keep="last")].sort_index()
    except TypeError:
        pass

    # 3. Close is mandatory
    if "Close" not in df.columns:
        return None
    before = len(df)
    df = df[df["Close"].notna()]

    # 4. fill OHLC gaps from Close; Volume gaps -> 0
    for c in ("Open", "High", "Low"):
        if c in df.columns:
            df[c] = df[c].fillna(df["Close"])
    if "Volume" in df.columns:
        df["Volume"] = df["Volume"].fillna(0).clip(lower=0)

    # 5. drop non-positive prices (zero/negative are always bad data)
    price_cols = [c for c in ("Close", "High", "Low", "Open") if c in df.columns]
    df = df[(df[price_cols] > 0).all(axis=1)]
    if df.empty:
        return None

    # 6. repair OHLC integrity violations
    if {"Open", "High", "Low", "Close"}.issubset(df.columns):
        ohlc = df[["Open", "High", "Low", "Close"]]
        df["High"] = ohlc.max(axis=1)
        df["Low"] = ohlc.min(axis=1)

    # 7. neutralise impossible jumps on no volume (un-adjusted split / bad tick)
    ret = df["Close"].pct_change().abs()
    vol = df["Volume"] if "Volume" in df.columns else pd.Series(1, index=df.index)
    bad = (ret > max_daily_move) & (vol <= 0)
    n_bad = int(bad.sum())
    if n_bad:
        df.loc[bad, price_cols] = np.nan
        df[price_cols] = df[price_cols].ffill()
        df = df[df["Close"].notna()]

    # 8. trim + minimum length
    df = df.dropna(how="all")
    dropped = before - len(df)
    if verbose and (dropped or n_bad):
        print(f"    clean_ohlcv[{ticker or '?'}]: dropped {dropped} rows, "
              f"neutralised {n_bad} bad-print bars -> {len(df)} clean bars")
    if df.empty or len(df) < min_bars:
        return None
    return df


def clean_financials(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """Light hygiene for income-statement / balance-sheet / cash-flow frames.

    yfinance financials arrive with columns = reporting dates (newest first)
    and occasional all-NaN duplicate period columns. Coerce to numeric, drop
    fully-empty columns, and ensure newest period is column 0 (what row()/
    fin_metric() assume when they read col=0).
    """
    if df is None or df.empty:
        return None
    df = df.apply(pd.to_numeric, errors="coerce")
    df = df.dropna(axis=1, how="all")
    if df.empty:
        return None
    try:                                  # newest reporting period first
        if list(df.columns) != sorted(df.columns, reverse=True):
            df = df.reindex(sorted(df.columns, reverse=True), axis=1)
    except TypeError:
        pass
    return df


def extract_ticker_df(raw: pd.DataFrame, ticker: str,
                      min_bars: int = 1, clean: bool = True) -> Optional[pd.DataFrame]:
    """Pull one ticker's OHLCV frame out of a yfinance bulk-download result.

    Handles both MultiIndex (bulk) and flat (single) column layouts, then runs
    the data-cleaning gate (clean_ohlcv) so every consumer gets hygienic bars.
    Replaces the repeated try/except xs() blocks across all scan scripts.
    """
    try:
        if isinstance(raw.columns, pd.MultiIndex):
            df = raw.xs(ticker, axis=1, level=1).dropna(how="all")
        else:
            df = raw.dropna(how="all")
        if df.empty:
            return None
        if clean:
            return clean_ohlcv(df, ticker=ticker, min_bars=min_bars)
        if len(df) < min_bars:
            return None
        keep = [c for c in OHLCV_COLS if c in df.columns]
        return df[keep] if keep else df
    except (KeyError, Exception):
        return None


def is_rate_limited(err: Exception) -> bool:
    """Detect yfinance/HTTP rate-limit errors. Replaces inline string checks."""
    s = str(err)
    return any(k in s for k in ("Rate", "429", "Too Many", "Crumb"))


def bulk_download(tickers: List[str], period: str = "1y",
                  batch_size: int = 100, sleep_between: float = 1.5,
                  max_retries: int = 3, min_bars: int = 30,
                  start=None, verbose: bool = True) -> Dict[str, pd.DataFrame]:
    """Batch yfinance download with rate-limit retry + back-off.

    Single implementation replacing the bulk-download loop copy-pasted in
    market_data_cache, full_indian_market_scan, full_us_market_scan,
    walk_forward_backtest, ipo_tracker, and backtest_screeners.

    Returns {ticker: OHLCV DataFrame} (key strips .NS/.BO suffix).
    """
    if not _YF_OK:
        return {}
    result: Dict[str, pd.DataFrame] = {}
    batches = [tickers[i:i + batch_size] for i in range(0, len(tickers), batch_size)]
    if verbose:
        print(f"  bulk_download: {len(tickers)} tickers, {len(batches)} batches "
              f"(size={batch_size}, period={period})")

    for idx, batch in enumerate(batches, 1):
        if verbose:
            print(f"    Batch {idx}/{len(batches)} ({len(batch)}) …", end=" ", flush=True)
        for attempt in range(max_retries):
            try:
                kwargs = dict(auto_adjust=True, threads=True, progress=False)
                if start is not None:
                    kwargs["start"] = start
                else:
                    kwargs["period"] = period
                raw = yf.download(batch, **kwargs)
                if raw is None or raw.empty:
                    if verbose: print("empty")
                    break
                for tkr in batch:
                    df = extract_ticker_df(raw, tkr, min_bars=min_bars)
                    if df is not None:
                        result[_strip_suffix(tkr)] = df
                ok = sum(1 for t in batch if _strip_suffix(t) in result)
                if verbose: print(f"OK ({ok}/{len(batch)})")
                break
            except Exception as e:
                if is_rate_limited(e) and attempt < max_retries - 1:
                    wait = 20 * (attempt + 1)
                    if verbose: print(f"\n      rate-limited, wait {wait}s …", end="", flush=True)
                    time.sleep(wait)
                else:
                    if verbose: print(f"ERROR: {str(e)[:50]}")
                    break
        if idx < len(batches):
            time.sleep(sleep_between)

    if verbose:
        print(f"  bulk_download complete: {len(result)}/{len(tickers)} loaded")
    return result


def _strip_suffix(ticker: str) -> str:
    """RELIANCE.NS -> RELIANCE; AAPL -> AAPL."""
    return ticker.replace(".NS", "").replace(".BO", "")


# ══════════════════════════════════════════════════════════════════════════════
# PARALLEL EXECUTION  (replaces ThreadPoolExecutor boilerplate in 8 files)
# ══════════════════════════════════════════════════════════════════════════════

def parallel_map(fn: Callable, items: list, workers: int = 8,
                 progress_every: int = 100, label: str = "items",
                 verbose: bool = True) -> list:
    """Run fn over items in a thread pool, collecting non-None results.

    Replaces the repeated ThreadPoolExecutor + as_completed + progress-print
    blocks scattered across the fundamental-scan scripts.
    """
    results, done = [], 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, it): it for it in items}
        for fut in as_completed(futures):
            done += 1
            try:
                r = fut.result()
                if r is not None:
                    results.append(r)
            except Exception:
                pass
            if verbose and (done % progress_every == 0 or done == len(items)):
                print(f"    {done}/{len(items)} {label} processed, {len(results)} results")
    return results


# ══════════════════════════════════════════════════════════════════════════════
# NUMERIC / NORMALISATION HELPERS  (small repeated formulas)
# ══════════════════════════════════════════════════════════════════════════════

def pct_change(new: float, old: float) -> Optional[float]:
    """Percentage change with safe division. Replaces inline (a-b)/b*100."""
    if old is None or new is None or old == 0:
        return None
    return (new - old) / abs(old) * 100


def cagr(latest: float, earliest: float, years: int) -> Optional[float]:
    """Compound annual growth rate %. Used by Coffee Can across scripts."""
    if not latest or not earliest or earliest <= 0 or years < 1:
        return None
    return ((latest / earliest) ** (1 / years) - 1) * 100


def normalise_debt_to_equity(de_raw: Optional[float]) -> Optional[float]:
    """yfinance reports D/E as % sometimes (45.2 = 0.452x). Normalise if >10.

    Replaces the `de = de_raw/100 if de_raw>10 else de_raw` idiom in 6 files.
    """
    if de_raw is None:
        return None
    return de_raw / 100 if de_raw > 10 else de_raw


def market_cap_crores(market_cap: Optional[float]) -> float:
    """Convert raw market cap to Indian crores (÷1e7)."""
    return (market_cap or 0) / 1e7
