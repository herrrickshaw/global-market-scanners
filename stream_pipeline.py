#!/usr/bin/env python3
"""
stream_pipeline.py
------------------
Kafka work-queue for distributed scanning. A producer publishes the ticker
universe to a topic; any number of consumer workers (same consumer group) pull
partitions in parallel, compute a screen signal per ticker from the Cassandra
OHLC cache, and emit results to a results topic. Scale throughput by launching
more `consume` workers — Kafka spreads the partitions across them.

Requires the running local broker (localhost:9092) and confluent-kafka.

    # terminal 1..N  (workers — the more you run, the faster)
    python stream_pipeline.py consume --group scan1
    # terminal 0     (feed the universe)
    python stream_pipeline.py produce --market US --limit 500
    # quick self-test (produce + consume a handful in one process)
    python stream_pipeline.py demo
"""

import argparse
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

TOPIC_WORK = "scan.tickers"
TOPIC_RESULT = "scan.signals"
BROKER = "localhost:9092"

SCAN_GLOBS = {
    "US": "data/us_full_scan/**/us_full_scan_*.xlsx",
    "India": "data/**/indian_full_scan_*.xlsx",
}


def _universe(market, limit):
    hits = sorted(glob.glob(os.path.expanduser(f"~/Downloads/{SCAN_GLOBS[market]}"),
                            recursive=True))
    if not hits:
        return []
    a = pd.ExcelFile(hits[-1]).parse("All_Stocks")
    if market == "India":
        syms = (a["Symbol"].astype(str) + a["Suffix"].astype(str)).tolist()
    else:
        syms = a["Symbol"].astype(str).tolist()
    syms = [s for s in syms if s and s != "nan"]
    return syms[:limit] if limit else syms


def _signal(ticker):
    """Compute a quick screen signal for a ticker from the Cassandra OHLC cache."""
    from market_store import get_ohlc, cached_download
    df = get_ohlc(ticker)
    if df.empty:
        df = cached_download([ticker]).get(ticker, pd.DataFrame())
    if df is None or df.empty or len(df) < 60:
        return {"ticker": ticker, "signal": "NO_DATA"}
    close = df["Close"].astype(float)
    delta = close.diff()
    rsi = 100 - 100 / (1 + delta.clip(lower=0).rolling(14).mean() /
                       (-delta.clip(upper=0)).rolling(14).mean().replace(0, np.nan))
    hi60 = close.rolling(60).max()
    last = close.iloc[-1]
    sig = "BREAKOUT" if last >= hi60.iloc[-1] else ("OVERSOLD" if rsi.iloc[-1] < 30 else "NEUTRAL")
    return {"ticker": ticker, "signal": sig, "rsi": round(float(rsi.iloc[-1]), 1),
            "close": round(float(last), 2)}


def produce(market, limit):
    from confluent_kafka import Producer
    p = Producer({"bootstrap.servers": BROKER})
    tickers = _universe(market, limit)
    for t in tickers:
        p.produce(TOPIC_WORK, key=t, value=t)
    p.flush()
    print(f"produced {len(tickers)} {market} tickers -> {TOPIC_WORK}", file=sys.stderr)


def consume(group, max_idle=8):
    from confluent_kafka import Consumer, Producer
    c = Consumer({"bootstrap.servers": BROKER, "group.id": group,
                  "auto.offset.reset": "earliest"})
    c.subscribe([TOPIC_WORK])
    p = Producer({"bootstrap.servers": BROKER})
    n, idle = 0, 0
    print(f"[worker {group}] consuming {TOPIC_WORK}…", file=sys.stderr, flush=True)
    try:
        while idle < max_idle:
            msg = c.poll(1.0)
            if msg is None:
                idle += 1
                continue
            if msg.error():
                continue
            idle = 0
            res = _signal(msg.value().decode())
            p.produce(TOPIC_RESULT, key=res["ticker"], value=json.dumps(res))
            n += 1
            if n % 25 == 0:
                p.flush()
                print(f"[worker {group}] processed {n}", file=sys.stderr, flush=True)
    finally:
        p.flush(); c.close()
    print(f"[worker {group}] done, {n} signals -> {TOPIC_RESULT}", file=sys.stderr)


def demo():
    """Single-process produce+consume round-trip to prove the pipeline works."""
    from confluent_kafka import Producer, Consumer
    tickers = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"]
    Producer({"bootstrap.servers": BROKER}).__class__  # ensure import ok
    p = Producer({"bootstrap.servers": BROKER})
    for t in tickers:
        p.produce(TOPIC_WORK, key=t, value=t)
    p.flush()
    print(f"produced {len(tickers)} -> {TOPIC_WORK}", file=sys.stderr)
    c = Consumer({"bootstrap.servers": BROKER, "group.id": "demo",
                  "auto.offset.reset": "earliest"})
    c.subscribe([TOPIC_WORK])
    got, idle = 0, 0
    while got < len(tickers) and idle < 10:
        msg = c.poll(1.0)
        if msg is None:
            idle += 1; continue
        if msg.error():
            continue
        print("  signal:", _signal(msg.value().decode()))
        got += 1
    c.close()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("produce"); pr.add_argument("--market", default="US"); pr.add_argument("--limit", type=int)
    co = sub.add_parser("consume"); co.add_argument("--group", default="scan1")
    sub.add_parser("demo")
    a = ap.parse_args()
    if a.cmd == "produce":
        produce(a.market, a.limit)
    elif a.cmd == "consume":
        consume(a.group)
    else:
        demo()


if __name__ == "__main__":
    main()
