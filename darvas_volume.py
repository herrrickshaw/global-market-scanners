#!/usr/bin/env python3
"""
darvas_volume.py
----------------
A Darvas-box monitor tuned to detect **volume acquisition** (stealth accumulation)
and HFT footprints inside the box — the classic "someone is quietly building a
position while the price coils, before it breaks out" setup.

Darvas box: price consolidates in a range with a ceiling (box top = a recent high
that held) and a floor (box bottom = a low that held). A breakout above the top on
rising volume is the entry. Here we overlay, *inside* the box:

  Volume acquisition (accumulation) — is volume being absorbed while price ranges?
    OBV trend, Chaikin Accumulation/Distribution trend, Chaikin Money Flow (CMF),
    up-day vs down-day volume ratio, and the raw volume trend.
  HFT / microstructure footprint (from hft_selection proxies)
    low Kaufman efficiency ratio  = price pinned in the box (mark-time accumulation)
    positive volume autocorrelation = a persistent, worked order (not one-off prints)

Design rule (honoured): the box is formed EXCLUDING the current bar, so a breakout
or breakdown by the current bar is detectable (including the current bar in the box
would make a breakout impossible by construction).

Outputs a monitor of names (a) coiling in a tight box while (b) volume is being
acquired — ranked by an accumulation score — plus fresh volume-confirmed breakouts.

Usage:
  python darvas_volume.py --market US --top 15
  python darvas_volume.py --all --state breakout
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

LOOKBACK = 40        # bars used to form the box + measure accumulation
CONFIRM = 3          # bars a high/low must hold to define the box edge (Darvas)
VOL_SURGE = 1.5      # breakout volume must exceed VOL_SURGE × box-average volume


# ── volume-acquisition primitives (pure) ──────────────────────────────────────
def obv(close, volume) -> np.ndarray:
    """On-Balance Volume: cumulative signed volume (+ on up-closes, − on down)."""
    c = np.asarray(close, dtype=float); v = np.asarray(volume, dtype=float)
    sign = np.sign(np.diff(c, prepend=c[0]))
    return np.cumsum(sign * v)


def chaikin_ad(high, low, close, volume) -> np.ndarray:
    """Chaikin Accumulation/Distribution line: cumulative money-flow volume, where
    the money-flow multiplier = ((C−L) − (H−C)) / (H−L) ∈ [−1,1]."""
    h, l, c, v = (np.asarray(x, dtype=float) for x in (high, low, close, volume))
    rng = np.where((h - l) > 0, h - l, np.nan)
    mfm = ((c - l) - (h - c)) / rng
    mfm = np.nan_to_num(mfm)
    return np.cumsum(mfm * v)


def chaikin_money_flow(high, low, close, volume, period: int | None = None) -> float:
    """CMF over the window: Σ(money-flow volume) / Σ(volume) ∈ [−1,1]. >0 = net
    accumulation (closes in the upper part of the range on volume)."""
    h, l, c, v = (np.asarray(x, dtype=float) for x in (high, low, close, volume))
    if period:
        h, l, c, v = h[-period:], l[-period:], c[-period:], v[-period:]
    rng = np.where((h - l) > 0, h - l, np.nan)
    mfm = np.nan_to_num(((c - l) - (h - c)) / rng)
    vt = v.sum()
    return float((mfm * v).sum() / vt) if vt > 0 else np.nan


def up_down_volume_ratio(close, volume) -> float:
    """Total volume on up-closes / total volume on down-closes. >1 = accumulation."""
    c = np.asarray(close, dtype=float); v = np.asarray(volume, dtype=float)
    d = np.diff(c, prepend=c[0])
    up = v[d > 0].sum(); dn = v[d < 0].sum()
    return float(up / dn) if dn > 0 else (np.inf if up > 0 else np.nan)


def trend_corr(x) -> float:
    """Scale-free trend: correlation of the series with time ∈ [−1,1] (rising = +)."""
    a = np.asarray(x, dtype=float)
    a = a[np.isfinite(a)]
    if len(a) < 3 or np.std(a) == 0:
        return np.nan
    return float(np.corrcoef(a, np.arange(len(a)))[0, 1])


# ── Darvas box (pure) ─────────────────────────────────────────────────────────
def darvas_box(high, low, lookback: int = LOOKBACK, confirm: int = CONFIRM,
               exclude_current: bool = True) -> dict:
    """Form the current Darvas box from highs/lows. Per the design rule the current
    bar is EXCLUDED from formation. box top = the highest high in the lookback that
    then held for `confirm` bars; box bottom = the lowest low from the top onward."""
    h = np.asarray(high, dtype=float); l = np.asarray(low, dtype=float)
    if exclude_current:
        h, l = h[:-1], l[:-1]
    if len(h) < confirm + 2:
        return {"top": np.nan, "bottom": np.nan, "len": 0}
    hw = h[-lookback:]; lw = l[-lookback:]
    top_i = int(np.nanargmax(hw))
    top = float(hw[top_i])
    # require the top held for `confirm` subsequent bars (none exceeded it)
    held = (len(hw) - top_i - 1) >= confirm and np.all(hw[top_i + 1:] <= top + 1e-9)
    bottom = float(np.nanmin(lw[top_i:])) if top_i < len(lw) else float(np.nanmin(lw))
    return {"top": top, "bottom": bottom, "len": len(hw) - top_i, "held": bool(held)}


def box_state(close_last: float, high_last: float, low_last: float, box: dict,
              vol_last: float, vol_avg: float) -> dict:
    """Classify the current bar vs the box: breakout / breakdown / in_box, its
    position in the box [0,1], and whether a breakout is volume-confirmed."""
    top, bottom = box["top"], box["bottom"]
    if not np.isfinite(top) or not np.isfinite(bottom) or top <= bottom:
        return {"state": "no_box", "position": np.nan, "vol_confirmed": False}
    if close_last > top:
        state = "breakout"
    elif close_last < bottom:
        state = "breakdown"
    else:
        state = "in_box"
    pos = (close_last - bottom) / (top - bottom)
    vol_confirmed = bool(vol_avg > 0 and vol_last >= VOL_SURGE * vol_avg)
    return {"state": state, "position": round(float(np.clip(pos, 0, 1.2)), 3),
            "vol_confirmed": vol_confirmed}


def accumulation_features(high, low, close, volume) -> dict:
    """The volume-acquisition signals over the window."""
    return {
        "obv_trend": trend_corr(obv(close, volume)),
        "ad_trend": trend_corr(chaikin_ad(high, low, close, volume)),
        "cmf": chaikin_money_flow(high, low, close, volume),
        "ud_vol_ratio": up_down_volume_ratio(close, volume),
        "vol_trend": trend_corr(volume),
    }


def _z(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan)
    sd = s.std(ddof=0)
    return (s - s.mean()) / sd if sd and not np.isnan(sd) else pd.Series(0.0, index=s.index)


def accumulation_score(feat: pd.DataFrame) -> pd.Series:
    """Cross-sectional composite: OBV/AD/volume up-trends + CMF + up/down volume,
    minus efficiency ratio (pinned price = mark-time accumulation). Higher = more
    volume being quietly acquired."""
    ud = pd.to_numeric(feat["ud_vol_ratio"], errors="coerce").replace([np.inf], np.nan)
    ud = np.log(ud.clip(lower=1e-6))                       # symmetric around 0 (ratio 1)
    comp = (_z(feat["obv_trend"]) + _z(feat["ad_trend"]) + _z(feat["cmf"])
            + _z(ud) + _z(feat["vol_trend"]) - _z(feat.get("eff_ratio", pd.Series(0, index=feat.index))))
    return comp.round(3)


# ── data assembly (offline) ───────────────────────────────────────────────────
def scan_market(market: str, lookback: int = LOOKBACK) -> pd.DataFrame:
    import liquidity_factor as lf
    import pead_factor as pf
    from hft_selection import efficiency_ratio, lag1_autocorr
    close, vol = lf._market_wide(market)
    if close is None:
        return pd.DataFrame()
    px = pd.read_parquet(os.path.join(SEED, f"cleaned_long_{market}.parquet"))
    high = px.pivot_table(index="Date", columns="Symbol", values="High", aggfunc="last").astype(float)
    low = px.pivot_table(index="Date", columns="Symbol", values="Low", aggfunc="last").astype(float)
    symbols = pf._liquid_symbols(close, vol)
    rows = []
    for s in symbols:
        c = close[s].dropna()
        if len(c) < lookback + 5:
            continue
        idx = c.index[-(lookback + 1):]                   # include current bar for the test
        h = high[s].reindex(idx); l = low[s].reindex(idx); v = vol[s].reindex(idx)
        cc = c.reindex(idx)
        box = darvas_box(h.values, l.values, lookback=lookback)
        if not np.isfinite(box["top"]):
            continue
        vol_avg = float(np.nanmean(v.values[:-1]))        # box-average volume (excl current)
        st = box_state(cc.values[-1], h.values[-1], l.values[-1], box, v.values[-1], vol_avg)
        # accumulation + HFT footprint over the box window (exclude current bar)
        acc = accumulation_features(h.values[:-1], l.values[:-1], cc.values[:-1], v.values[:-1])
        row = {"market": market, "ticker": s, "close": round(float(cc.values[-1]), 2),
               "box_top": round(box["top"], 2), "box_bottom": round(box["bottom"], 2),
               **st, **acc,
               "eff_ratio": efficiency_ratio(cc.values[:-1]),
               "vol_autocorr": lag1_autocorr(v.values[:-1])}
        rows.append(row)
    df = pd.DataFrame(rows)
    if not df.empty:
        df["accumulation"] = accumulation_score(df)
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default=None, help="market code, e.g. US, JP; default: all")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--lookback", type=int, default=LOOKBACK)
    ap.add_argument("--state", choices=["in_box", "breakout", "breakdown"], default="in_box",
                    help="which box state to monitor (default: in_box coils)")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    markets = ([f.split("cleaned_long_")[1].split(".")[0]
                for f in sorted(os.listdir(SEED)) if f.startswith("cleaned_long_")]
               if (args.all or not args.market) else [args.market])
    scans = [scan_market(m, args.lookback) for m in markets]
    scans = [s for s in scans if not s.empty]
    if not scans:
        raise SystemExit("no data (missing price parquets?)")
    df = pd.concat(scans, ignore_index=True)

    sub = df[df["state"] == args.state].copy()
    if args.state == "breakout":
        sub = sub[sub["vol_confirmed"]]                   # only volume-confirmed breakouts
    sub = sub.sort_values("accumulation", ascending=False)

    tag = ", ".join(markets) if len(markets) <= 3 else f"{len(markets)} markets"
    label = {"in_box": "COILING IN BOX — volume being acquired (stealth accumulation)",
             "breakout": "VOLUME-CONFIRMED BREAKOUTS (box top cleared on surging volume)",
             "breakdown": "BREAKDOWNS (box bottom lost)"}[args.state]
    print(f"\n=== DARVAS × VOLUME-ACQUISITION MONITOR — {tag} ===")
    print(f"    {label}  ·  {len(sub)} names")
    print(f"  {'mkt':4}{'ticker':12}{'close':>9}{'boxLo':>9}{'boxHi':>9}{'pos':>6}"
          f"{'OBV↗':>6}{'CMF':>7}{'U/D':>6}{'effR':>6}{'vAC':>6}{'ACC':>7}")
    for _, r in sub.head(args.top).iterrows():
        ud = r["ud_vol_ratio"]; ud_s = "inf" if not np.isfinite(ud) else f"{ud:.2f}"
        print(f"  {str(r['market']):4}{str(r['ticker']):12}{r['close']:>9.2f}"
              f"{r['box_bottom']:>9.2f}{r['box_top']:>9.2f}{r['position']:>6.2f}"
              f"{r['obv_trend']:>6.2f}{r['cmf']:>7.2f}{ud_s:>6}{r['eff_ratio']:>6.2f}"
              f"{r['vol_autocorr']:>6.2f}{r['accumulation']:>7.2f}")
    print("\n  pos=where close sits in the box (→1 = pressing the top). OBV↗/CMF/U-D>1 & low effR")
    print("  + positive vAC = volume acquired while price coils. Box excludes the current bar.")


if __name__ == "__main__":
    main()
