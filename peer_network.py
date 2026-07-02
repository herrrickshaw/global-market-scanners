#!/usr/bin/env python3
"""
peer_network.py
---------------
Closes the scout's 'network' gap: economic-links / peer lead-lag momentum (in the
spirit of Cohen & Frazzini 2008, "Economic Links and Predictable Returns"). The idea:
returns of economically-linked firms predict a stock's *future* return with a lag,
because information diffuses slowly across linked names.

We form the link network from the industry/peer dataset (companies_industry.parquet):
a stock's "peer basket" = the other liquid firms in its industry (same market). The
signal is the **peer basket's lagged return** (its recent past), tested against the
stock's **forward return** — a lead-lag spillover distinct from own-momentum.

Point-in-time, look-ahead-free; validated with quantile forward returns + the
information coefficient (reusing accumulation_screener). Offline.

Usage:
  python peer_network.py --market US
  python peer_network.py --all --horizon 21
"""

from __future__ import annotations

import argparse
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = os.path.expanduser("~/Downloads/code/python_files/cache_seed")
COMPANIES = os.path.join(HERE, "companies_industry.parquet")
LAG = 21             # peer basket's trailing-return window (the lead signal)
HORIZON = 21         # stock's forward-return window


# ── pure lead-lag core ────────────────────────────────────────────────────────
def basket_return(member_prices: pd.DataFrame, start: int, end: int) -> float:
    """Equal-weight return of a basket of members over positional [start, end]."""
    if member_prices.shape[1] == 0 or end >= len(member_prices) or start < 0:
        return np.nan
    rr = [member_prices.iloc[end, j] / member_prices.iloc[start, j] - 1
          for j in range(member_prices.shape[1])
          if np.isfinite(member_prices.iloc[start, j]) and member_prices.iloc[start, j] > 0
          and np.isfinite(member_prices.iloc[end, j])]
    return float(np.mean(rr)) if rr else np.nan


def peer_signal(peer_lagged_return: float) -> float:
    """The lead-lag signal is simply the peer basket's lagged return (linked names'
    recent move that the stock has not yet fully reflected)."""
    return float(peer_lagged_return) if np.isfinite(peer_lagged_return) else np.nan


# ── data assembly (offline, point-in-time) ────────────────────────────────────
def _clean(t):
    return str(t).split(".")[0].upper()


def build_panel(market: str, lag: int = LAG, horizon: int = HORIZON) -> pd.DataFrame:
    import liquidity_factor as lf
    import pead_factor as pf
    if not os.path.exists(COMPANIES):
        return pd.DataFrame()
    w = lf._market_wide(market)
    if w is None:
        return pd.DataFrame()
    close, vol = w
    symbols = pf._liquid_symbols(close, vol)
    comp = pd.read_parquet(COMPANIES)[["ticker", "industry"]]
    comp["key"] = comp["ticker"].map(_clean)
    ind_of = dict(zip(comp["key"], comp["industry"]))
    by_ind = {}
    for s in symbols:
        ind = ind_of.get(_clean(s))
        if ind and ind == ind:
            by_ind.setdefault(ind, []).append(s)
    n = len(close); T = n - horizon - 1
    if T - lag < 0:
        return pd.DataFrame()
    rows = []
    for ind, members in by_ind.items():
        if len(members) < 4:                          # need peers to form a basket
            continue
        mp = close[members]
        for s in members:
            c = close[s]
            p0, p1 = c.iloc[T], c.iloc[T + horizon]
            if not (np.isfinite(p0) and np.isfinite(p1) and p0 > 0):
                continue
            peers = [x for x in members if x != s]
            peer_lag = basket_return(close[peers], T - lag, T)     # peers' lagged return
            own_lag = c.iloc[T] / c.iloc[T - lag] - 1 if np.isfinite(c.iloc[T - lag]) and c.iloc[T - lag] > 0 else np.nan
            fwd = p1 / p0 - 1
            if np.isfinite(peer_lag) and np.isfinite(fwd):
                rows.append({"market": market, "ticker": s, "industry": ind,
                             "peer_signal": peer_signal(peer_lag),
                             "own_lag": own_lag, "fwd_ret": fwd})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--lag", type=int, default=LAG)
    ap.add_argument("--horizon", type=int, default=HORIZON)
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()
    from accumulation_screener import quantile_returns, information_coefficient, monotonicity

    markets = ([f.split("cleaned_long_")[1].split(".")[0]
                for f in sorted(os.listdir(SEED)) if f.startswith("cleaned_long_")]
               if (args.all or not args.market) else [args.market])
    panel = pd.concat([build_panel(m, args.lag, args.horizon) for m in markets], ignore_index=True)
    panel = panel[panel["fwd_ret"].abs() < 3.0] if not panel.empty else panel
    if panel.empty:
        raise SystemExit("no peer-network panel (need companies_industry.parquet + price data)")

    print(f"\n=== PEER-NETWORK LEAD-LAG — {len(panel)} stock-obs, "
          f"peer {args.lag}d lag -> {args.horizon}d forward ===")
    curve = quantile_returns(panel, "peer_signal")
    if not curve.empty:
        ic = information_coefficient(panel["peer_signal"], panel["fwd_ret"])
        meds = " ".join(f"{b}={m:+.1f}" for b, m in zip(curve["bucket"], curve["median_fwd%"]))
        print(f"  fwd return by peer-signal quantile: {meds}")
        print(f"  Q5-Q1 median {curve['median_fwd%'].iloc[-1]-curve['median_fwd%'].iloc[0]:+.2f}%  "
              f"IC={ic:+.3f}  mono={monotonicity(curve):+.2f}")
    print(f"\n  current top {args.top} by peer signal (linked names moved, stock may follow):")
    cur = panel.sort_values("peer_signal", ascending=False).head(args.top)
    print(f"  {'mkt':4}{'ticker':12}{'industry':28}{'peer%':>8}")
    for _, r in cur.iterrows():
        print(f"  {str(r['market']):4}{str(r['ticker']):12}{str(r['industry'])[:26]:28}"
              f"{r['peer_signal']*100:>8.1f}")


if __name__ == "__main__":
    main()
