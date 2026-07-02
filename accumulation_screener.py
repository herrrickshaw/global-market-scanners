#!/usr/bin/env python3
"""
accumulation_screener.py
------------------------
A screener that ranks stocks by **accumulation** and **Chaikin Money Flow (CMF)**,
and — the point — *validates* the signal against realised forward returns at the
**1-month (21d)** and **6-month (126d)** horizons.

Signal (from daily OHLC, reusing darvas_volume primitives), over a trailing window:
  cmf          Chaikin Money Flow  ∈ [−1,1]   (closes in the upper range on volume)
  accum        composite = cmf + OBV-trend + A/D-trend + volume-trend
               + tanh(½·ln(up/down-volume))   (all scale-free, ≈ [−5,5])

Validation is point-in-time and look-ahead-free: the signal is measured over the
window ending at date T, the return is realised strictly after T (T → T+horizon).
Stocks are sorted into quintiles; we report the **median** forward return per
quintile (robust to penny-stock outliers), the long-short Q5−Q1 spread, the rank
monotonicity, and the **information coefficient** (correlation of signal to forward
return). Pooled across the liquid names in all markets.

Caveat: the seed parquets hold ~1 trading year, so the 6-month test uses a single
forward window; treat magnitudes as indicative, the Q5−Q1 *ordering* as the result.

Usage:
  python accumulation_screener.py --market US            # screen + 1m/6m validation
  python accumulation_screener.py --all --signal cmf
  python accumulation_screener.py --market US --out accum_screen.csv
"""

from __future__ import annotations

import argparse
import os
import sys
import warnings

import numpy as np
import pandas as pd

from darvas_volume import (obv, chaikin_ad, chaikin_money_flow,
                           up_down_volume_ratio, trend_corr)

warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = os.path.expanduser("~/Downloads/code/python_files/cache_seed")

SIG_WINDOW = 42       # ~2 months to estimate stable OBV/CMF trends
H_1M = 21             # 1-month forward
H_6M = 126            # 6-month forward


# ── pure signal + validation core ─────────────────────────────────────────────
def accumulation_signal(high, low, close, volume) -> dict:
    """The screener's features over a window: CMF and the accumulation composite."""
    cmf = chaikin_money_flow(high, low, close, volume)
    obv_tr = trend_corr(obv(close, volume))
    ad_tr = trend_corr(chaikin_ad(high, low, close, volume))
    vol_tr = trend_corr(volume)
    ud = up_down_volume_ratio(close, volume)
    ud_term = np.tanh(0.5 * np.log(ud)) if np.isfinite(ud) and ud > 0 else 0.0
    parts = [x for x in [cmf, obv_tr, ad_tr, vol_tr, ud_term] if np.isfinite(x)]
    accum = float(np.sum(parts)) if parts else np.nan
    return {"cmf": cmf, "accum": accum}


def information_coefficient(signal, fwd_ret) -> float:
    """Pearson correlation between the signal and the forward return (the IC).
    Positive = the signal predicts higher returns."""
    s = pd.to_numeric(pd.Series(list(signal)), errors="coerce")
    r = pd.to_numeric(pd.Series(list(fwd_ret)), errors="coerce")
    j = pd.concat([s, r], axis=1).dropna()
    if len(j) < 10 or j.iloc[:, 0].std() == 0 or j.iloc[:, 1].std() == 0:
        return np.nan
    return float(j.iloc[:, 0].corr(j.iloc[:, 1]))


def quantile_returns(panel: pd.DataFrame, signal_col: str, q: int = 5,
                     ret_col: str = "fwd_ret") -> pd.DataFrame:
    """Median & mean forward return by signal quantile (Q1 = lowest signal)."""
    d = panel.dropna(subset=[signal_col, ret_col]).copy()
    if len(d) < q * 3:
        return pd.DataFrame()
    d["bucket"] = pd.qcut(d[signal_col], q, labels=[f"Q{i}" for i in range(1, q + 1)],
                          duplicates="drop")
    g = d.groupby("bucket")[ret_col].agg(median="median", mean="mean", n="count").reset_index()
    g["median_fwd%"] = (g["median"] * 100).round(2)
    g["mean_fwd%"] = (g["mean"] * 100).round(2)
    return g[["bucket", "median_fwd%", "mean_fwd%", "n"]]


def monotonicity(curve: pd.DataFrame, col: str = "median_fwd%") -> float:
    if curve.empty or len(curve) < 3:
        return np.nan
    ranks = pd.Series(curve[col].values).rank().values
    ideal = np.arange(1, len(curve) + 1)
    return float(np.corrcoef(ranks, ideal)[0, 1])


# ── data assembly (offline, point-in-time) ────────────────────────────────────
def _wide(market: str):
    import marketdata
    return marketdata.wide(market)


def build_panel(market: str, horizon: int, sig_window: int = SIG_WINDOW) -> pd.DataFrame:
    """Point-in-time panel: signal over [T−sig_window, T], forward return T→T+horizon,
    for each liquid name (T is the latest date leaving a full forward window)."""
    import pead_factor as pf
    w = _wide(market)
    if w is None:
        return pd.DataFrame()
    close, high, low, vol = w["Close"], w["High"], w["Low"], w["Volume"]
    symbols = pf._liquid_symbols(close, vol)
    n = len(close)
    T = n - horizon - 1
    if T - sig_window < 0:
        return pd.DataFrame()
    rows = []
    for s in symbols:
        c = close[s]
        p0, p1 = c.iloc[T], c.iloc[T + horizon]
        if not (np.isfinite(p0) and np.isfinite(p1) and p0 > 0):
            continue
        sl = slice(T - sig_window, T)
        cw = c.iloc[sl].values
        if np.isfinite(cw).sum() < sig_window // 2:
            continue
        sig = accumulation_signal(high[s].iloc[sl].values, low[s].iloc[sl].values,
                                  cw, vol[s].iloc[sl].values)
        rows.append({"market": market, "ticker": s, "fwd_ret": float(p1 / p0 - 1),
                     **sig})
    return pd.DataFrame(rows)


def current_screen(market: str, sig_window: int = SIG_WINDOW) -> pd.DataFrame:
    """The actionable screen right now: signal over the most recent window."""
    import pead_factor as pf
    w = _wide(market)
    if w is None:
        return pd.DataFrame()
    close, high, low, vol = w["Close"], w["High"], w["Low"], w["Volume"]
    symbols = pf._liquid_symbols(close, vol)
    rows = []
    for s in symbols:
        c = close[s].dropna()
        if len(c) < sig_window + 2:
            continue
        idx = c.index[-sig_window:]
        sig = accumulation_signal(high[s].reindex(idx).values, low[s].reindex(idx).values,
                                  c.reindex(idx).values, vol[s].reindex(idx).values)
        rows.append({"market": market, "ticker": s, "close": round(float(c.iloc[-1]), 2), **sig})
    return pd.DataFrame(rows)


def _report(panel: pd.DataFrame, label: str):
    print(f"\n  --- {label} forward ({len(panel)} stock-obs) ---")
    for col in ("cmf", "accum"):
        curve = quantile_returns(panel, col)
        if curve.empty:
            print(f"    {col}: too few obs"); continue
        ic = information_coefficient(panel[col], panel["fwd_ret"])
        mono = monotonicity(curve)
        ls = curve["median_fwd%"].iloc[-1] - curve["median_fwd%"].iloc[0]
        meds = " ".join(f"{b}={m:+.1f}" for b, m in zip(curve["bucket"], curve["median_fwd%"]))
        print(f"    by {col:6}: {meds}  | Q5−Q1 median {ls:+.2f}%  IC={ic:+.3f}  mono={mono:+.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default=None, help="market code; default: all")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--signal", choices=["accum", "cmf"], default="accum")
    ap.add_argument("--sig-window", type=int, default=SIG_WINDOW)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    markets = (marketdata.market_list()
               if (args.all or not args.market) else [args.market])

    # ── validation ────────────────────────────────────────────────────────────
    print("=== ACCUMULATION / CMF SCREENER — VALIDATION (point-in-time, look-ahead-free) ===")
    for horizon, name in [(H_1M, "1-MONTH (21d)"), (H_6M, "6-MONTH (126d)")]:
        panel = pd.concat([build_panel(m, horizon, args.sig_window) for m in markets],
                          ignore_index=True)
        panel = panel[panel.get("fwd_ret").abs() < 3.0] if not panel.empty else panel  # drop >300% penny outliers
        if panel.empty:
            print(f"\n  {name}: insufficient history for this horizon"); continue
        _report(panel, name)
    print("\n  (higher accumulation/CMF quantile earning higher forward return = the signal works;"
          "\n   IC = signal↔return correlation; median used so penny outliers don't distort.)")

    # ── current screen ─────────────────────────────────────────────────────────
    scr = pd.concat([current_screen(m, args.sig_window) for m in markets], ignore_index=True)
    scr = scr.dropna(subset=[args.signal]).sort_values(args.signal, ascending=False)
    tag = ", ".join(markets) if len(markets) <= 3 else f"{len(markets)} markets"
    print(f"\n=== CURRENT SCREEN — top {args.top} by {args.signal} — {tag} "
          f"({len(scr)} liquid names) ===")
    print(f"  {'mkt':4}{'ticker':13}{'close':>9}{'CMF':>8}{'accum':>8}")
    for _, r in scr.head(args.top).iterrows():
        print(f"  {str(r['market']):4}{str(r['ticker']):13}{r['close']:>9.2f}"
              f"{r['cmf']:>8.2f}{r['accum']:>8.2f}")
    if args.out:
        scr.to_csv(args.out, index=False)
        print(f"\n  wrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
