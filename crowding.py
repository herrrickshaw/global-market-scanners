#!/usr/bin/env python3
"""
crowding.py
-----------
Closes the scout's 'short_crowding' gap — as far as free data allows. True short-
interest / days-to-cover data is NOT in the public pipeline (it needs an exchange or
vendor feed), so this implements the *price-based* face of crowding: **co-movement
concentration**. A crowded trade is one whose members move together — a stock that is
highly correlated with its market/industry and has run hard is more exposed to a
crowd-unwind than an idiosyncratic name.

  crowding = avg pairwise correlation to the market (β-crowd / co-movement)
             combined with recent relative strength (how far the crowd has pushed it)

Honest limit (stated in the output): this is a co-movement crowding proxy, not
short-interest crowding. It flags names moving with the herd, not names heavily
shorted. Pure core (offline, unit-tested).

Usage:
  python crowding.py --market US --top 15
"""

from __future__ import annotations

import argparse
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

SEED = os.path.expanduser("~/Downloads/code/python_files/cache_seed")
WINDOW = 63          # ~3 months of daily returns for correlations


# ── pure crowding core ────────────────────────────────────────────────────────
def corr_to_market(stock_ret: pd.Series, market_ret: pd.Series) -> float:
    """Correlation of a stock's returns with the market proxy (co-movement)."""
    j = pd.concat([stock_ret, market_ret], axis=1).dropna()
    if len(j) < 20 or j.iloc[:, 0].std() == 0 or j.iloc[:, 1].std() == 0:
        return np.nan
    return float(j.iloc[:, 0].corr(j.iloc[:, 1]))


def rel_strength(stock_prices: pd.Series, lookback: int = WINDOW) -> float:
    """Trailing return over the window (how far the crowd has pushed the name)."""
    c = stock_prices.dropna()
    if len(c) < lookback + 1 or c.iloc[-lookback] <= 0:
        return np.nan
    return float(c.iloc[-1] / c.iloc[-lookback] - 1)


def crowding_score(feat: pd.DataFrame) -> pd.Series:
    """Cross-sectional 0–100 composite from PERCENTILE ranks (robust to moonshot
    outliers): weight co-movement (correlation to the market) more than the run-up, so
    'crowded' means moving with the herd, not a low-correlation single-name moonshot."""
    corr_r = pd.to_numeric(feat["corr_mkt"], errors="coerce").rank(pct=True)
    rs_r = pd.to_numeric(feat["rel_strength"], errors="coerce").rank(pct=True)
    return (100 * (0.65 * corr_r + 0.35 * rs_r)).round(1)


# ── data (offline) ────────────────────────────────────────────────────────────
def scan_market(market: str, window: int = WINDOW) -> pd.DataFrame:
    import liquidity_factor as lf
    import pead_factor as pf
    w = lf._market_wide(market)
    if w is None:
        return pd.DataFrame()
    close, vol = w
    symbols = pf._liquid_symbols(close, vol)
    rets = close[symbols].pct_change(fill_method=None).clip(-0.5, 0.5)
    mkt = rets.mean(axis=1)
    rows = []
    for s in symbols:
        cm = corr_to_market(rets[s].tail(window), mkt.tail(window))
        rs = rel_strength(close[s], window)
        if np.isfinite(cm) and np.isfinite(rs):
            rows.append({"market": market, "ticker": s, "corr_mkt": round(cm, 3),
                         "rel_strength": round(rs, 3)})
    df = pd.DataFrame(rows)
    if not df.empty:
        df["crowding"] = crowding_score(df)
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()
    markets = ([f.split("cleaned_long_")[1].split(".")[0]
                for f in sorted(os.listdir(SEED)) if f.startswith("cleaned_long_")]
               if (args.all or not args.market) else [args.market])
    df = pd.concat([scan_market(m) for m in markets], ignore_index=True)
    if df.empty:
        raise SystemExit("no data")
    df = df.sort_values("crowding", ascending=False)
    print(f"\n=== CO-MOVEMENT CROWDING — {len(df)} liquid names ===")
    print("  proxy: high market-correlation + strong recent run = crowded/co-move risk.")
    print("  (NOT short-interest crowding — that needs an exchange/vendor feed.)")
    print(f"  {'mkt':4}{'ticker':12}{'corr_mkt':>10}{'relStr%':>9}{'crowd':>7}")
    for _, r in df.head(args.top).iterrows():
        print(f"  {str(r['market']):4}{str(r['ticker']):12}{r['corr_mkt']:>10.2f}"
              f"{r['rel_strength']*100:>9.1f}{r['crowding']:>7.2f}")


if __name__ == "__main__":
    main()
