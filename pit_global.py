#!/usr/bin/env python3
"""
pit_global.py
-------------
pit_backtest.py is rigorously point-in-time but US-only (it needs SEC EDGAR
filed-dates). The other 18 markets had *no* lookahead-free backtest at all — a
credibility gap, since "point-in-time" is a headline claim. This extends the
backtest to every market using only what is genuinely point-in-time outside the
US: **prices**. It runs the monthly-rebalanced Darvas-breakout arm vs an
equal-weight benchmark on the local cleaned_long parquets, net of the market's
round-trip cost (from apply_costs).

Honesty about the fundamental gate: outside the US we only have a *current*
fundamentals snapshot (fundamentals_cache.db), not a filed-date history, so it
CANNOT be applied point-in-time. When --overlay is passed we additionally report
the technical arm restricted to names that *currently* pass a DVM durability
gate, and label it loudly as a STATIC (look-ahead) overlay — a rough "does
quality help" read, not a clean backtest. The pure Darvas arm remains fully PIT.

Usage:
  python pit_global.py --market JP --years 5
  python pit_global.py --market KR --overlay
  python pit_global.py --all                 # every market with a parquet
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

from apply_costs import COSTS_PCT
from pit_backtest import darvas_breakout, metrics

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = os.path.expanduser("~/Downloads/code/python_files/cache_seed")

# map cleaned_long market code -> apply_costs cost bucket (fallback 0.30%)
COST_BUCKET = {"US": "US", "JP": "Japan", "KR": "Korea", "DE": "Europe", "EU": "Europe",
               "FI": "Europe", "DK": "Europe", "CH": "Europe", "SE": "Europe", "UK": "Europe"}


def market_cost(market: str) -> float:
    return COSTS_PCT.get(COST_BUCKET.get(market, ""), 0.30)


def load_closes(market: str, years: int) -> dict:
    p = os.path.join(SEED, f"cleaned_long_{market}.parquet")
    if not os.path.exists(p):
        return {}
    df = pd.read_parquet(p)
    df["Date"] = pd.to_datetime(df["Date"])
    cutoff = df["Date"].max() - pd.Timedelta(days=365 * years + 30)
    df = df[df["Date"] >= cutoff]
    out = {}
    for sym, g in df.groupby("Symbol"):
        s = g.set_index("Date")["Close"].astype(float).dropna().sort_index()
        if len(s) > 80:
            out[sym] = s
    return out


def durable_names(market: str) -> set:
    """Names that CURRENTLY pass a simple durability gate (STATIC — not PIT)."""
    cache = os.path.join(HERE, "fundamentals_cache.db")
    if not os.path.exists(cache):
        return set()
    con = sqlite3.connect(cache)
    try:
        f = pd.read_sql("SELECT ticker, roe, de FROM fund WHERE market=?", con, params=(market,))
    finally:
        con.close()
    f["roe"] = pd.to_numeric(f["roe"], errors="coerce")
    f["de"] = pd.to_numeric(f["de"], errors="coerce")
    ok = f[(f["roe"] > 12) & (f["de"] < 1.5)]
    return set(ok["ticker"].astype(str).str.split(".").str[0].str.upper())


def run_market(market: str, years: int, overlay: bool) -> pd.DataFrame:
    closes = load_closes(market, years)
    if len(closes) < 20:
        print(f"  [skip] {market}: only {len(closes)} usable series", file=sys.stderr)
        return pd.DataFrame()
    cost = market_cost(market)
    all_idx = sorted(set().union(*[s.index for s in closes.values()]))
    rebal = pd.Series(1, index=pd.DatetimeIndex(all_idx)).resample("MS").first().index
    rebal = [d for d in rebal if d >= pd.Timestamp(all_idx[61])]

    durable = durable_names(market) if overlay else set()
    arms = {"A_darvas": [], "BENCH_all": []}
    if overlay:
        arms["D_darvas_durable"] = []

    for i in range(len(rebal) - 1):
        t0, t1 = rebal[i], rebal[i + 1]
        held, breakout, dur_breakout, fwd_by = [], [], [], {}
        for tkr, s in closes.items():
            idx = s.index
            pre0 = idx[idx <= t0]; pre1 = idx[idx <= t1]
            if len(pre0) == 0 or len(pre1) == 0:
                continue
            e0, e1 = pre0[-1], pre1[-1]
            if e0 >= e1:
                continue
            fwd = (s.loc[e1] / s.loc[e0] - 1) * 100
            if not np.isfinite(fwd):
                continue
            fwd_by[tkr] = fwd
            held.append(fwd)
            if darvas_breakout(s, e0):
                breakout.append(tkr)
                if tkr.split(".")[0].upper() in durable:
                    dur_breakout.append(tkr)
        if held:
            arms["BENCH_all"].append(float(np.mean(held)) - cost)
        if breakout:
            arms["A_darvas"].append(float(np.mean([fwd_by[t] for t in breakout])) - cost)
        if overlay and dur_breakout:
            arms["D_darvas_durable"].append(
                float(np.mean([fwd_by[t] for t in dur_breakout])) - cost)

    rows = []
    for arm, rets in arms.items():
        m = metrics(rets); m["arm"] = arm; m["market"] = market
        rows.append(m)
    cols = ["market", "arm", "n_months", "avg_mth%", "ann_return%", "hit_rate%", "sharpe", "max_dd%"]
    return pd.DataFrame(rows)[[c for c in cols if c in rows[0]]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="JP")
    ap.add_argument("--all", action="store_true", help="every market with a parquet")
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--overlay", action="store_true",
                    help="also report a STATIC current-durability overlay (NOT point-in-time)")
    ap.add_argument("--db", default="pit_global.db")
    args = ap.parse_args()

    markets = ([f.split("cleaned_long_")[1].split(".")[0]
                for f in sorted(os.listdir(SEED)) if f.startswith("cleaned_long_")]
               if args.all else [args.market])
    out = []
    for m in markets:
        r = run_market(m, args.years, args.overlay)
        if not r.empty:
            out.append(r)
            print(f"\n=== {m} — Darvas breakout, monthly, net of {market_cost(m):.2f}% "
                  f"(prices = point-in-time) ===")
            print(r.to_string(index=False))
    if out:
        allres = pd.concat(out, ignore_index=True)
        con = sqlite3.connect(args.db); con.execute("PRAGMA journal_mode=DELETE;")
        allres.to_sql("global_arm_summary", con, if_exists="replace", index=False)
        con.commit(); con.close()
        print(f"\nsaved {len(allres)} rows -> {args.db}", file=sys.stderr)
    if args.overlay:
        print("\nNOTE: D_darvas_durable uses CURRENT fundamentals as a static gate — it is "
              "look-ahead (not point-in-time). Only the A_darvas and BENCH arms are clean PIT.")


if __name__ == "__main__":
    main()
