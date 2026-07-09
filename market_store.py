#!/usr/bin/env python3
"""
market_store.py
---------------
Cassandra-backed OHLC cache — the real speedup for repeated runs. Instead of
re-downloading 5 years of history from yfinance on every scan/backtest (the
bottleneck that keeps hitting rate limits), history is stored once in Cassandra
and read locally thereafter.

Drop-in usage:
    from market_store import cached_download
    ohlc = cached_download(tickers, years=5)   # {ticker: DataFrame}
    # 1st call: downloads misses + writes to Cassandra
    # later calls: served from Cassandra (no network)

Cassandra and the Kafka CDC publish are MANDATORY, not optional fallbacks.
Every write path (put_ohlc) requires a live Cassandra session and requires the
Kafka publish to succeed — this module raises MarketStoreUnavailable /
CDCPublishError rather than degrading silently, so a broken node or a stopped
consumer surfaces immediately instead of quietly reverting to uncached,
unmonitored yfinance calls.

`kafka_cdc_consumer.py` (pure Python, no JVM) is the default consumer for this
topic and is meant to run at all times — install it as a persistent service
via kafka_cdc_consumer.plist, the same way Cassandra/Kafka run via `brew
services` rather than as a one-off foreground process. `flink_cdc_consumer.py`
does the same job on PyFlink and is kept as an option for when a real
multi-node Flink deployment earns its operational cost, but the `apache-flink`
Homebrew formula has no `brew services` entry — a manually-started standalone
cluster is not a persistent system service the way Cassandra/Kafka are, and in
practice it was killed by an external signal within an hour of starting it.
Don't rely on it being up unless something is actively supervising it.

Schema (keyspace `market`):
    ohlc_bars(ticker text, d date, o,h,l,c,v double, PRIMARY KEY (ticker, d))
"""

from __future__ import annotations

import json
import os
import sys
import warnings
from datetime import date, timedelta

import pandas as pd
# Hard dependencies — no try/except around these imports. If either package is
# missing, the module must fail at import time, not degrade at call time.
from cassandra.cluster import Cluster
from cassandra import ConsistencyLevel
from cassandra.concurrent import execute_concurrent_with_args
from confluent_kafka import Producer

warnings.filterwarnings("ignore")

KEYSPACE = "market"
CDC_TOPIC = "ohlc.cdc"           # mutations -> Kafka -> kafka_cdc_consumer.py
_session = None
_prepared = {}
_producer = None


class MarketStoreUnavailable(RuntimeError):
    """Raised when Cassandra cannot be reached. Cassandra is mandatory for this
    module — callers must handle this explicitly rather than getting silent
    yfinance-direct behavior."""


class CDCPublishError(RuntimeError):
    """Raised when a CDC mutation event cannot be published to Kafka. The
    real-time fan-out (a consumer reading `ohlc.cdc`) is mandatory, so a
    publish failure is not swallowed."""


def _emit_cdc(ticker: str, n_bars: int, latest):
    """Publish a mutation event to Kafka and confirm delivery. Mandatory: any
    failure to construct the producer, enqueue, or deliver the message raises
    CDCPublishError rather than being swallowed, so a dead broker (or no
    consumer running) is visible at write time instead of silently losing
    real-time events.

    Calls flush(), not just poll(0) — a bare poll(0) only serves already-queued
    delivery callbacks and returns immediately, so a short-lived process (any
    one-off script, not just a long-running server) can exit before librdkafka
    has actually sent the message, silently dropping it. flush() blocks until
    the broker has acked or the timeout elapses, and raises on failure."""
    global _producer
    delivery_error = []

    def _on_delivery(err, msg):
        if err is not None:
            delivery_error.append(err)

    try:
        if _producer is None:
            _producer = Producer({"bootstrap.servers": os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")})
        _producer.produce(CDC_TOPIC, key=ticker, value=json.dumps(
            {"op": "upsert", "table": "ohlc_bars", "ticker": ticker,
             "n_bars": int(n_bars), "latest": str(latest)}), callback=_on_delivery)
        remaining = _producer.flush(timeout=10)
    except Exception as e:
        raise CDCPublishError(f"failed to publish CDC event for {ticker}: {e}") from e
    if remaining > 0:
        raise CDCPublishError(f"CDC event for {ticker} not delivered within 10s ({remaining} still queued)")
    if delivery_error:
        raise CDCPublishError(f"CDC event for {ticker} rejected by broker: {delivery_error[0]}")


def _connect():
    """Return a Cassandra session. Mandatory: raises MarketStoreUnavailable
    instead of returning None, so callers can't accidentally treat a down
    Cassandra node as "no cache, proceed with plain yfinance"."""
    global _session
    if _session is not None:
        return _session
    try:
        cluster = Cluster(["127.0.0.1"], port=9042, connect_timeout=4)
        s = cluster.connect()
        s.execute(
            "CREATE KEYSPACE IF NOT EXISTS %s WITH replication="
            "{'class':'SimpleStrategy','replication_factor':1}" % KEYSPACE)
        s.set_keyspace(KEYSPACE)
        # tunable consistency (blueprint): single node -> LOCAL_ONE; raise to
        # LOCAL_QUORUM when running RF>=3 across a cluster.
        s.default_consistency_level = ConsistencyLevel.LOCAL_ONE
        s.execute("""CREATE TABLE IF NOT EXISTS ohlc_bars(
            ticker text, d date, o double, h double, l double, c double, v double,
            PRIMARY KEY (ticker, d))""")
        _session = s
        return s
    except Exception as e:
        raise MarketStoreUnavailable(
            f"Cassandra is required by market_store.py but is unreachable: {e}"
        ) from e


def _prep(s, key, cql):
    if key not in _prepared:
        _prepared[key] = s.prepare(cql)
    return _prepared[key]


def coverage(ticker: str):
    """(min_date, max_date, n_bars) held in Cassandra for a ticker, or None.
    Raises MarketStoreUnavailable if Cassandra itself is unreachable — that is
    a different condition from "ticker not yet cached" (which returns None)."""
    s = _connect()
    row = s.execute(_prep(s, "cov",
        "SELECT MIN(d) AS mn, MAX(d) AS mx, COUNT(*) AS n FROM ohlc_bars WHERE ticker=?"),
        (ticker,)).one()
    if not row or not row.n or row.mn is None:
        return None
    mn = row.mn.date() if hasattr(row.mn, "date") else row.mn
    mx = row.mx.date() if hasattr(row.mx, "date") else row.mx
    return mn, mx, row.n


def put_ohlc(ticker: str, df: pd.DataFrame):
    """Write bars for a ticker and publish the CDC mutation event. Both the
    Cassandra write and the Kafka publish are mandatory — a failure in either
    raises rather than silently dropping the write or the real-time event."""
    if df is None or df.empty:
        return
    s = _connect()
    ins = _prep(s, "ins",
        "INSERT INTO ohlc_bars(ticker,d,o,h,l,c,v) VALUES (?,?,?,?,?,?,?)")
    args = []
    for idx, r in df.iterrows():
        d = idx.date() if hasattr(idx, "date") else idx
        args.append((ticker, d, float(r.get("Open", r.get("Close"))),
                     float(r.get("High", r.get("Close"))), float(r.get("Low", r.get("Close"))),
                     float(r["Close"]), float(r.get("Volume", 0) or 0)))
    execute_concurrent_with_args(s, ins, args, concurrency=64)
    if args:                                  # emit CDC mutation event (mandatory)
        _emit_cdc(ticker, len(args), df.index.max().date() if hasattr(df.index.max(), "date") else None)


def get_ohlc(ticker: str) -> pd.DataFrame:
    s = _connect()
    rows = s.execute(_prep(s, "sel",
        "SELECT d,o,h,l,c,v FROM ohlc_bars WHERE ticker=?"), (ticker,))
    recs = [((r.d.date() if hasattr(r.d, "date") else r.d), r.o, r.h, r.l, r.c, r.v)
            for r in rows]
    if not recs:
        return pd.DataFrame()
    df = pd.DataFrame(recs, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    df["Date"] = pd.to_datetime(df["Date"])
    return df.set_index("Date").sort_index()


def cached_download(tickers, years: int = 5, refresh_days: int = 3) -> dict:
    """OHLC for tickers: served from Cassandra when fresh, else downloaded and
    written back. Returns {ticker: DataFrame}. Cassandra is mandatory — this
    raises MarketStoreUnavailable immediately (before touching yfinance at all)
    if the cluster can't be reached, instead of quietly reverting to
    uncached/unmonitored direct downloads."""
    from apiclient import yf_download   # governed, deduped, chunked, rate-limited
    out, misses = {}, []
    fresh_cut = date.today() - timedelta(days=refresh_days + 4)   # allow weekend/holiday gap
    # require coverage back to ~years, minus a 30d buffer (a "5y" yfinance pull
    # starts ~exactly 5y ago, so don't demand more history than it returns).
    need_start = date.today() - timedelta(days=int(years * 365) - 30)

    _connect()  # raises MarketStoreUnavailable up front if Cassandra is down
    stale = []                    # cached with enough history but missing recent days
    for t in tickers:
        cov = coverage(t)
        if cov and cov[0] <= need_start and cov[1] >= fresh_cut:
            out[t] = get_ohlc(t)                       # fresh — serve from cache
        elif cov and cov[0] <= need_start:
            stale.append((t, cov[1]))                  # incremental: only fetch cov[1]..today
        else:
            misses.append(t)                           # not cached / too little history — full pull

    # ── incremental delta fetch (only the new bars since last cached date) ──
    if stale:
        print(f"  [market_store] {len(stale)} stale -> fetching only new bars since last cache…",
              file=sys.stderr, flush=True)
        for t, last in stale:
            new = yf_download([t], start=str(last + timedelta(days=1))).get(t)
            if new is not None and not new.empty:
                put_ohlc(t, new)                 # upsert (PK = ticker,d) — idempotent
            out[t] = get_ohlc(t)

    if misses:
        print(f"  [market_store] {len(out)} from Cassandra, downloading {len(misses)} misses…",
              file=sys.stderr, flush=True)
        for t, df in yf_download(misses, period=f"{years}y").items():
            out[t] = df
            put_ohlc(t, df)
    elif not stale:
        print(f"  [market_store] all {len(out)} tickers served from Cassandra (no network)",
              file=sys.stderr, flush=True)
    return out


if __name__ == "__main__":
    import time
    tk = sys.argv[1:] or ["AAPL", "MSFT", "NVDA"]
    t0 = time.time(); a = cached_download(tk, years=5); t1 = time.time()
    print(f"pass 1: {len(a)} tickers in {t1-t0:.1f}s")
    t0 = time.time(); b = cached_download(tk, years=5); t1 = time.time()
    print(f"pass 2 (cached): {len(b)} tickers in {t1-t0:.1f}s")
    for t, df in list(b.items())[:1]:
        print(f"  {t}: {len(df)} bars {df.index.min().date()}..{df.index.max().date()}")
