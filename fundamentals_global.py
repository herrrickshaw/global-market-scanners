#!/usr/bin/env python3
"""
fundamentals_global.py
----------------------
Global FUNDAMENTAL screener — sources the Trendlyne-style fundamental metrics
(P/E, P/B, ROE, ROA, D/E, revenue/earnings growth, operating margin, dividend
yield, market cap) via yfinance for the liquid subset of every market with local
OHLC, then runs the public fundamental screens across markets.

Non-US fundamentals come from yfinance get_info (global coverage); US could also
use EDGAR (pit_fundamentals) but yfinance keeps it uniform across all 19 markets.
Fundamentals are cached to fundamentals_cache.db (resumable, low-concurrency +
retry to survive Yahoo rate limits). Liquidity picks the per-market subset.

Screens (Trendlyne / screener.in public fundamental types):
  high_roe_low_de   ROE>15% & D/E<1
  growth_roe_lowpe  ROE>15% & rev_growth>10% & 0<P/E<30   ("High Growth High RoE Low PE")
  value             P/B<3 & 0<P/E<15 & D/E<1
  dividend          dividend_yield>3% & payout sustainable (D/E<1.5)

Usage:
  python fundamentals_global.py --top 50 --screen high_roe_low_de
  python fundamentals_global.py --markets US JP DE --top 80 --screen growth_roe_lowpe
"""

from __future__ import annotations

import argparse
import glob
import os
import random
import sqlite3
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

SEED = os.path.expanduser("~/Downloads/code/python_files/cache_seed")
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fundamentals_cache.db")


def _cache():
    c = sqlite3.connect(CACHE); c.execute("PRAGMA journal_mode=DELETE;")
    c.execute("""CREATE TABLE IF NOT EXISTS fund(ticker TEXT PRIMARY KEY, market TEXT,
        pe REAL, pb REAL, roe REAL, roa REAL, de REAL, rev_growth REAL,
        earn_growth REAL, op_margin REAL, div_yield REAL, mktcap REAL, sector TEXT)""")
    c.commit(); return c


def _norm_de(x):
    if x is None:
        return None
    return x / 100 if x > 10 else x            # yfinance gives D/E as % sometimes (memory note)


def fetch_one(ticker, retries=4):
    import yfinance as yf
    for a in range(retries):
        try:
            i = yf.Ticker(ticker).get_info()
            if not i or "marketCap" not in i:
                raise ValueError("empty")
            roe = i.get("returnOnEquity"); roa = i.get("returnOnAssets")
            return ticker, dict(
                pe=i.get("trailingPE"), pb=i.get("priceToBook"),
                roe=(roe * 100 if roe is not None else None),
                roa=(roa * 100 if roa is not None else None),
                de=_norm_de(i.get("debtToEquity")),
                rev_growth=(i.get("revenueGrowth") or 0) * 100 if i.get("revenueGrowth") is not None else None,
                earn_growth=(i.get("earningsGrowth") or 0) * 100 if i.get("earningsGrowth") is not None else None,
                op_margin=(i.get("operatingMargins") or 0) * 100 if i.get("operatingMargins") is not None else None,
                div_yield=(i.get("dividendYield") or 0) if i.get("dividendYield") is not None else None,
                mktcap=i.get("marketCap"), sector=i.get("sector"))
        except Exception:
            time.sleep(1.5 * (a + 1) + random.uniform(0, 1))
    return ticker, None


def liquid_subset(market, top):
    df = pd.read_parquet(os.path.join(SEED, f"cleaned_long_{market}.parquet"))
    dv = (df.assign(dv=df["Close"] * df["Volume"]).groupby("Symbol")["dv"].median()
          .sort_values(ascending=False))
    return dv.head(top).index.tolist()


SCREENS = {
    "high_roe_low_de":  lambda r: (r["roe"] or 0) > 15 and r["de"] is not None and r["de"] < 1,
    "growth_roe_lowpe": lambda r: (r["roe"] or 0) > 15 and (r["rev_growth"] or 0) > 10
                                  and r["pe"] is not None and 0 < r["pe"] < 30,
    "value":            lambda r: r["pb"] is not None and r["pb"] < 3 and r["pe"] is not None
                                  and 0 < r["pe"] < 15 and (r["de"] is None or r["de"] < 1),
    "dividend":         lambda r: (r["div_yield"] or 0) > 3 and (r["de"] is None or r["de"] < 1.5),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", nargs="*", default=None)
    ap.add_argument("--top", type=int, default=50, help="liquid tickers per market to source")
    ap.add_argument("--screen", choices=list(SCREENS), default="high_roe_low_de")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--db", default="fund_screen.db")
    args = ap.parse_args()

    all_m = sorted(os.path.basename(p).replace("cleaned_long_", "").replace(".parquet", "")
                   for p in glob.glob(os.path.join(SEED, "cleaned_long_*.parquet")))
    markets = args.markets or all_m
    cc = _cache()

    # assemble target tickers (liquid subset per market), skip cached
    targets = []
    for m in markets:
        for t in liquid_subset(m, args.top):
            targets.append((t, m))
    have = set(r[0] for r in cc.execute("SELECT ticker FROM fund").fetchall())
    todo = [(t, m) for t, m in targets if t not in have]
    print(f"{len(targets)} liquid tickers across {len(markets)} markets; "
          f"cached={len(targets)-len(todo)} to_fetch={len(todo)}", file=sys.stderr, flush=True)

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_one, t): (t, m) for t, m in todo}
        batch = []
        for fut in as_completed(futs):
            t, m = futs[fut]
            _, data = fut.result(); done += 1
            if data:
                batch.append((t, m, data["pe"], data["pb"], data["roe"], data["roa"], data["de"],
                              data["rev_growth"], data["earn_growth"], data["op_margin"],
                              data["div_yield"], data["mktcap"], data["sector"]))
            if len(batch) >= 40 or done == len(todo):
                cc.executemany("INSERT OR REPLACE INTO fund VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
                cc.commit(); batch = []
            if done % 100 == 0:
                print(f"  fetched {done}/{len(todo)}", file=sys.stderr, flush=True)

    # run the screen per market from the cache
    tset = set(t for t, _ in targets)
    rows = [dict(ticker=r[0], market=r[1], pe=r[2], pb=r[3], roe=r[4], roa=r[5], de=r[6],
                 rev_growth=r[7], earn_growth=r[8], op_margin=r[9], div_yield=r[10],
                 mktcap=r[11], sector=r[12])
            for r in cc.execute("SELECT * FROM fund").fetchall() if r[0] in tset]
    cc.close()

    out = sqlite3.connect(args.db); out.execute("DROP TABLE IF EXISTS fund_hits")
    out.execute("""CREATE TABLE fund_hits(market TEXT, ticker TEXT, roe REAL, de REAL,
        pe REAL, pb REAL, rev_growth REAL, div_yield REAL, sector TEXT)""")
    from collections import Counter
    per_mkt = Counter(); hits_mkt = Counter(); sample = {}
    for r in rows:
        per_mkt[r["market"]] += 1
        if SCREENS[args.screen](r):
            hits_mkt[r["market"]] += 1
            out.execute("INSERT INTO fund_hits VALUES (?,?,?,?,?,?,?,?,?)",
                        (r["market"], r["ticker"], r["roe"], r["de"], r["pe"], r["pb"],
                         r["rev_growth"], r["div_yield"], r["sector"]))
            sample.setdefault(r["market"], []).append(r)
    out.commit(); out.close()

    print(f"\n=== GLOBAL FUNDAMENTAL SCREEN: {args.screen} ===")
    print(f"  {'market':8}{'w/funds':>9}{'hits':>7}")
    for m in markets:
        if per_mkt.get(m):
            print(f"  {m:8}{per_mkt[m]:>9}{hits_mkt.get(m,0):>7}")
    print(f"  {'TOTAL':8}{sum(per_mkt.values()):>9}{sum(hits_mkt.values()):>7}")
    print("\n  sample hits:")
    for m, rs in list(sample.items())[:6]:
        for r in rs[:2]:
            print(f"   {m} {r['ticker']:12} ROE={r['roe']} D/E={r['de']} PE={r['pe']} "
                  f"revG={r['rev_growth']} [{r['sector']}]")


if __name__ == "__main__":
    main()
