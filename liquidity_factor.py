#!/usr/bin/env python3
"""
liquidity_factor.py
-------------------
The liquidity factor — the next research gap the literature scout surfaced. It
implements Amihud's (2002) **illiquidity** measure (ILLIQ) and the associated
**liquidity premium** (illiquid stocks must offer higher expected returns to
compensate for their trading frictions; Amihud-Mendelson 1986, Pastor-Stambaugh
2003), across all 19 markets — using only price and volume, which the platform
already has.

  ILLIQ_i = average over the window of  |return_i,t| / dollar_volume_i,t   (× 1e6)

High ILLIQ = a stock whose price moves a lot per dollar traded = illiquid.

Two uses, deliberately kept distinct because they point opposite ways:
  * the **liquidity premium** (a return signal) — illiquid names have earned higher
    forward returns; reported as forward return by ILLIQ quantile. Real but hard to
    harvest net of the very trading costs that cause it.
  * a **capacity / tradeability score** (a risk lens) — high = liquid = safe to size
    up; low = illiquid = cap the position. This is the retail-appropriate use and is
    what feeds `meta_screen.py --liquidity` and can tighten `portfolio.py` caps.

Pure core (ILLIQ, scores, the premium quantile study) is unit-tested and offline.

Usage:
  python liquidity_factor.py --market US                  # ILLIQ + liquidity-premium study
  python liquidity_factor.py --all --out liquidity.csv    # capacity scores for meta_screen
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

ILLIQ_SCALE = 1e6        # Amihud's conventional scaling so ILLIQ is O(0.1–10)
# the seed parquets hold ~1 trading year, so the point-in-time windows are sized to fit:
DEFAULT_LOOKBACK = 126   # ~6 months to estimate ILLIQ
DEFAULT_HORIZON = 21     # ~1 month forward for the premium study


# ── pure liquidity core ───────────────────────────────────────────────────────
def amihud_illiq(returns, dollar_volume, scale: float = ILLIQ_SCALE) -> float:
    """Amihud (2002) ILLIQ: mean of |return| / dollar-volume over valid days, ×scale.
    Days with zero/NaN dollar-volume are dropped (undefined price impact)."""
    r = np.abs(np.asarray(returns, dtype=float))
    dv = np.asarray(dollar_volume, dtype=float)
    ok = np.isfinite(r) & np.isfinite(dv) & (dv > 0)
    if ok.sum() < 5:
        return np.nan
    return float(np.mean(r[ok] / dv[ok]) * scale)


def zero_return_frac(returns) -> float:
    """Lesmond-style illiquidity proxy: fraction of near-zero-return days (no trade
    pressure moved the price)."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if r.size == 0:
        return np.nan
    return float(np.mean(np.abs(r) < 1e-4))


def turnover(volume, shares_out) -> float:
    """Share turnover = traded shares / shares outstanding (higher = more liquid)."""
    v = np.nansum(np.asarray(volume, dtype=float))
    return float(v / shares_out) if shares_out else np.nan


def capacity_score(illiq: pd.Series) -> pd.Series:
    """Cross-sectional 0–100 tradeability score: 100 = most liquid (lowest ILLIQ),
    0 = most illiquid. The retail/risk-appropriate lens (size up liquid names)."""
    r = pd.to_numeric(illiq, errors="coerce").rank(pct=True)   # high ILLIQ -> high rank
    return ((1.0 - r) * 100).round(1)                          # invert: liquid -> high score


def illiq_pctile(illiq: pd.Series) -> pd.Series:
    """Cross-sectional 0–100 ILLIQ percentile (100 = most illiquid) — the sort
    variable for the liquidity-premium study."""
    return (pd.to_numeric(illiq, errors="coerce").rank(pct=True) * 100).round(1)


def premium_by_illiq(panel: pd.DataFrame, q: int = 5) -> pd.DataFrame:
    """Forward return by ILLIQ quantile. `panel` needs columns illiq, fwd_ret.
    The liquidity premium ⇒ mean forward return increases from liquid (Q1) to
    illiquid (Q5)."""
    d = panel.dropna(subset=["illiq", "fwd_ret"]).copy()
    if len(d) < q * 3:
        return pd.DataFrame()
    d["bucket"] = pd.qcut(d["illiq"], q, labels=[f"Q{i}" for i in range(1, q + 1)],
                          duplicates="drop")
    g = d.groupby("bucket")["fwd_ret"].agg(mean="mean", median="median", n="count").reset_index()
    g["mean_fwd%"] = (g["mean"] * 100).round(2)
    g["median_fwd%"] = (g["median"] * 100).round(2)
    return g[["bucket", "mean_fwd%", "median_fwd%", "n"]]


def monotonicity(curve: pd.DataFrame, col: str = "mean_fwd%") -> float:
    """+1 = forward return rises perfectly from Q1→Q5 (a clean liquidity premium)."""
    if curve.empty or len(curve) < 3:
        return np.nan
    ranks = pd.Series(curve[col].values).rank().values
    ideal = np.arange(1, len(curve) + 1)
    return float(np.corrcoef(ranks, ideal)[0, 1])


# ── data assembly (offline, prices) ───────────────────────────────────────────
def _market_wide(market: str):
    p = os.path.join(SEED, f"cleaned_long_{market}.parquet")
    if not os.path.exists(p):
        return None, None
    px = pd.read_parquet(p)
    close = px.pivot_table(index="Date", columns="Symbol", values="Close", aggfunc="last").astype(float)
    vol = px.pivot_table(index="Date", columns="Symbol", values="Volume", aggfunc="last").astype(float)
    return close, vol


def scan_market(market: str, lookback: int = DEFAULT_LOOKBACK, horizon: int = DEFAULT_HORIZON) -> tuple:
    """Return (panel, scores) for a market. panel: {ticker, illiq, fwd_ret} evaluated
    point-in-time (ILLIQ on the trailing window ending at T, forward return after T).
    scores: current {ticker, illiq, capacity_score, illiq_pctile}."""
    close, vol = _market_wide(market)
    if close is None:
        return pd.DataFrame(), pd.DataFrame()
    rets = close.pct_change()
    dollar_vol = close * vol
    n = len(close)
    T = n - horizon - 1                                        # evaluation date (leaves a fwd window)
    panel_rows, score_rows = [], []
    for sym in close.columns:
        c = close[sym].dropna()
        if len(c) < lookback + horizon + 5:
            continue
        r = rets[sym]
        dv = dollar_vol[sym]
        # current ILLIQ over the most recent window (for the tradeable capacity score)
        cur = amihud_illiq(r.tail(lookback), dv.tail(lookback))
        if np.isfinite(cur):
            score_rows.append({"market": market, "ticker": sym, "illiq": cur})
        # point-in-time panel: ILLIQ up to T, realised return T -> T+horizon
        if T > lookback and T + horizon < n:
            illiq_T = amihud_illiq(r.iloc[T - lookback:T], dv.iloc[T - lookback:T])
            fwd = c_fwd = None
            try:
                p0, p1 = close[sym].iloc[T], close[sym].iloc[T + horizon]
                if np.isfinite(p0) and np.isfinite(p1) and p0 > 0:
                    fwd = p1 / p0 - 1
            except Exception:
                fwd = None
            if illiq_T is not None and np.isfinite(illiq_T) and fwd is not None and np.isfinite(fwd):
                panel_rows.append({"market": market, "ticker": sym,
                                   "illiq": illiq_T, "fwd_ret": fwd})
    scores = pd.DataFrame(score_rows)
    if not scores.empty:
        scores["capacity_score"] = capacity_score(scores["illiq"])
        scores["illiq_pctile"] = illiq_pctile(scores["illiq"])
    return pd.DataFrame(panel_rows), scores


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default=None, help="market code, e.g. US, JP; default: all")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK)
    ap.add_argument("--horizon", type=int, default=DEFAULT_HORIZON, help="forward window for the premium study")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--out", default=None, help="write ticker,capacity_score CSV for meta_screen")
    args = ap.parse_args()

    markets = ([f.split("cleaned_long_")[1].split(".")[0]
                for f in sorted(os.listdir(SEED)) if f.startswith("cleaned_long_")]
               if (args.all or not args.market) else [args.market])

    panels, scores = [], []
    for m in markets:
        pan, sc = scan_market(m, args.lookback, args.horizon)
        if not pan.empty:
            panels.append(pan)
        if not sc.empty:
            scores.append(sc)
    if not panels:
        raise SystemExit("no liquidity panel (missing price parquets?)")
    panel = pd.concat(panels, ignore_index=True)
    allsc = pd.concat(scores, ignore_index=True) if scores else pd.DataFrame()

    curve = premium_by_illiq(panel)
    tag = ", ".join(markets) if len(markets) <= 3 else f"{len(markets)} markets"
    print(f"\n=== LIQUIDITY PREMIUM — {tag} "
          f"({len(panel)} stock-observations, {args.horizon}d forward) ===")
    if not curve.empty:
        print("  forward return by Amihud ILLIQ quantile (Q1=most liquid, Q5=most illiquid):")
        print(curve.to_string(index=False))
        mono = monotonicity(curve)
        prem = curve["mean_fwd%"].iloc[-1] - curve["mean_fwd%"].iloc[0]
        verdict = ("liquidity premium PRESENT (illiquid earn more, monotone)"
                   if mono and mono > 0.7 and prem > 0 else "weak/absent")
        print(f"  monotonicity = {mono:.2f}  ->  {verdict}")
        print(f"  premium (Q5−Q1 forward) = {prem:.2f}%  "
              f"(gross; the trading costs that cause illiquidity also erode this)")

    if not allsc.empty:
        print(f"\n=== most vs least liquid (capacity score; 100=liquid) — top/bottom {args.top} ===")
        s = allsc.sort_values("capacity_score", ascending=False)
        print("  MOST liquid:", ", ".join(f"{r.ticker}" for r in s.head(args.top).itertuples()))
        print("  LEAST liquid:", ", ".join(f"{r.ticker}" for r in s.tail(args.top).itertuples()))

    if args.out and not allsc.empty:
        allsc = allsc.rename(columns={"capacity_score": "liquidity_score"})
        allsc[["ticker", "liquidity_score"]].to_csv(args.out, index=False)
        print(f"\n  wrote {args.out} (ticker,liquidity_score for meta_screen --liquidity)",
              file=sys.stderr)


if __name__ == "__main__":
    main()
