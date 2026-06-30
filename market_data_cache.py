# market_data_cache.py
# =====================
# Persistent Parquet cache for 5-year OHLC + financial data.
#
# WHY THIS EXISTS
# ───────────────
# AlQahtani et al. (IJACSA 2025) recommend collecting 5 years of daily OHLC
# via yfinance for reliable ML model training.  Fetching 2,400+ NSE stocks
# every run wastes 30–90 minutes and hammers Yahoo Finance's rate limits.
#
# This module solves that with two strategies:
#   1. Full download on first run → stored as Parquet (10× faster read than CSV,
#      5× smaller file, preserves dtypes perfectly)
#   2. Incremental update on subsequent runs → only fetches rows since the last
#      cached date (typically 1–5 rows per stock, takes < 60 seconds for 2,400 stocks)
#
# CACHE LAYOUT
# ────────────
# ~/Downloads/market_cache/
#   ohlc/           SYMBOL.NS.parquet     5-year daily OHLC (Open/High/Low/Close/Volume)
#   fundamentals/   SYMBOL.NS_annual.parquet    annual income/balance/cashflow
#                   SYMBOL.NS_quarterly.parquet quarterly income
#   index/          NSEI.parquet          Nifty 50 index (10y for backtest)
#                   GSPC.parquet          S&P 500 index
#   meta/           cache_index.json      {ticker: {rows, from, to, updated}}
#
# USAGE
# ─────
#   from market_data_cache import MarketCache
#   cache = MarketCache()
#
#   # Get OHLC for one stock (downloads if missing, updates if stale)
#   df = cache.get_ohlc("RELIANCE.NS")
#
#   # Bulk OHLC for many stocks (incremental — only fetches new rows)
#   ohlc_map = cache.get_ohlc_bulk(["RELIANCE.NS", "TCS.NS", ...])
#
#   # Financial statements (annual / quarterly)
#   annual = cache.get_financials("RELIANCE.NS", freq="annual")
#
#   # Force full refresh
#   cache.refresh("RELIANCE.NS", force=True)
#
#   # Cache health report
#   cache.report()
#
# RUNTIME IMPACT
# ──────────────
#   First run (cold):   ~45 min for 2,400 NSE stocks (5y × 250 rows/yr = 1,250 rows each)
#   Subsequent runs:    < 60 seconds (only new rows since last update)
#   Disk usage:         ~250 MB for full NSE universe (Parquet compressed)
#   Memory per stock:   ~50 KB (1,250 rows × 6 columns)
#
# Paper reference:
#   AlQahtani et al. (2025) — 5-year historical yfinance data collection,
#   forward/backward interpolation for missing values, outlier handling.

import json
import time
import warnings
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

try:
    import yfinance as yf
    _YF_OK = True
except ImportError:
    _YF_OK = False

try:                                   # central data-cleaning gate (shared)
    from stock_utils import clean_ohlcv as _clean_ohlcv_central
except ImportError:
    _clean_ohlcv_central = None

# ── Configuration ─────────────────────────────────────────────────────────────

CACHE_ROOT   = Path.home() / "Downloads" / "market_cache"
OHLC_DIR     = CACHE_ROOT / "ohlc"
FUND_DIR     = CACHE_ROOT / "fundamentals"
INDEX_DIR    = CACHE_ROOT / "index"
META_DIR     = CACHE_ROOT / "meta"
META_FILE    = META_DIR / "cache_index.json"

# Default data windows (paper: 5 years for ML training)
OHLC_YEARS   = 5          # years of OHLC to keep cached
INDEX_YEARS  = 10         # years for index (backtest uses 10y)

# Stale thresholds — how old before we refresh
OHLC_STALE_HOURS  = 20    # refresh OHLC if older than 20h (1 trading session)
FUND_STALE_DAYS   = 7     # refresh financials weekly (quarterly results ≤ 4× / year)
INDEX_STALE_HOURS = 20    # same as OHLC

# yfinance batch parameters
BATCH_SIZE  = 100         # smaller batches → fewer rate limit hits
BATCH_SLEEP = 2.0         # seconds between batches

# Create directories on import
for d in [OHLC_DIR, FUND_DIR, INDEX_DIR, META_DIR]:
    d.mkdir(parents=True, exist_ok=True)


# ════════════════════════════════════════════════════════════════════════════════
# CACHE CLASS
# ════════════════════════════════════════════════════════════════════════════════

class MarketCache:
    """
    Persistent cache for market data using Parquet files.

    Parquet was chosen over CSV/SQLite because:
    - Read speed: 10-50× faster than CSV for columnar data
    - File size: 5-8× smaller than CSV (snappy compression)
    - Type safety: preserves float64, datetime64 dtypes
    - Pandas native: pd.read_parquet() / df.to_parquet()
    """

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self._meta   = self._load_meta()
        # In-memory cache: {ticker: DataFrame}
        # Avoids repeated disk reads within the same Python session.
        # Cleared when the process exits (ephemeral, disk is the source of truth).
        self._mem:   dict = {}

    # ── Meta (JSON index tracking what's cached) ──────────────────────────────

    def _load_meta(self) -> dict:
        if META_FILE.exists():
            try:
                return json.loads(META_FILE.read_text())
            except Exception:
                pass
        return {}

    def _save_meta(self):
        META_FILE.write_text(json.dumps(self._meta, indent=2, default=str))

    def _update_meta(self, key: str, path: Path, df: pd.DataFrame):
        """Record cache entry metadata after writing a file."""
        self._meta[key] = {
            "rows":    len(df),
            "from":    str(df.index.min().date()) if not df.empty else None,
            "to":      str(df.index.max().date()) if not df.empty else None,
            "updated": datetime.now().isoformat(),
            "file":    str(path),
        }
        self._save_meta()

    def _is_stale(self, key: str, stale_hours: float) -> bool:
        """Return True if the cache entry is older than stale_hours."""
        entry = self._meta.get(key)
        if not entry:
            return True
        try:
            updated = datetime.fromisoformat(entry["updated"])
            age_hours = (datetime.now() - updated).total_seconds() / 3600
            return age_hours > stale_hours
        except Exception:
            return True

    def _last_cached_date(self, key: str) -> Optional[date]:
        """Return the last date in the cache for incremental updates."""
        entry = self._meta.get(key)
        if entry and entry.get("to"):
            try:
                return date.fromisoformat(entry["to"])
            except Exception:
                pass
        return None

    # ── OHLC ─────────────────────────────────────────────────────────────────

    def _ohlc_path(self, ticker: str) -> Path:
        safe = ticker.replace("/", "_").replace(":", "_")
        return OHLC_DIR / f"{safe}.parquet"

    def get_ohlc(self, ticker: str, force: bool = False) -> pd.DataFrame:
        """
        Get 5-year daily OHLC for one ticker.
        - First call: downloads full 5-year history, saves to Parquet
        - Subsequent calls: reads from Parquet, appends only new rows
        - Missing values: forward-filled then backward-filled (AlQahtani et al. 2025)
        """
        key  = f"ohlc:{ticker}"
        path = self._ohlc_path(ticker)

        if not force and path.exists() and not self._is_stale(key, OHLC_STALE_HOURS):
            # L1: in-memory (instantaneous)
            if ticker in self._mem:
                return self._mem[ticker]
            # L2: disk Parquet (~5-20ms per file with parallel IO)
            df = pd.read_parquet(path)
            self._mem[ticker] = df
            return df

        if path.exists() and not force:
            # Cache exists but may be stale — incremental update
            existing  = pd.read_parquet(path)
            last_date = self._last_cached_date(key)
            if last_date:
                # Only fetch from the day after last cached date
                start = last_date + timedelta(days=1)
                if start >= date.today():
                    # Nothing to fetch — already up to date
                    return existing
                new_df = self._yf_download_single(ticker, start=start)
                if not new_df.empty:
                    combined = pd.concat([existing, new_df]).sort_index()
                    combined = combined[~combined.index.duplicated(keep="last")]
                    combined = self._clean_ohlc(combined)
                    self._write_parquet(path, combined)
                    self._update_meta(key, path, combined)
                    if self.verbose:
                        print(f"  Cache updated: {ticker} +{len(new_df)} rows "
                              f"(total {len(combined)})")
                    return combined
                return existing
        else:
            # Cold start — download full history
            start = date.today() - timedelta(days=OHLC_YEARS * 365 + 30)
            df    = self._yf_download_single(ticker, start=start)
            if df.empty:
                return df
            df = self._clean_ohlc(df)
            self._write_parquet(path, df)
            self._update_meta(key, path, df)
            if self.verbose:
                print(f"  Cache created: {ticker} {len(df)} rows "
                      f"({df.index.min().date()} – {df.index.max().date()})")
            return df

    def get_ohlc_bulk(self, tickers: list, force: bool = False,
                      workers: int = 1) -> dict:
        """
        Get OHLC for many tickers efficiently.

        Strategy:
          1. Split tickers into: (a) needs full download, (b) needs increment, (c) fresh
          2. Bulk-download group (a) in batches of BATCH_SIZE via yfinance.download()
             — much faster than individual downloads
          3. Incremental-update group (b) in batches
          4. Read group (c) straight from disk

        Returns dict: {ticker: DataFrame}
        """
        needs_full  = []
        needs_incr  = []
        fresh       = []

        for t in tickers:
            key  = f"ohlc:{t}"
            path = self._ohlc_path(t)
            if not path.exists():
                needs_full.append(t)
            elif force or self._is_stale(key, OHLC_STALE_HOURS):
                needs_incr.append(t)
            else:
                fresh.append(t)

        if self.verbose:
            print(f"  Cache: {len(fresh)} fresh | {len(needs_incr)} incremental | "
                  f"{len(needs_full)} cold-start")

        result: dict[str, pd.DataFrame] = {}

        # ── Read fresh: L1 memory first, L2 disk in parallel ────────────────
        from concurrent.futures import ThreadPoolExecutor as _TPE, as_completed as _ac

        def _read_one(t):
            # L1: memory
            if t in self._mem:
                return t, self._mem[t], "mem"
            # L2: disk
            path = self._ohlc_path(t)
            try:
                df = pd.read_parquet(path)
                self._mem[t] = df    # promote to L1
                return t, df, "disk"
            except Exception:
                return t, None, "error"

        n_workers = min(16, max(1, len(fresh)))
        with _TPE(max_workers=n_workers) as pool:
            futs = {pool.submit(_read_one, t): t for t in fresh}
            for fut in _ac(futs):
                t, df, src = fut.result()
                if df is not None:
                    result[t] = df
                else:
                    needs_full.append(t)

        # ── Bulk download for cold-start ─────────────────────────────────────
        if needs_full:
            start = date.today() - timedelta(days=OHLC_YEARS * 365 + 30)
            downloaded = self._bulk_yf_download(needs_full, start=start)
            for t, df in downloaded.items():
                df = self._clean_ohlc(df)
                path = self._ohlc_path(t)
                self._write_parquet(path, df)
                self._update_meta(f"ohlc:{t}", path, df)
                result[t] = df

        # ── Incremental update ───────────────────────────────────────────────
        if needs_incr:
            # Group by last date to batch tickers needing similar ranges
            # For simplicity, fetch all from yesterday (most will only be 1–2 rows)
            start = date.today() - timedelta(days=5)  # small window for incremental
            downloaded = self._bulk_yf_download(needs_incr, start=start)
            for t in needs_incr:
                path = self._ohlc_path(t)
                try:
                    existing = pd.read_parquet(path) if path.exists() else pd.DataFrame()
                    new_rows = downloaded.get(t, pd.DataFrame())
                    if not new_rows.empty:
                        combined = pd.concat([existing, new_rows]).sort_index()
                        combined = combined[~combined.index.duplicated(keep="last")]
                        combined = self._clean_ohlc(combined)
                        self._write_parquet(path, combined)
                        self._update_meta(f"ohlc:{t}", path, combined)
                        result[t] = combined
                    elif not existing.empty:
                        result[t] = existing
                except Exception:
                    if t in downloaded:
                        result[t] = downloaded[t]

        if self.verbose:
            print(f"  Cache result: {len(result)}/{len(tickers)} tickers loaded")
        return result

    # ── Financials ────────────────────────────────────────────────────────────

    def get_financials(self, ticker: str, freq: str = "annual",
                       force: bool = False) -> dict:
        """
        Get income statement + balance sheet + cash flow for one ticker.
        freq: 'annual' or 'quarterly'
        Returns dict: {income_stmt, balance_sheet, cash_flow}
        """
        key  = f"fund:{ticker}:{freq}"
        path = FUND_DIR / f"{ticker.replace('/', '_')}_{freq}.parquet"

        if not force and path.exists() and not self._is_stale(key, FUND_STALE_DAYS * 24):
            try:
                combined = pd.read_parquet(path)
                # Unpack sheets stored as a stacked DataFrame
                result = {}
                for sheet in combined["_sheet"].unique():
                    sub = combined[combined["_sheet"] == sheet].drop("_sheet", axis=1)
                    sub.columns = pd.to_datetime(sub.columns, errors="coerce")
                    result[sheet] = sub
                return result
            except Exception:
                pass

        if not _YF_OK:
            return {}

        try:
            t = yf.Ticker(ticker)
            if freq == "annual":
                data = {
                    "income_stmt":   _first_df(t, "income_stmt",  "financials"),
                    "balance_sheet": _first_df(t, "balance_sheet"),
                    "cash_flow":     _first_df(t, "cash_flow",    "cashflow"),
                }
            else:
                data = {
                    "quarterly_income": _first_df(t, "quarterly_income_stmt",
                                                    "quarterly_financials"),
                }

            # Store as stacked Parquet (all sheets in one file)
            frames = []
            for sheet, df in data.items():
                if df is not None and not df.empty:
                    df2 = df.copy()
                    df2["_sheet"] = sheet
                    frames.append(df2)

            if frames:
                combined = pd.concat(frames)
                combined.columns = [str(c) for c in combined.columns]
                self._write_parquet(path, combined)
                self._meta[key] = {
                    "updated": datetime.now().isoformat(),
                    "sheets":  list(data.keys()),
                }
                self._save_meta()

            return {k: v for k, v in data.items() if v is not None and not v.empty}
        except Exception as e:
            if self.verbose:
                print(f"  Financial fetch failed for {ticker}: {e}")
            return {}

    # ── Index data ────────────────────────────────────────────────────────────

    def get_index(self, ticker: str = "^NSEI", force: bool = False) -> pd.DataFrame:
        """
        Get 10-year index data (Nifty 50 / S&P 500) with 200 DMA pre-computed.
        Cached separately from stock OHLC because it needs longer history.
        """
        safe = ticker.replace("^", "").replace("/", "_")
        key  = f"index:{ticker}"
        path = INDEX_DIR / f"{safe}.parquet"

        if not force and path.exists() and not self._is_stale(key, INDEX_STALE_HOURS):
            df = pd.read_parquet(path)
            return df

        start = date.today() - timedelta(days=INDEX_YEARS * 365 + 30)
        df    = self._yf_download_single(ticker, start=start)
        if df.empty:
            return df

        # Pre-compute regime indicators (done once, stored in cache)
        df["dma50"]     = df["Close"].rolling(50).mean()
        df["dma200"]    = df["Close"].rolling(200).mean()
        df["dma200_sl"] = df["dma200"].diff(5)
        df["vol20"]     = df["Close"].pct_change().rolling(20).std() * np.sqrt(252) * 100

        self._write_parquet(path, df)
        self._meta[key] = {
            "updated": datetime.now().isoformat(),
            "rows":    len(df),
            "from":    str(df.index.min().date()),
            "to":      str(df.index.max().date()),
        }
        self._save_meta()
        if self.verbose:
            print(f"  Index cached: {ticker} {len(df)} bars")
        return df

    # ── Cache management ──────────────────────────────────────────────────────

    def report(self) -> pd.DataFrame:
        """Print a summary of the cache: what's stored, how old, how many rows."""
        rows = []
        for key, meta in self._meta.items():
            age = "—"
            if meta.get("updated"):
                try:
                    updated = datetime.fromisoformat(meta["updated"])
                    delta = datetime.now() - updated
                    if delta.total_seconds() < 3600:
                        age = f"{delta.seconds//60}m"
                    elif delta.total_seconds() < 86400:
                        age = f"{delta.seconds//3600}h"
                    else:
                        age = f"{delta.days}d"
                except Exception:
                    pass
            rows.append({
                "Key":     key[:50],
                "Rows":    meta.get("rows", "—"),
                "From":    meta.get("from", "—"),
                "To":      meta.get("to", "—"),
                "Age":     age,
            })
        if not rows:
            print("  Cache is empty — run cache.get_ohlc_bulk(symbols) to populate")
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        ohlc_count = sum(1 for k in self._meta if k.startswith("ohlc:"))
        fund_count = sum(1 for k in self._meta if k.startswith("fund:"))
        idx_count  = sum(1 for k in self._meta if k.startswith("index:"))
        size_mb    = sum(
            f.stat().st_size / 1e6
            for d in [OHLC_DIR, FUND_DIR, INDEX_DIR]
            for f in d.glob("*.parquet")
        )
        print(f"\n  Cache report: {ohlc_count} OHLC | {fund_count} fundamental | "
              f"{idx_count} index | {size_mb:.1f} MB on disk")
        return df

    def purge(self, ticker: str = None):
        """
        Remove a specific ticker or wipe the entire cache.
        ticker=None → full wipe (use before switching from 1y to 5y window).
        """
        if ticker:
            for path in [self._ohlc_path(ticker),
                         FUND_DIR / f"{ticker.replace('/', '_')}_annual.parquet",
                         FUND_DIR / f"{ticker.replace('/', '_')}_quarterly.parquet"]:
                if path.exists():
                    path.unlink()
            keys = [k for k in self._meta if ticker in k]
            for k in keys:
                del self._meta[k]
            self._save_meta()
        else:
            for d in [OHLC_DIR, FUND_DIR, INDEX_DIR]:
                for f in d.glob("*.parquet"):
                    f.unlink()
            self._meta = {}
            self._save_meta()
            print("  Cache wiped — next run will do a full cold-start download")

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _yf_download_single(self, ticker: str, start: date) -> pd.DataFrame:
        """Download OHLC for a single ticker with retry."""
        if not _YF_OK:
            return pd.DataFrame()
        for attempt in range(3):
            try:
                df = yf.download(ticker, start=start, auto_adjust=True, progress=False)
                if isinstance(df.columns, pd.MultiIndex):
                    df = df.xs(ticker, axis=1, level=1)
                if not df.empty:
                    return df[["Open","High","Low","Close","Volume"]].dropna(how="all")
            except Exception as e:
                if "Rate" in str(e) or "429" in str(e):
                    time.sleep(15 * (attempt + 1))
                else:
                    break
        return pd.DataFrame()

    def _bulk_yf_download(self, tickers: list, start: date) -> dict:
        """Batch-download OHLC for multiple tickers."""
        if not _YF_OK:
            return {}
        result = {}
        batches = [tickers[i:i+BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]
        n_total = len(tickers)
        n_done  = 0

        for idx, batch in enumerate(batches, 1):
            print(f"    Downloading batch {idx}/{len(batches)} "
                  f"({len(batch)} tickers) …", end=" ", flush=True)
            for attempt in range(3):
                try:
                    raw = yf.download(
                        batch, start=start, auto_adjust=True,
                        threads=True, progress=False
                    )
                    if raw.empty:
                        print("empty"); break
                    if isinstance(raw.columns, pd.MultiIndex):
                        for t in batch:
                            try:
                                df = raw.xs(t, axis=1, level=1).dropna(how="all")
                                if not df.empty and "Close" in df.columns:
                                    result[t] = df[["Open","High","Low","Close","Volume"]]
                            except KeyError:
                                pass
                    else:
                        if not raw.empty:
                            result[batch[0]] = raw[["Open","High","Low","Close","Volume"]]
                    ok = sum(1 for t in batch if t in result)
                    print(f"OK ({ok}/{len(batch)})")
                    n_done += ok
                    break
                except Exception as e:
                    if "Rate" in str(e) or "429" in str(e) or "Too Many" in str(e):
                        wait = 30 * (attempt + 1)
                        print(f"\n    Rate limited — waiting {wait}s …", end="", flush=True)
                        time.sleep(wait)
                    else:
                        print(f"ERROR: {e}"); break
            if idx < len(batches):
                time.sleep(BATCH_SLEEP)

        print(f"  Bulk download complete: {n_done}/{n_total} tickers")
        return result

    @staticmethod
    def _clean_ohlc(df: pd.DataFrame) -> pd.DataFrame:
        """Clean OHLC before caching — delegates to the central clean_ohlcv gate.

        Cache-specific prep: coerce to a sorted DatetimeIndex and drop unparseable
        dates first, then hand off to stock_utils.clean_ohlcv() so the cache stores
        exactly the same hygiene (numeric coercion, dedup, non-positive-price drop,
        OHLC-integrity repair, bad-print/split neutralisation) every consumer sees.
        Falls back to the prior ffill/bfill behaviour if stock_utils is unavailable.
        """
        if df.empty:
            return df
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, errors="coerce")
        df = df[~df.index.isna()].sort_index()
        if _clean_ohlcv_central is not None:
            cleaned = _clean_ohlcv_central(df, min_bars=1)
            return cleaned if cleaned is not None else df.iloc[0:0]
        # fallback (stock_utils not importable)
        df = df[~df.index.duplicated(keep="last")]
        return df.ffill().bfill()

    @staticmethod
    def _write_parquet(path: Path, df: pd.DataFrame):
        """Atomic write: temp file → rename (prevents corruption on crash)."""
        tmp = path.with_suffix(".tmp")
        try:
            df.to_parquet(tmp, compression="snappy", index=True)
            tmp.rename(path)
        except Exception:
            if tmp.exists():
                tmp.unlink()
            raise


# ── Module-level helpers (used by other scripts) ──────────────────────────────

def _first_df(ticker, *attrs):
    for a in attrs:
        df = getattr(ticker, a, None)
        if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
            return df
    return None


# Singleton cache instance (created once, reused across imports)
_CACHE_INSTANCE: Optional[MarketCache] = None

def get_cache() -> MarketCache:
    global _CACHE_INSTANCE
    if _CACHE_INSTANCE is None:
        _CACHE_INSTANCE = MarketCache(verbose=True)
    return _CACHE_INSTANCE


def warm_cache(symbols: list, period_years: int = 5,
               index_tickers: list = None, verbose: bool = True):
    """
    One-shot cache warm-up for a symbol list.
    Call this once (takes ~45 min for full NSE) — every subsequent run is fast.

    symbols:      list of yfinance tickers (e.g. ['RELIANCE.NS', 'TCS.NS', ...])
    period_years: how many years of OHLC to store (paper recommends 5)
    index_tickers: index symbols to cache (default: ['^NSEI', '^GSPC'])
    """
    if index_tickers is None:
        index_tickers = ["^NSEI", "^GSPC"]

    cache = get_cache()

    print(f"  Warming cache: {len(symbols)} stocks, {period_years}-year window")
    print(f"  Storage: {CACHE_ROOT}")
    print(f"  First run: ~{len(symbols)//100 * 3} minutes | "
          f"Subsequent runs: < 60 seconds\n")

    # Download index first (fast, needed for regime classification)
    for idx_sym in index_tickers:
        cache.get_index(idx_sym)

    # Bulk OHLC download
    cache.get_ohlc_bulk(symbols)

    cache.report()
    print(f"\n  ✅ Cache warm. All future runs will use local Parquet files.")
