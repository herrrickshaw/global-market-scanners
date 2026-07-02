#!/usr/bin/env python3
"""
apiclient.py
------------
One governed path for every external data source, so the whole platform stays under
rate limits. Provides per-source throttling (min-interval + max-concurrency), adaptive
backoff (auto-slow-down on 429/crumb errors, decay back when healthy), and retry with
exponential backoff + jitter. Also cuts the *number* of calls: yfinance bulk downloads
are deduped and chunked, and every fetcher is cache-first elsewhere so each ticker is
fetched at most once.

Per-source policy (min seconds between calls, max concurrent) — tuned to what actually
survived this project's runs (yfinance 401 "crumb" storms appeared above ~3 workers):

    yfinance   0.4 s, 3    edgar     0.12 s, 5   (SEC fair-access ~10/s)
    gleif      0.25 s, 4   wikidata  60 s, 1     (their outage rule)
    opencorp   0.5 s, 2    default   0.5 s, 3

Usage:
    from apiclient import yf_download, yf_info, http_get, robust, throttle
    ohlc = yf_download(tickers, period="5y")      # {ticker: DataFrame}, governed
    info = yf_info("AAPL")                          # throttled + retried
    r    = http_get("edgar", url, headers=UA)       # governed requests.get
"""

from __future__ import annotations

import random
import threading
import time

LIMITS = {                       # source -> (min_interval_s, max_concurrency)
    "yfinance": (0.40, 3),
    "edgar":    (0.12, 5),
    "gleif":    (0.25, 4),
    "wikidata": (60.0, 1),
    "opencorporates": (0.50, 2),
    "openalex":  (0.15, 4),          # polite pool (with mailto): generous
    "crossref":  (0.20, 3),          # polite pool
    "arxiv":     (3.00, 1),          # arXiv asks for <= 1 request / 3s
    "semanticscholar": (1.10, 1),    # ~1 req/s unauthenticated
}
_DEFAULT = (0.50, 3)
YF_CHUNK = 50                    # tickers per yfinance batch (small enough to avoid crumb storms)
_RATE_HINTS = ("rate", "429", "too many", "crumb", "401", "throttl",
               "unavailable", "timed out", "connection reset")


def _is_rate_error(e) -> bool:
    return any(h in str(e).lower() for h in _RATE_HINTS)


class _Source:
    """Thread-safe adaptive throttle for one source."""
    def __init__(self, interval, concurrency):
        self.interval = interval
        self.sem = threading.BoundedSemaphore(concurrency)
        self.lock = threading.Lock()
        self.next_ok = 0.0
        self.penalty = 1.0                       # multiplies the interval; grows on errors

    def acquire(self):
        self.sem.acquire()
        with self.lock:
            now = time.monotonic()
            wait = max(0.0, self.next_ok - now)
            self.next_ok = max(now, self.next_ok) + self.interval * self.penalty
        if wait > 0:
            time.sleep(wait)

    def release(self):
        self.sem.release()

    def penalize(self):
        with self.lock:
            self.penalty = min(self.penalty * 2.0, 32.0)   # exponential slow-down

    def relax(self):
        with self.lock:
            self.penalty = max(1.0, self.penalty * 0.92)   # gentle recovery


_sources: dict = {}
_sources_lock = threading.Lock()


def _src(name) -> _Source:
    with _sources_lock:
        if name not in _sources:
            _sources[name] = _Source(*LIMITS.get(name, _DEFAULT))
        return _sources[name]


class throttle:
    """Context manager: hold a rate slot for `source` (blocks to respect the limit)."""
    def __init__(self, source):
        self.s = _src(source)

    def __enter__(self):
        self.s.acquire(); return self

    def __exit__(self, *a):
        self.s.release()


def robust(source, fn, *args, retries=5, **kwargs):
    """Throttled call with retry + exponential backoff + jitter; adaptive on rate errors."""
    s = _src(source)
    last = None
    for attempt in range(retries):
        with throttle(source):
            try:
                out = fn(*args, **kwargs)
                s.relax()
                return out
            except Exception as e:                # noqa: BLE001 — we classify below
                last = e
                if _is_rate_error(e):
                    s.penalize()
                elif attempt >= 2:                # non-rate error, give up after a few tries
                    raise
        time.sleep(min(90.0, (2 ** attempt) * 1.5) + random.uniform(0, 1.0))
    raise last if last else RuntimeError(f"{source}: retries exhausted")


# ── yfinance (the hot source) ────────────────────────────────────────────────
def yf_download(tickers, period=None, start=None, **kw) -> dict:
    """Deduped, chunked, governed bulk OHLC. Returns {ticker: DataFrame}."""
    import pandas as pd
    import yfinance as yf
    uniq = list(dict.fromkeys(t for t in (tickers if isinstance(tickers, (list, tuple)) else [tickers]) if t))
    out = {}
    for i in range(0, len(uniq), YF_CHUNK):
        batch = uniq[i:i + YF_CHUNK]

        def _dl():
            return yf.download(batch, period=period, start=start, auto_adjust=True,
                               progress=False, group_by="ticker", threads=True, **kw)
        try:
            data = robust("yfinance", _dl)
        except Exception:
            continue
        for t in batch:
            try:
                df = data[t] if isinstance(data.columns, pd.MultiIndex) else data
                df = df.dropna(how="all")
                if df is not None and not df.empty:
                    out[t] = df
            except Exception:
                continue
    return out


def yf_info(ticker) -> dict:
    """Throttled + retried yfinance fundamentals for one ticker."""
    import yfinance as yf
    return robust("yfinance", lambda: yf.Ticker(ticker).get_info())


# ── generic HTTP (EDGAR, GLEIF, OpenCorporates, Wikidata …) ───────────────────
def http_get(source, url, headers=None, timeout=45, retries=5):
    """Governed requests.get — treats 429 as a rate error so backoff kicks in.
    `retries` is exposed so callers that have their own fallback (e.g. the literature
    scout, which fails over to another API) can fail fast instead of exhausting the
    full backoff schedule on a source that is down."""
    import requests

    def _g():
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code == 429:
            raise RuntimeError("HTTP 429 rate limited")
        return r
    return robust(source, _g, retries=retries)


if __name__ == "__main__":
    import sys
    t0 = time.time()
    d = yf_download(sys.argv[1:] or ["AAPL", "MSFT", "NVDA"], period="1y")
    print(f"yf_download {len(d)} tickers in {time.time()-t0:.1f}s (governed)")
