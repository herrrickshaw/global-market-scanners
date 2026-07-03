#!/usr/bin/env python3
"""
liquidity_multiyear.py
----------------------
The 1-year seed cache is too short to test the **liquidity premium** cleanly
(liquidity_factor.py could only run a single cross-section). This fetches ~10 years
of daily OHLC via the governed apiclient for a liquid US universe and runs a proper
**multi-period (Fama-MacBeth-style)** test:

  * at each quarter-end T over the sample, sort the cross-section into quintiles by
    Amihud illiquidity (ILLIQ over the trailing year);
  * record each quintile's mean forward return (next quarter);
  * average those quintile returns ACROSS all the dates, and t-test the illiquid−liquid
    (Q5−Q1) spread across dates — the classic liquidity-premium test with real power.

The premium (Amihud-Mendelson 1986; Amihud 2002) predicts a positive, significant
Q5−Q1. Reuses `liquidity_factor.amihud_illiq`.

Usage:
  python liquidity_multiyear.py --n 250 --years 10 --horizon 63
"""

from __future__ import annotations

import argparse
import sys
import warnings

import numpy as np
import pandas as pd

import marketdata
from liquidity_factor import amihud_illiq

warnings.filterwarnings("ignore")


# ── pure multi-period core ────────────────────────────────────────────────────
def fama_macbeth_quintiles(panel: pd.DataFrame, q: int = 5) -> dict:
    """panel: rows of {date, ticker, illiq, fwd_ret}. For each date sort into ILLIQ
    quintiles, take each quintile's mean forward return; average across dates and
    t-test the Q5−Q1 spread (Fama-MacBeth). Returns the quintile means, the spread,
    its t-stat, the average cross-sectional IC and monotonicity."""
    per_date_q = []          # list of {Q1..Qq} mean fwd per date
    per_date_ic = []
    for d, g in panel.groupby("date"):
        g = g.dropna(subset=["illiq", "fwd_ret"])
        if len(g) < q * 3:
            continue
        try:
            buckets = pd.qcut(g["illiq"], q, labels=range(1, q + 1), duplicates="drop")
        except ValueError:
            continue
        means = g.groupby(buckets)["fwd_ret"].mean()
        if len(means) == q:
            per_date_q.append(means.values)
        if g["illiq"].std() > 0 and g["fwd_ret"].std() > 0:
            per_date_ic.append(g["illiq"].corr(g["fwd_ret"]))
    if len(per_date_q) < 5:
        return {"n_dates": len(per_date_q)}
    M = np.array(per_date_q)                       # dates × quintiles
    qmean = M.mean(axis=0)                          # avg quintile forward return
    spread = M[:, -1] - M[:, 0]                     # Q5−Q1 per date
    t = float(spread.mean() / (spread.std(ddof=1) / np.sqrt(len(spread)))) if spread.std() else np.nan
    ranks = pd.Series(qmean).rank().values
    mono = float(np.corrcoef(ranks, np.arange(1, q + 1))[0, 1])
    return {"n_dates": len(per_date_q), "quintile_fwd%": [round(x * 100, 2) for x in qmean],
            "Q5_minus_Q1%": round((qmean[-1] - qmean[0]) * 100, 2), "t_stat": round(t, 2),
            "avg_IC": round(float(np.mean(per_date_ic)), 3) if per_date_ic else np.nan,
            "monotonicity": round(mono, 2)}


# ── data (yfinance, governed) ─────────────────────────────────────────────────
def universe(n: int) -> list:
    """A liquid US universe: the top-n names by trailing dollar-volume in the cache
    (bare tickers work as yfinance US symbols)."""
    close, vol = marketdata.close_volume("US")
    if close is None:
        return []
    return marketdata.liquid_symbols(close, vol)[:n]


def build_panel(prices: dict, years: int, lookback: int = 252, horizon: int = 63,
                step: int = 63) -> pd.DataFrame:
    """Rolling point-in-time panel across all fetched tickers: ILLIQ over the trailing
    year at each quarter-end T, forward return over the next `horizon` days."""
    rows = []
    for tk, df in prices.items():
        if df is None or "Close" not in df or len(df) < lookback + horizon + 5:
            continue
        c = df["Close"].astype(float)
        v = df["Volume"].astype(float) if "Volume" in df else pd.Series(np.nan, index=c.index)
        r = c.pct_change()
        dv = c * v
        for T in range(lookback, len(c) - horizon, step):
            p0, p1 = c.iloc[T], c.iloc[T + horizon]
            if not (np.isfinite(p0) and np.isfinite(p1) and p0 > 0):
                continue
            il = amihud_illiq(r.iloc[T - lookback:T].values, dv.iloc[T - lookback:T].values)
            if np.isfinite(il):
                rows.append({"date": c.index[T], "ticker": tk, "illiq": il,
                             "fwd_ret": float(p1 / p0 - 1)})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=250, help="liquid US names to fetch")
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--horizon", type=int, default=63, help="forward window (trading days)")
    args = ap.parse_args()

    import apiclient
    tk = universe(args.n)
    print(f"fetching {len(tk)} US tickers, {args.years}y daily (governed yfinance)…",
          file=sys.stderr)
    prices = apiclient.yf_download(tk, period=f"{args.years}y")
    print(f"got {len(prices)} price series", file=sys.stderr)
    panel = build_panel(prices, args.years, horizon=args.horizon)
    panel = panel[panel["fwd_ret"].abs() < 3.0]
    res = fama_macbeth_quintiles(panel)

    print(f"\n=== LIQUIDITY PREMIUM — MULTI-YEAR ({args.years}y), Fama-MacBeth ===")
    print(f"  {len(prices)} names · {panel['ticker'].nunique()} in panel · "
          f"{res.get('n_dates',0)} quarterly cross-sections · {len(panel):,} stock-obs")
    if res.get("quintile_fwd%"):
        qs = res["quintile_fwd%"]
        print(f"  mean {args.horizon}d fwd return by ILLIQ quintile (Q1=liquid…Q5=illiquid):")
        print("   " + "  ".join(f"Q{i+1}={qs[i]:+.2f}%" for i in range(len(qs))))
        print(f"  Q5−Q1 = {res['Q5_minus_Q1%']:+.2f}%   t = {res['t_stat']}   "
              f"avg IC = {res['avg_IC']}   monotonicity = {res['monotonicity']}")
        sig = "SIGNIFICANT liquidity premium" if abs(res["t_stat"]) > 2 and res["Q5_minus_Q1%"] > 0 \
            else "not significant"
        print(f"  -> {sig} (illiquid earn more, |t|>2)")
    else:
        print("  insufficient cross-sections")


if __name__ == "__main__":
    main()
