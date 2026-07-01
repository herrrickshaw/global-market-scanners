#!/usr/bin/env python3
"""
dvm_composite.py
----------------
The capstone: a single GLOBAL DVM composite ranking (Trendlyne GGG/GGB/BBG) that
fuses the two halves built earlier —
  Momentum   (technical, all markets)  from local OHLC (cache_seed)
  Durability (fundamental)             from ROE/D-E/growth/margin (fundamentals_cache.db)
  Valuation  (fundamental)             from earnings-yield & P/B cross-sectional rank
— for every stock that has both fundamentals and prices, across all 19 markets.

Classification: D/V/M each Good(>=50)/Bad(<50) -> GGG Strong Performer, GGB Value
Under Radar, BBG Momentum Trap, etc. Ranked by (D+V+M)/3.

Uses fundamentals_cache.db (populate via fundamentals_global.py) + cleaned_long
parquets. Self-contained, no network.

Usage:
  python dvm_composite.py                 # global GGG ranking
  python dvm_composite.py --code GGB      # only Value-Under-Radar names
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

SEED = os.path.expanduser("~/Downloads/code/python_files/cache_seed")
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fundamentals_cache.db")
LABELS = {"GGG": "Strong Performer", "GGB": "Value Under Radar", "GBG": "Expensive Durable Mover",
          "GBB": "Expensive Quality", "BGG": "Cheap Turnaround Mover", "BGB": "Deep Value / Watch",
          "BBG": "Momentum Trap", "BBB": "Weak / Avoid"}


def momentum(c: pd.Series) -> float:
    if len(c) < 200:
        return np.nan
    d = c.diff()
    rsi = (100 - 100 / (1 + d.clip(lower=0).rolling(14).mean() /
                        (-d.clip(upper=0)).rolling(14).mean().replace(0, np.nan))).iloc[-1]
    macd_h = (c.ewm(span=12).mean() - c.ewm(span=26).mean())
    macd_h = (macd_h - macd_h.ewm(span=9).mean()).iloc[-1]
    dma50 = c.rolling(50).mean().iloc[-1]; dma200 = c.rolling(200).mean().iloc[-1]
    px = c.iloc[-1]; hi52 = c.rolling(252, min_periods=150).max().iloc[-1]
    subs = [min(100, max(0, rsi if rsi <= 70 else 70 - (rsi - 70) * 2)),
            100 if macd_h > 0 else 25,
            100 if (px > dma50 > dma200) else (60 if px > dma200 else 20),
            min(100, max(0, 100 + (px / hi52 - 1) * 300))]
    return float(np.mean(subs))


def durability(r) -> float:
    subs = []
    roe = r["roe"]; de = r["de"]; rg = r["rev_growth"]; om = r["op_margin"]; eg = r["earn_growth"]
    if pd.notna(roe):
        subs.append(90 if roe > 20 else 75 if roe > 15 else 55 if roe > 10 else 35 if roe > 0 else 15)
    if pd.notna(de):
        subs.append(90 if de < 0.5 else 70 if de < 1 else 45 if de < 2 else 20)
    if pd.notna(rg):
        subs.append(85 if rg > 15 else 60 if rg > 5 else 40 if rg > 0 else 20)
    if pd.notna(om):
        subs.append(85 if om > 20 else 60 if om > 10 else 40 if om > 0 else 20)
    if pd.notna(eg):
        subs.append(75 if eg > 0 else 35)
    return float(np.mean(subs)) if subs else np.nan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default=None, help="filter to one composite code e.g. GGG")
    ap.add_argument("--db", default="dvm_composite.db")
    args = ap.parse_args()

    if not os.path.exists(CACHE):
        print("no fundamentals_cache.db — run fundamentals_global.py first", file=sys.stderr); return
    fund = pd.read_sql("SELECT * FROM fund", sqlite3.connect(CACHE))
    for col in ["pe", "pb", "roe", "roa", "de", "rev_growth", "earn_growth",
                "op_margin", "div_yield", "mktcap"]:
        fund[col] = pd.to_numeric(fund[col], errors="coerce")
    print(f"{len(fund)} stocks with fundamentals across {fund['market'].nunique()} markets",
          file=sys.stderr)

    # momentum per stock from local OHLC (load each market's parquet once)
    rows = []
    for mkt, grp in fund.groupby("market"):
        p = os.path.join(SEED, f"cleaned_long_{mkt}.parquet")
        if not os.path.exists(p):
            continue
        px = pd.read_parquet(p).sort_values("Date")
        px = px[px["Symbol"].isin(set(grp["ticker"]))]
        closes = {s: g.set_index("Date")["Close"].astype(float) for s, g in px.groupby("Symbol")}
        for _, r in grp.iterrows():
            c = closes.get(r["ticker"])
            M = momentum(c) if c is not None else np.nan
            D = durability(r)
            if pd.isna(M) or pd.isna(D):
                continue
            ey = (1.0 / r["pe"]) if (r["pe"] and r["pe"] > 0) else None
            rows.append({"market": mkt, "ticker": r["ticker"], "M": M, "D": D,
                         "_ey": ey, "_pb": r["pb"], "roe": r["roe"], "de": r["de"],
                         "pe": r["pe"], "sector": r["sector"]})
    df = pd.DataFrame(rows)
    if df.empty:
        print("no stocks with both momentum + durability", file=sys.stderr); return

    # Valuation = cross-sectional: 60% earnings-yield rank + 40% inverse P/B rank
    ey_rank = df["_ey"].rank(pct=True) * 100
    pb_rank = (1 - df["_pb"].rank(pct=True)) * 100
    df["V"] = (0.6 * ey_rank.fillna(ey_rank.mean()) + 0.4 * pb_rank.fillna(pb_rank.mean())).round(1)
    df["M"] = df["M"].round(1); df["D"] = df["D"].round(1)

    g = lambda x: "G" if x >= 50 else "B"
    df["code"] = [g(d) + g(v) + g(m) for d, v, m in zip(df["D"], df["V"], df["M"])]
    df["label"] = df["code"].map(LABELS)
    df["composite"] = ((df["D"] + df["V"] + df["M"]) / 3).round(1)
    df = df.sort_values("composite", ascending=False)

    out = sqlite3.connect(args.db); df.drop(columns=["_ey", "_pb"]).to_sql("dvm_composite", out, if_exists="replace", index=False); out.close()

    from collections import Counter
    print(f"\n=== GLOBAL DVM COMPOSITE — {len(df)} stocks, all markets ===")
    print("  classification:", dict(Counter(df["code"]).most_common()))
    show = df if not args.code else df[df["code"] == args.code]
    tag = f"code={args.code}" if args.code else "top GGG Strong Performers"
    ggg = show[show["code"] == "GGG"] if not args.code else show
    print(f"\n=== {tag} ({len(ggg)}) — top 20 by composite ===")
    print(f"  {'mkt':4}{'ticker':12}{'D':>6}{'V':>6}{'M':>6}{'comp':>7}  {'code':5} label")
    for _, r in ggg.head(20).iterrows():
        print(f"  {r['market']:4}{r['ticker']:12}{r['D']:>6}{r['V']:>6}{r['M']:>6}"
              f"{r['composite']:>7}  {r['code']:5} {r['label']}")
    print(f"\n  saved to {args.db}")


if __name__ == "__main__":
    main()
