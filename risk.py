#!/usr/bin/env python3
"""
risk.py
-------
The risk layer the platform was missing: every backtest and screen reported
*return* but never *risk-adjusted survival*. This turns any return series into
the numbers a desk actually gates on — annualised vol, historical VaR/CVaR,
max drawdown, Sharpe/Sortino — plus a lightweight regime flag (is trailing vol
elevated vs its own history, and are we in a drawdown?).

Pure functions over a pandas/numpy return series (decimal, e.g. 0.01 = +1%),
so it's unit-testable offline and reusable by portfolio.py / pit_backtest.py.

Usage:
  python risk.py --market US               # risk of an equal-weight market proxy
  python risk.py --market JP --periods 252
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

SEED = os.path.expanduser("~/Downloads/code/python_files/cache_seed")


# ── pure metrics ──────────────────────────────────────────────────────────────
def equity_curve(returns: np.ndarray) -> np.ndarray:
    """Cumulative wealth from decimal returns (starts at 1.0 after first step)."""
    r = np.asarray(returns, dtype=float)
    return np.cumprod(1.0 + r)


def max_drawdown(returns) -> float:
    """Worst peak-to-trough loss as a negative fraction (e.g. -0.32 = -32%)."""
    r = np.asarray(returns, dtype=float)
    if r.size == 0:
        return 0.0
    eq = equity_curve(r)
    peak = np.maximum.accumulate(eq)
    return float(((eq - peak) / peak).min())


def hist_var(returns, alpha: float = 0.05) -> float:
    """Historical Value-at-Risk: the alpha-quantile loss, returned as a positive
    fraction (0.04 = you lose >=4% on the worst alpha of periods)."""
    r = np.asarray(returns, dtype=float)
    if r.size == 0:
        return 0.0
    return float(-np.quantile(r, alpha))


def cvar(returns, alpha: float = 0.05) -> float:
    """Conditional VaR / expected shortfall: mean loss in the worst alpha tail."""
    r = np.asarray(returns, dtype=float)
    if r.size == 0:
        return 0.0
    thr = np.quantile(r, alpha)
    tail = r[r <= thr]
    return float(-tail.mean()) if tail.size else float(-thr)


def ann_vol(returns, periods: int = 252) -> float:
    r = np.asarray(returns, dtype=float)
    return float(r.std(ddof=1) * np.sqrt(periods)) if r.size > 1 else 0.0


def ann_return(returns, periods: int = 252) -> float:
    r = np.asarray(returns, dtype=float)
    if r.size == 0:
        return 0.0
    eq = equity_curve(r)
    return float(eq[-1] ** (periods / r.size) - 1)


def sharpe(returns, rf: float = 0.0, periods: int = 252) -> float:
    r = np.asarray(returns, dtype=float)
    ex = r - rf / periods
    sd = ex.std(ddof=1)
    return float(ex.mean() / sd * np.sqrt(periods)) if sd > 0 else 0.0


def sortino(returns, rf: float = 0.0, periods: int = 252) -> float:
    r = np.asarray(returns, dtype=float)
    ex = r - rf / periods
    downside = ex[ex < 0]
    dd = downside.std(ddof=1) if downside.size > 1 else 0.0
    return float(ex.mean() / dd * np.sqrt(periods)) if dd > 0 else 0.0


def regime_flag(returns, window: int = 63, periods: int = 252) -> dict:
    """Simple regime read: is trailing-window vol elevated vs the full-sample vol,
    and are we currently in a drawdown? -> 'risk_on' / 'caution' / 'risk_off'."""
    r = np.asarray(returns, dtype=float)
    if r.size < window + 1:
        return {"regime": "unknown", "vol_ratio": None, "in_drawdown": None}
    recent_vol = r[-window:].std(ddof=1) * np.sqrt(periods)
    full_vol = r.std(ddof=1) * np.sqrt(periods)
    ratio = recent_vol / full_vol if full_vol > 0 else 1.0
    eq = equity_curve(r)
    in_dd = bool(eq[-1] < np.maximum.accumulate(eq)[-1] * 0.98)   # >2% off the peak
    if ratio > 1.3 and in_dd:
        regime = "risk_off"
    elif ratio > 1.15 or in_dd:
        regime = "caution"
    else:
        regime = "risk_on"
    return {"regime": regime, "vol_ratio": round(float(ratio), 2), "in_drawdown": in_dd}


def risk_report(returns, periods: int = 252) -> dict:
    """One dict with everything, for a serving layer / dashboard."""
    return {
        "n": int(np.asarray(returns).size),
        "ann_return%": round(ann_return(returns, periods) * 100, 2),
        "ann_vol%": round(ann_vol(returns, periods) * 100, 2),
        "sharpe": round(sharpe(returns, periods=periods), 2),
        "sortino": round(sortino(returns, periods=periods), 2),
        "max_dd%": round(max_drawdown(returns) * 100, 1),
        "VaR95%": round(hist_var(returns, 0.05) * 100, 2),
        "CVaR95%": round(cvar(returns, 0.05) * 100, 2),
        **regime_flag(returns, periods=periods),
    }


# ── data helper (offline, from local parquets) ───────────────────────────────
def market_proxy_returns(market: str) -> pd.Series:
    """Equal-weight daily return of every stock in a market's cleaned_long parquet."""
    p = os.path.join(SEED, f"cleaned_long_{market}.parquet")
    if not os.path.exists(p):
        raise SystemExit(f"no parquet for market {market!r}: {p}")
    df = pd.read_parquet(p)
    wide = df.pivot_table(index="Date", columns="Symbol", values="Close", aggfunc="last")
    rets = wide.astype(float).pct_change()
    return rets.mean(axis=1).dropna()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="US")
    ap.add_argument("--periods", type=int, default=252)
    args = ap.parse_args()

    r = market_proxy_returns(args.market).values
    print(f"=== risk report — {args.market} equal-weight proxy ({len(r)} days) ===",
          file=sys.stderr)
    rep = risk_report(r, periods=args.periods)
    w = max(len(k) for k in rep)
    for k, v in rep.items():
        print(f"  {k:<{w}}  {v}")


if __name__ == "__main__":
    main()
