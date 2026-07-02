#!/usr/bin/env python3
"""
sector_rotation.py
------------------
The industry/peer parquet (companies_industry.parquet) was built for labelling
and never used as a *signal*. This turns it into a sector-rotation model: group
every stock by industry (or sector/segment), measure each group's momentum
(median 12-1 month member return, the classic academic definition that skips the
most-recent month), and rank industries strongest-to-weakest. The output is a
rotation shortlist — the industries to overweight, and their leading names.

Pure ranking core (numpy/pandas), so it's unit-testable; prices come from the
local cleaned_long parquets, no network.

Usage:
  python sector_rotation.py --by industry --market US --top 15
  python sector_rotation.py --by sector                     # all markets pooled
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = os.path.expanduser("~/Downloads/code/python_files/cache_seed")
COMPANIES = os.path.join(HERE, "companies_industry.parquet")


# ── pure momentum + ranking core ──────────────────────────────────────────────
def member_momentum(close: pd.Series, lookback: int = 252, skip: int = 21) -> float:
    """12-1 month momentum: return from ~12m ago to ~1m ago (skips last month)."""
    c = close.dropna().astype(float)
    if len(c) < lookback + 1:
        return np.nan
    start = c.iloc[-lookback]
    end = c.iloc[-skip - 1] if len(c) > skip else c.iloc[-1]
    return float(end / start - 1) if start > 0 else np.nan


def rank_groups(members: pd.DataFrame, group_col: str, min_members: int = 3) -> pd.DataFrame:
    """members: rows of {group_col, ticker, momentum}. Returns per-group median
    momentum, breadth (% positive) and member count, ranked descending."""
    g = members.dropna(subset=["momentum"]).groupby(group_col)
    out = g["momentum"].agg(
        mom_median="median",
        mom_mean="mean",
        breadth=lambda s: float((s > 0).mean()),
        n="count",
    ).reset_index()
    out = out[out["n"] >= min_members].copy()
    out["mom_median%"] = (out["mom_median"] * 100).round(2)
    out["mom_mean%"] = (out["mom_mean"] * 100).round(2)
    out["breadth%"] = (out["breadth"] * 100).round(0)
    out["rank"] = out["mom_median"].rank(ascending=False).astype(int)
    return out.sort_values("mom_median", ascending=False)[
        [group_col, "rank", "mom_median%", "mom_mean%", "breadth%", "n"]]


# ── data assembly ─────────────────────────────────────────────────────────────
def _clean_ticker(t: str) -> str:
    """cleaned_long uses bare symbols; companies_industry uses suffixed tickers."""
    return str(t).split(".")[0].upper()


def build_member_table(by: str, market: str | None) -> pd.DataFrame:
    comp = pd.read_parquet(COMPANIES)[["ticker", "country", "sector", "industry", "segment"]]
    comp["key"] = comp["ticker"].map(_clean_ticker)
    markets = [market] if market else [
        f.split("cleaned_long_")[1].split(".")[0]
        for f in os.listdir(SEED) if f.startswith("cleaned_long_")]
    rows = []
    for mkt in markets:
        p = os.path.join(SEED, f"cleaned_long_{mkt}.parquet")
        if not os.path.exists(p):
            continue
        px = pd.read_parquet(p).sort_values("Date")
        for sym, grp in px.groupby("Symbol"):
            mom = member_momentum(grp.set_index("Date")["Close"])
            if np.isnan(mom):
                continue
            rows.append({"market": mkt, "key": _clean_ticker(sym), "ticker": sym, "momentum": mom})
    mem = pd.DataFrame(rows)
    if mem.empty:
        return mem
    mem = mem.merge(comp[["key", by]].drop_duplicates("key"), on="key", how="left")
    mem[by] = mem[by].fillna("Unknown")
    return mem


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--by", choices=["industry", "sector", "segment"], default="industry")
    ap.add_argument("--market", default=None, help="single market code e.g. US (default: all)")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--min-members", type=int, default=3)
    args = ap.parse_args()

    if not os.path.exists(COMPANIES):
        raise SystemExit("no companies_industry.parquet — run build_industry_parquet.py first")
    mem = build_member_table(args.by, args.market)
    if mem.empty:
        raise SystemExit("no member momentum computed (missing price parquets?)")
    ranked = rank_groups(mem, args.by, args.min_members)

    print(f"\n=== SECTOR ROTATION by {args.by} — {args.market or 'all markets'} "
          f"({len(ranked)} groups) ===", file=sys.stderr)
    print(f"  {'#':>3} {args.by:32}{'12-1 mom%':>11}{'breadth%':>10}{'n':>5}")
    for _, r in ranked.head(args.top).iterrows():
        print(f"  {r['rank']:>3} {str(r[args.by])[:32]:32}"
              f"{r['mom_median%']:>11}{r['breadth%']:>10.0f}{int(r['n']):>5}")
    print("\n  Leaders (top group's names):", file=sys.stderr)
    lead = ranked.iloc[0][args.by]
    names = mem[mem[args.by] == lead].sort_values("momentum", ascending=False).head(8)
    for _, r in names.iterrows():
        print(f"    {r['ticker']:14} {r['momentum']*100:6.1f}%")


if __name__ == "__main__":
    main()
