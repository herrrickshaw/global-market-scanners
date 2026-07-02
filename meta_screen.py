#!/usr/bin/env python3
"""
meta_screen.py
--------------
The scanners run independently — Triple-Hit (Darvas+Piotroski+CoffeeCan), the
global DVM composite (GGG), and the ML directional signal each produce their own
list. dvm_composite proved that *fusing* durability+valuation+momentum works;
this generalises that idea one level up and fuses the **screens themselves** into
one 0–100 conviction score, so a name flagged by several methods ranks above a
name flagged by one.

Fusion is a weighted mean over whichever components are present (weights
renormalise when a component is missing), plus a confirmation bonus when a name
clears a hard gate (e.g. it's a Triple-Hit). Pure and unit-testable.

Components (all normalised to 0–100, higher = better):
  durability   D   from dvm_composite
  valuation    V   from dvm_composite
  momentum     M   from dvm_composite
  ml_signal    optional, from an ml scores table/CSV if provided
  triple_hit   hard gate (bonus), from a scanner Triple-Hit list if provided

Usage:
  python meta_screen.py --market US --top 25
  python meta_screen.py --triple-hits triple_hits_US.csv --ml ml_scores.csv
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_WEIGHTS = {"durability": 0.30, "valuation": 0.20, "momentum": 0.25, "ml_signal": 0.25}
GATE_BONUS = 10.0          # points added for clearing a hard gate (capped at 100)


# ── pure fusion core ──────────────────────────────────────────────────────────
def fuse(components: dict, weights: dict = None, gates: dict = None,
         gate_bonus: float = GATE_BONUS) -> float:
    """Weighted mean over present components (None/NaN skipped, weights
    renormalised), plus `gate_bonus` per satisfied gate. Result clamped to [0,100].

    components: {name: score_0_100 or None}
    gates:      {name: bool}     -> each True adds gate_bonus
    """
    weights = weights or DEFAULT_WEIGHTS
    num = den = 0.0
    for name, score in components.items():
        if score is None or (isinstance(score, float) and np.isnan(score)):
            continue
        w = weights.get(name, 0.0)
        if w <= 0:
            continue
        num += w * float(score)
        den += w
    base = (num / den) if den > 0 else 0.0
    bonus = gate_bonus * sum(1 for v in (gates or {}).values() if v)
    return float(min(100.0, max(0.0, base + bonus)))


def rank(df: pd.DataFrame, weights: dict = None) -> pd.DataFrame:
    """Add a `conviction` column and sort. df must have D/V/M columns; optional
    ml_signal column and boolean triple_hit column."""
    weights = weights or DEFAULT_WEIGHTS
    out = df.copy()
    conv = []
    for _, r in out.iterrows():
        comps = {"durability": r.get("D"), "valuation": r.get("V"),
                 "momentum": r.get("M"), "ml_signal": r.get("ml_signal")}
        gates = {"triple_hit": bool(r.get("triple_hit", False))}
        conv.append(fuse(comps, weights, gates))
    out["conviction"] = np.round(conv, 1)
    n_methods = (
        out[["D", "V", "M"]].notna().any(axis=1).astype(int)
        + out.get("ml_signal", pd.Series(np.nan, index=out.index)).notna().astype(int)
        + out.get("triple_hit", pd.Series(False, index=out.index)).astype(bool).astype(int)
    )
    out["n_confirms"] = n_methods
    return out.sort_values("conviction", ascending=False)


# ── data assembly (offline) ───────────────────────────────────────────────────
def load_composite(market: str | None, db: str) -> pd.DataFrame:
    q = "SELECT market, ticker, D, V, M, composite, code FROM dvm_composite"
    params = ()
    if market:
        q += " WHERE market=?"; params = (market,)
    con = sqlite3.connect(db)
    try:
        return pd.read_sql(q, con, params=params)
    finally:
        con.close()


def _merge_optional(df, path, col, keys=("ticker",)):
    if not path or not os.path.exists(path):
        return df
    extra = pd.read_csv(path)
    return df.merge(extra, on=list(keys), how="left")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default=None)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--db", default=os.path.join(HERE, "dvm_composite.db"))
    ap.add_argument("--ml", default=None, help="CSV with ticker,ml_signal (0-100)")
    ap.add_argument("--triple-hits", default=None, help="CSV with ticker column (Triple-Hit names)")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        raise SystemExit("no dvm_composite.db — run dvm_composite.py first")
    df = load_composite(args.market, args.db)
    df = _merge_optional(df, args.ml, "ml_signal")
    if args.triple_hits and os.path.exists(args.triple_hits):
        th = set(pd.read_csv(args.triple_hits)["ticker"].astype(str))
        df["triple_hit"] = df["ticker"].astype(str).isin(th)

    ranked = rank(df)
    print(f"\n=== META-SCREEN conviction — {args.market or 'all markets'} "
          f"({len(ranked)} names) ===", file=sys.stderr)
    print(f"  {'mkt':4}{'ticker':14}{'D':>5}{'V':>5}{'M':>5}"
          f"{'conv':>7}{'#conf':>6}  code")
    for _, r in ranked.head(args.top).iterrows():
        print(f"  {str(r['market']):4}{str(r['ticker']):14}"
              f"{r['D']:>5.0f}{r['V']:>5.0f}{r['M']:>5.0f}"
              f"{r['conviction']:>7.1f}{int(r['n_confirms']):>6}  {r.get('code','')}")


if __name__ == "__main__":
    main()
