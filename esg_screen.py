#!/usr/bin/env python3
"""
esg_screen.py
-------------
Closes the scout's 'esg_climate' gap: an ESG screener from yfinance
`.sustainability` (Sustainalytics ESG *risk* scores — lower = lower ESG risk =
better). Exposes the total ESG risk plus the E/S/G pillar scores and a controversy
level, and ranks names best-first.

Coverage is limited to firms Sustainalytics rates (mostly larger caps); the pure
normalisation is unit-tested and the fetch degrades gracefully offline.

Usage:
  python esg_screen.py --tickers AAPL,MSFT,XOM,NVDA
"""

from __future__ import annotations

import argparse
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


# ── pure normalisation core ───────────────────────────────────────────────────
def esg_grade(total_risk: float) -> str:
    """Sustainalytics risk bands: <10 negligible, 10-20 low, 20-30 medium, 30-40 high,
    >=40 severe."""
    if total_risk is None or not np.isfinite(total_risk):
        return "n/a"
    for hi, g in [(10, "negligible"), (20, "low"), (30, "medium"), (40, "high")]:
        if total_risk < hi:
            return g
    return "severe"


def esg_score_0_100(total_risk: float) -> float:
    """Convert a 0–50+ risk score to a 0–100 'quality' score (100 = lowest risk)."""
    if total_risk is None or not np.isfinite(total_risk):
        return np.nan
    return round(float(max(0.0, 100.0 - 2.0 * total_risk)), 1)     # risk 0 -> 100, risk 50 -> 0


def rank_esg(df: pd.DataFrame) -> pd.DataFrame:
    """Rank a frame of {ticker,total_esg,...} best-first (lowest risk first)."""
    d = df.dropna(subset=["total_esg"]).copy()
    d["esg_score"] = d["total_esg"].map(esg_score_0_100)
    d["grade"] = d["total_esg"].map(esg_grade)
    return d.sort_values("total_esg")


# ── fetch (yfinance sustainability, graceful) ─────────────────────────────────
def fetch_esg_yf(ticker: str) -> dict:
    import apiclient
    import yfinance as yf
    try:
        s = apiclient.robust("yfinance", lambda: yf.Ticker(ticker).sustainability, retries=2)
        if s is None or s.empty:
            return {}
        col = s.columns[0]
        g = lambda k: float(s.loc[k, col]) if k in s.index and pd.notna(s.loc[k, col]) else np.nan
        return {"ticker": ticker, "total_esg": g("totalEsg"), "env": g("environmentScore"),
                "social": g("socialScore"), "gov": g("governanceScore"),
                "controversy": g("highestControversy")}
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default="AAPL,MSFT,XOM,NVDA,JNJ")
    args = ap.parse_args()
    rows = [d for d in (fetch_esg_yf(t.strip().upper())
                        for t in args.tickers.split(",") if t.strip()) if d]
    if not rows:
        print("no ESG data fetched (offline / uncovered) — normalisation is unit-tested"); return
    df = rank_esg(pd.DataFrame(rows))
    print(f"\n=== ESG RISK SCREENER (yfinance / Sustainalytics) — {len(df)} names ===")
    print(f"  {'ticker':10}{'ESG_risk':>9}{'score':>7}{'grade':>12}{'E':>7}{'S':>7}{'G':>7}{'ctrv':>6}")
    for _, r in df.iterrows():
        print(f"  {str(r['ticker']):10}{r['total_esg']:>9.1f}{r['esg_score']:>7.1f}{r['grade']:>12}"
              f"{r.get('env', float('nan')):>7.1f}{r.get('social', float('nan')):>7.1f}"
              f"{r.get('gov', float('nan')):>7.1f}{r.get('controversy', float('nan')):>6.0f}")
    print("\n  ESG_risk = Sustainalytics risk (lower better); score = 100−2×risk; ctrv = controversy 1-5.")


if __name__ == "__main__":
    main()
