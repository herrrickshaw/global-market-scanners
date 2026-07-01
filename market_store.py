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

Falls back cleanly to plain yfinance if Cassandra isn't reachable, so nothing
breaks when the node is down.

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

warnings.filterwarnings("ignore")

KEYSPACE = "market"
CDC_TOPIC = "ohlc.cdc"           # blueprint: mutations -> Kafka -> Flink
_session = None
_prepared = {}
_producer = None


def _emit_cdc(ticker: str, n_bars: int, latest):
    """Application-level Change Data Capture: publish a mutation event to Kafka on
    write, mirroring the blueprint's Cassandra->CDC->Kafka dataflow without the
    commit-log CDC machinery. Opt-in via MARKET_STORE_CDC=1; best-effort."""
    global _producer
    if os.environ.get("MARKET_STORE_CDC") != "1":
        return
    try:
        if _producer is None:
            from confluent_kafka import Producer
            _producer = Producer({"bootstrap.servers": "localhost:9092"})
        _producer.produce(CDC_TOPIC, key=ticker, value=json.dumps(
            {"op": "upsert", "table": "ohlc_bars", "ticker": ticker,
             "n_bars": int(n_bars), "latest": str(latest)}))
        _producer.poll(0)
    except Exception:
        pass  # CDC is a side-channel; never block the write


def _connect():
    """Return a Cassandra session, or None if unreachable (caller falls back)."""
    global _session
    if _session is not None:
        return _session
    try:
        from cassandra.cluster import Cluster
        cluster = Cluster(["127.0.0.1"], port=9042, connect_timeout=4)
        s = cluster.connect()
        s.execute(
            "CREATE KEYSPACE IF NOT EXISTS %s WITH replication="
            "{'class':'SimpleStrategy','replication_factor':1}" % KEYSPACE)
        s.set_keyspace(KEYSPACE)
        # tunable consistency (blueprint): single node -> LOCAL_ONE; raise to
        # LOCAL_QUORUM when running RF>=3 across a cluster.
        try:
            from cassandra import ConsistencyLevel
            s.default_consistency_level = ConsistencyLevel.LOCAL_ONE
        except Exception:
            pass
        s.execute("""CREATE TABLE IF NOT EXISTS ohlc_bars(
            ticker text, d date, o double, h double, l double, c double, v double,
            PRIMARY KEY (ticker, d))""")
        _session = s
        return s
    except Exception as e:
        print(f"  [market_store] Cassandra unavailable ({e}); using yfinance direct",
              file=sys.stderr)
        return None


def _prep(s, key, cql):
    if key not in _prepared:
        _prepared[key] = s.prepare(cql)
    return _prepared[key]


def coverage(ticker: str):
    """(min_date, max_date, n_bars) held in Cassandra for a ticker, or None."""
    s = _connect()
    if not s:
        return None
    row = s.execute(_prep(s, "cov",
        "SELECT MIN(d) AS mn, MAX(d) AS mx, COUNT(*) AS n FROM ohlc_bars WHERE ticker=?"),
        (ticker,)).one()
    if not row or not row.n or row.mn is None:
        return None
    mn = row.mn.date() if hasattr(row.mn, "date") else row.mn
    mx = row.mx.date() if hasattr(row.mx, "date") else row.mx
    return mn, mx, row.n


def put_ohlc(ticker: str, df: pd.DataFrame):
    s = _connect()
    if not s or df is None or df.empty:
        return
    ins = _prep(s, "ins",
        "INSERT INTO ohlc_bars(ticker,d,o,h,l,c,v) VALUES (?,?,?,?,?,?,?)")
    from cassandra.concurrent import execute_concurrent_with_args
    args = []
    for idx, r in df.iterrows():
        d = idx.date() if hasattr(idx, "date") else idx
        args.append((ticker, d, float(r.get("Open", r.get("Close"))),
                     float(r.get("High", r.get("Close"))), float(r.get("Low", r.get("Close"))),
                     float(r["Close"]), float(r.get("Volume", 0) or 0)))
    execute_concurrent_with_args(s, ins, args, concurrency=64)
    if args:                                  # emit CDC mutation event (opt-in)
        _emit_cdc(ticker, len(args), df.index.max().date() if hasattr(df.index.max(), "date") else None)


def get_ohlc(ticker: str) -> pd.DataFrame:
    s = _connect()
    if not s:
        return pd.DataFrame()
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
    written back. Returns {ticker: DataFrame}. Falls back to pure yfinance if
    Cassandra is down."""
    import yfinance as yf
    out, misses = {}, []
    fresh_cut = date.today() - timedelta(days=refresh_days + 4)   # allow weekend/holiday gap
    # require coverage back to ~years, minus a 30d buffer (a "5y" yfinance pull
    # starts ~exactly 5y ago, so don't demand more history than it returns).
    need_start = date.today() - timedelta(days=int(years * 365) - 30)

    s = _connect()
    for t in tickers:
        cov = coverage(t) if s else None
        if cov and cov[0] <= need_start and cov[1] >= fresh_cut:
            out[t] = get_ohlc(t)
        else:
            misses.append(t)

    if misses:
        print(f"  [market_store] {len(out)} from Cassandra, downloading {len(misses)} misses…",
              file=sys.stderr, flush=True)
        for i in range(0, len(misses), 250):
            batch = misses[i:i + 250]
            data = yf.download(batch, period=f"{years}y", auto_adjust=True,
                               progress=False, group_by="ticker", threads=True)
            for t in batch:
                try:
                    df = data[t] if isinstance(data.columns, pd.MultiIndex) else data
                    df = df.dropna(how="all")
                    if df is not None and not df.empty:
                        out[t] = df
                        put_ohlc(t, df)
                except Exception:
                    continue
    else:
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
