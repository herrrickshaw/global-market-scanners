#!/usr/bin/env python3
"""
seasonality.py
--------------
Closes the scout's 'seasonality' gap: calendar-effect analysis from prices —
day-of-week, turn-of-the-month, monthly seasonality and the Sell-in-May (Halloween)
effect.

Data honesty: day-of-week (~50 obs/weekday/yr) and turn-of-month (~12/yr) are
estimable from the ~1-year seed data; **monthly seasonality and Sell-in-May need
several years** (1 obs/month/yr), so those are computed only when a multi-year series
is supplied (e.g. an index fetched with more history) and are otherwise skipped.

Pure functions over a daily-return series (offline, unit-tested); the CLI runs them on
each market's equal-weight proxy.

Usage:
  python seasonality.py --market US
  python seasonality.py --all
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
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri"]


# ── pure calendar-effect core ─────────────────────────────────────────────────
def day_of_week(returns: pd.Series) -> pd.DataFrame:
    """Mean return by weekday (a DatetimeIndex return Series)."""
    r = returns.dropna()
    g = r.groupby(r.index.dayofweek).agg(mean="mean", n="count")
    g = g[g.index <= 4]
    g["weekday"] = [DOW[i] for i in g.index]
    g["mean%"] = (g["mean"] * 100).round(3)
    return g.reset_index(drop=True)[["weekday", "mean%", "n"]]


def turn_of_month(returns: pd.Series, window: int = 3) -> dict:
    """Turn-of-the-month effect: mean return on the last trading day of a month plus
    the first `window` of the next, vs the rest of the month."""
    r = returns.dropna()
    if r.empty:
        return {}
    dfm = pd.DataFrame({"r": r})
    dfm["ym"] = dfm.index.to_period("M")
    is_tom = pd.Series(False, index=dfm.index)
    for _, idx in dfm.groupby("ym").groups.items():
        idx = pd.DatetimeIndex(idx)
        is_tom.loc[idx[-1:]] = True                      # last day of month
    # first `window` days of each month
    for _, idx in dfm.groupby("ym").groups.items():
        idx = pd.DatetimeIndex(sorted(idx))
        is_tom.loc[idx[:window]] = True
    tom = r[is_tom]; rest = r[~is_tom]
    return {"tom_mean%": round(tom.mean() * 100, 3), "tom_n": int(len(tom)),
            "rest_mean%": round(rest.mean() * 100, 3), "rest_n": int(len(rest)),
            "edge%": round((tom.mean() - rest.mean()) * 100, 3)}


def monthly_seasonality(returns: pd.Series) -> pd.DataFrame:
    """Mean return by calendar month (needs multi-year data to be meaningful)."""
    r = returns.dropna()
    m = (1 + r).groupby([r.index.year, r.index.month]).prod() - 1   # compounded monthly
    m.index = m.index.set_names(["year", "month"])
    g = m.groupby("month").agg(mean="mean", n="count")
    g["mean%"] = (g["mean"] * 100).round(2)
    return g.reset_index()[["month", "mean%", "n"]]


def sell_in_may(returns: pd.Series) -> dict:
    """Halloween effect: Nov–Apr vs May–Oct mean monthly return."""
    r = returns.dropna()
    m = (1 + r).groupby([r.index.year, r.index.month]).prod() - 1
    month = m.index.get_level_values(1)
    win = m[(month >= 11) | (month <= 4)]                # Nov–Apr
    sum_ = m[(month >= 5) & (month <= 10)]               # May–Oct
    return {"nov_apr%": round(win.mean() * 100, 2), "may_oct%": round(sum_.mean() * 100, 2),
            "edge%": round((win.mean() - sum_.mean()) * 100, 2),
            "years": int(m.index.get_level_values(0).nunique())}


# ── data (offline) ────────────────────────────────────────────────────────────
def market_returns(market: str) -> pd.Series:
    """Equal-weight daily return of the LIQUID universe (penny junk poisons a raw
    all-stock mean), with per-day returns clipped to ±50% as a glitch guard."""
    import liquidity_factor as lf
    import pead_factor as pf
    w = lf._market_wide(market)
    if w is None:
        raise SystemExit(f"no parquet for {market}")
    close, vol = w
    symbols = pf._liquid_symbols(close, vol)
    rets = close[symbols].pct_change(fill_method=None).clip(-0.5, 0.5)
    r = rets.mean(axis=1).dropna()
    r.index = pd.to_datetime(r.index)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="US")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    markets = ([f.split("cleaned_long_")[1].split(".")[0]
                for f in sorted(os.listdir(SEED)) if f.startswith("cleaned_long_")]
               if args.all else [args.market])
    for m in markets:
        r = market_returns(m)
        yrs = r.index.year.nunique()
        print(f"\n=== SEASONALITY — {m} equal-weight proxy ({len(r)} days, {yrs} yr) ===")
        print("  day-of-week mean return:")
        print(day_of_week(r).to_string(index=False))
        tom = turn_of_month(r)
        print(f"  turn-of-month: ToM {tom.get('tom_mean%')}% vs rest {tom.get('rest_mean%')}%  "
              f"-> edge {tom.get('edge%')}% (ToM n={tom.get('tom_n')})")
        if yrs >= 3:
            sim = sell_in_may(r)
            print(f"  Sell-in-May: Nov-Apr {sim['nov_apr%']}% vs May-Oct {sim['may_oct%']}%  "
                  f"-> edge {sim['edge%']}%")
        else:
            print(f"  monthly/Sell-in-May: skipped (needs >=3 yr; have {yrs})")


if __name__ == "__main__":
    main()
