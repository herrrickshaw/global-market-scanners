#!/usr/bin/env python3
"""
pead_factor.py
--------------
Post-Earnings-Announcement Drift (PEAD) — the anomaly the literature scout
surfaced as the platform's top research gap (Ball & Brown 1968; Bernard & Thomas
1989; Chordia et al.). PEAD: after a firm reports an earnings *surprise*, its price
keeps drifting in the direction of the surprise for weeks — positive surprises
drift up, negative down — a slow, predictable under-reaction.

The clean way to measure PEAD needs a history of quarterly EPS vs consensus and the
announcement dates. This platform's global cache has neither, so — as with
`pit_global.py` — we implement the honest, price-only version that DOES generalise
to all 19 markets: earnings days are the canonical **high-volume return-jump** days,
so we detect those as announcement *proxies*, take the sign/size of the event-day
market-adjusted return as the *surprise*, and measure the subsequent **cumulative
abnormal return (CAR)** drift. The event is defined at t and the drift is measured
strictly after t, so the study is lookahead-free.

Two outputs:
  * an **event study** — average forward CAR grouped by surprise quantile: PEAD
    predicts a monotone increasing curve (positive-surprise names drift up); and
  * a **current signal** — for each stock's most recent event still inside the drift
    window, a 0–100 PEAD score (surprise sign × magnitude × time-decay), exported for
    `meta_screen.py --pead`.

Note on the US: for the US, EDGAR filing dates + YoY earnings change give a genuine
point-in-time SUE (standardised unexpected earnings); `sue()` is provided for that
path and unit-tested. The default CLI uses the price-proxy across all markets.

Usage:
  python pead_factor.py --market US --horizon 60      # event study + top signals
  python pead_factor.py --all --out pead.csv          # signal export for meta_screen
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


# ── pure event-study core ─────────────────────────────────────────────────────
def sue(actual: float, expected: float, std: float) -> float:
    """Standardised Unexpected Earnings = (actual − expected) / stdev(surprises).
    The textbook PEAD sort variable (used on the US EDGAR path)."""
    if not std or np.isnan(std):
        return np.nan
    return float((actual - expected) / std)


def market_adjust(stock_ret: pd.Series, market_ret: pd.Series, beta: float = 1.0) -> pd.Series:
    """Abnormal return = stock − beta·market (market-adjusted event-study returns)."""
    m = market_ret.reindex(stock_ret.index)
    return stock_ret - beta * m


def car(abnormal: pd.Series, start: int, end: int) -> float:
    """Cumulative abnormal return over the positional window [start, end] (inclusive
    of start, exclusive of end+1); tolerant of out-of-range slices."""
    seg = abnormal.iloc[max(0, start):end + 1]
    return float(seg.sum())


def detect_events(close: pd.Series, volume: pd.Series, lookback: int = 20,
                  vol_mult: float = 2.5, ret_z: float = 2.0, min_gap: int = 40) -> list:
    """Positional indices of earnings-announcement *proxy* days: a volume spike
    (> vol_mult × trailing average) coincident with a large move (|ret| > ret_z ×
    trailing stdev). Consecutive events within `min_gap` days are de-clustered."""
    c = close.astype(float)
    ret = c.pct_change()
    vol = volume.astype(float)
    vol_ma = vol.rolling(lookback).mean()
    ret_sd = ret.rolling(lookback).std()
    hit = (vol > vol_mult * vol_ma) & (ret.abs() > ret_z * ret_sd)
    idxs = [i for i, h in enumerate(hit.values) if h and i > lookback]
    kept = []
    for i in idxs:
        if not kept or i - kept[-1] >= min_gap:
            kept.append(i)
    return kept


def event_surprise(abnormal: pd.Series, ev: int, half: int = 1) -> float:
    """Surprise = CAR over the tight announcement window [ev−half, ev+half]."""
    return car(abnormal, ev - half, ev + half)


def pead_score(surprise: float, days_since: int, horizon: int = 60, halflife: int = 20) -> float:
    """Current tradeable signal in [0,100]: the sign of the surprise, scaled by an
    exponential time-decay across the drift window (drift is strongest just after the
    event and fades by `horizon`). 50 = neutral; >50 positive-surprise still drifting."""
    if days_since < 0 or days_since > horizon or np.isnan(surprise):
        return 50.0
    decay = 0.5 ** (days_since / halflife)
    raw = np.tanh(surprise * 8.0) * decay        # squ–1..1, scaled so ~12% move ≈ saturate
    return float(50.0 + 50.0 * raw)


def drift_by_surprise(events: pd.DataFrame, q: int = 5) -> pd.DataFrame:
    """Average forward CAR grouped by surprise quantile — the PEAD curve. `events`
    needs columns surprise, fwd_car. PEAD ⇒ mean fwd_car increases across quantiles."""
    d = events.dropna(subset=["surprise", "fwd_car"]).copy()
    if len(d) < q * 3:
        return pd.DataFrame()
    d["bucket"] = pd.qcut(d["surprise"], q, labels=[f"Q{i}" for i in range(1, q + 1)],
                          duplicates="drop")
    g = d.groupby("bucket")["fwd_car"].agg(mean="mean", median="median", n="count").reset_index()
    g["mean%"] = (g["mean"] * 100).round(2)
    g["median%"] = (g["median"] * 100).round(2)
    return g[["bucket", "mean%", "median%", "n"]]


def monotonicity(curve: pd.DataFrame) -> float:
    """Spearman-style monotonicity of the PEAD curve (+1 = perfectly increasing =
    strong PEAD). Summarises whether higher surprise ⇒ higher forward drift."""
    import marketdata
    return marketdata.monotonicity(curve, "mean%")


# ── data assembly (offline, prices) ───────────────────────────────────────────
def _market_wide(market: str):
    p = os.path.join(SEED, f"cleaned_long_{market}.parquet")
    if not os.path.exists(p):
        return None, None
    px = pd.read_parquet(p)
    close = px.pivot_table(index="Date", columns="Symbol", values="Close", aggfunc="last").astype(float)
    vol = px.pivot_table(index="Date", columns="Symbol", values="Volume", aggfunc="last").astype(float)
    return close, vol


DAILY_CLIP = 0.25        # clip abnormal returns to ±25%/day so one bad print can't blow up a 60d CAR
SURPRISE_CAP = 0.40      # events with a >40% one-day move are almost always splits/glitches, not earnings
MIN_HISTORY = 250        # need ~1y of data
LIQ_QUANTILE = 0.60      # keep only the top 40% by median $-volume within a market (clean, tradeable)


def _liquid_symbols(close: pd.DataFrame, vol: pd.DataFrame) -> list:
    """The tradeable universe — delegates to the shared marketdata filter."""
    import marketdata
    return marketdata.liquid_symbols(close, vol, quantile=LIQ_QUANTILE, min_history=MIN_HISTORY)


def scan_market(market: str, horizon: int = 60) -> tuple:
    """Detect events for every liquid stock in a market, returning (events_df,
    signals_df). events_df: one row per historical event (surprise, forward CAR) for
    the study. signals_df: one row per stock with its current PEAD score."""
    close, vol = _market_wide(market)
    if close is None:
        return pd.DataFrame(), pd.DataFrame()
    symbols = _liquid_symbols(close, vol)
    mkt = close[symbols].pct_change().mean(axis=1)     # equal-weight proxy over the clean set
    ev_rows, sig_rows = [], []
    for sym in symbols:
        c = close[sym].dropna()
        if len(c) < MIN_HISTORY:
            continue
        v = vol[sym].reindex(c.index)
        # abnormal returns, clipped per-day so glitches can't dominate a cumulative sum
        abn = market_adjust(c.pct_change(), mkt.reindex(c.index)).clip(-DAILY_CLIP, DAILY_CLIP)
        events = detect_events(c, v)
        last_pos = len(c) - 1
        for ev in events:
            surprise = event_surprise(abn, ev)
            if abs(surprise) > SURPRISE_CAP:               # split/glitch, not an earnings surprise
                continue
            if ev + horizon <= len(abn) - 1:               # only events with a COMPLETE drift window
                fwd = car(abn, ev + 2, ev + horizon)
                ev_rows.append({"market": market, "ticker": sym,
                                "surprise": surprise, "fwd_car": fwd})
        # current signal from the most recent (non-glitch) event still in the drift window
        recent = [e for e in events if abs(event_surprise(abn, e)) <= SURPRISE_CAP]
        if recent:
            ev = recent[-1]
            days_since = last_pos - ev
            s = event_surprise(abn, ev)
            sig_rows.append({"market": market, "ticker": sym, "surprise": s,
                             "days_since": int(days_since),
                             "pead_score": round(pead_score(s, days_since, horizon), 1)})
    return pd.DataFrame(ev_rows), pd.DataFrame(sig_rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default=None, help="market code, e.g. US, JP; default: all")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--horizon", type=int, default=60, help="drift window in trading days")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--out", default=None, help="write ticker,pead_score CSV for meta_screen")
    args = ap.parse_args()

    markets = (marketdata.market_list()
               if (args.all or not args.market) else [args.market])

    all_ev, all_sig = [], []
    for m in markets:
        ev, sig = scan_market(m, args.horizon)
        if not ev.empty:
            all_ev.append(ev); all_sig.append(sig)
    if not all_ev:
        raise SystemExit("no events detected (missing price parquets?)")
    events = pd.concat(all_ev, ignore_index=True)
    signals = pd.concat(all_sig, ignore_index=True)

    curve = drift_by_surprise(events)
    print(f"\n=== PEAD EVENT STUDY — {', '.join(markets) if len(markets)<=3 else str(len(markets))+' markets'} "
          f"({len(events)} earnings-proxy events, horizon {args.horizon}d) ===")
    if not curve.empty:
        print("  forward CAR by surprise quantile (Q1=most negative surprise, "
              f"Q{len(curve)}=most positive):")
        print(curve.to_string(index=False))
        mono = monotonicity(curve)
        verdict = ("PEAD PRESENT (positive surprises drift up, monotone)" if mono and mono > 0.7
                   else "weak/absent (no clean monotone drift)")
        print(f"  monotonicity = {mono:.2f}  ->  {verdict}")
        print(f"  long-short (Q{len(curve)}−Q1) forward CAR = "
              f"{curve['mean%'].iloc[-1] - curve['mean%'].iloc[0]:.2f}%")

    print(f"\n=== current PEAD signals — top {args.top} (recent positive surprises still drifting) ===")
    top = signals.sort_values("pead_score", ascending=False).head(args.top)
    print(f"  {'mkt':4}{'ticker':14}{'surprise%':>10}{'days_since':>11}{'pead':>7}")
    for _, r in top.iterrows():
        print(f"  {str(r['market']):4}{str(r['ticker']):14}"
              f"{r['surprise']*100:>10.2f}{int(r['days_since']):>11}{r['pead_score']:>7.1f}")

    if args.out:
        signals[["ticker", "pead_score"]].to_csv(args.out, index=False)
        print(f"\n  wrote {args.out} (ticker,pead_score for meta_screen --pead)", file=sys.stderr)


if __name__ == "__main__":
    main()
