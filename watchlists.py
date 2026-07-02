#!/usr/bin/env python3
"""
watchlists.py
-------------
Produces two SEPARATE, ranked shortlists — answering different questions from
different data — plus their intersection:

  1. FUNDAMENTALLY STRONG  — the AFP/QMJ quality score (quality_factor.py):
     profitability / growth / safety / payout, gated on ROE & leverage. "Is this a
     good business?"  (source: fundamentals_cache.db)

  2. BEING ACCUMULATED     — the Darvas volume-acquisition monitor (darvas_volume.py):
     names coiling in a box while volume is quietly acquired (OBV/CMF/up-down volume
     rising, price pinned). "Is someone building a position right now?"  (source:
     daily OHLC)

They come from different universes (fundamentals cover index heavyweights; the
accumulation scan covers every liquid name), so each list stands on its own. The
optional intersection (`--both`) is the "good business *and* being accumulated"
shortlist.

Pure filter/merge helpers are unit-tested; the CLI assembles the lists offline.

Usage:
  python watchlists.py --market US --top 20
  python watchlists.py --all --both
  python watchlists.py --market US --csv watch      # -> watch_strong.csv, watch_accum.csv
"""

from __future__ import annotations

import argparse
import os
import sys
import warnings

import numpy as np
import pandas as pd

import marketdata

warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = os.path.expanduser("~/Downloads/code/python_files/cache_seed")

MIN_QUALITY = 60.0        # quality-score percentile floor for "fundamentally strong"
MIN_ACCUM = 0.5           # accumulation-score floor for "being accumulated"


# ── pure filter / merge core ──────────────────────────────────────────────────
from marketdata import clean_key  # cross-source join key (re-exported)


def strong_from_scores(scored: pd.DataFrame, min_quality: float = MIN_QUALITY) -> pd.DataFrame:
    """Fundamentally-strong list from a quality_factor-scored frame: keep names above
    the quality floor, ranked by quality; flag the classic strong profile (ROE & D/E)."""
    d = scored.dropna(subset=["quality_score"]).copy()
    d = d[d["quality_score"] >= min_quality]
    roe = pd.to_numeric(d.get("roe"), errors="coerce")
    de = pd.to_numeric(d.get("de"), errors="coerce")
    d["strong_profile"] = ((roe > 15) & (de < 1)).fillna(False)
    d["key"] = d["ticker"].map(clean_key)
    return d.sort_values("quality_score", ascending=False)


def accumulated_from_scan(scan: pd.DataFrame, min_accum: float = MIN_ACCUM) -> pd.DataFrame:
    """Being-accumulated list from a darvas_volume scan: names coiling IN the box with
    a positive accumulation score, ranked by accumulation."""
    d = scan[(scan["state"] == "in_box") & (scan["accumulation"] >= min_accum)].copy()
    d["key"] = d["ticker"].map(clean_key)
    return d.sort_values("accumulation", ascending=False)


def intersect(strong: pd.DataFrame, accum: pd.DataFrame) -> pd.DataFrame:
    """Names in BOTH lists (good business AND being accumulated), joined by key."""
    if strong.empty or accum.empty:
        return pd.DataFrame()
    s = strong[["key", "ticker", "market", "quality_score"]].rename(columns={"ticker": "ticker_f"})
    a = accum[["key", "ticker", "accumulation", "position", "cmf"]].rename(columns={"ticker": "ticker_a"})
    m = s.merge(a, on="key", how="inner")
    return m.sort_values(["quality_score", "accumulation"], ascending=False)


# ── data assembly (reuse the existing modules) ────────────────────────────────
def build_strong(markets) -> pd.DataFrame:
    import quality_factor as qf
    f = qf.load_fundamentals(markets)
    if f.empty:
        return pd.DataFrame()
    f = qf.attach_price_risk(f)
    scored = qf.score_universe(f, by_market=True)
    return strong_from_scores(scored)


def build_accumulated(markets) -> pd.DataFrame:
    import darvas_volume as dv
    scans = [dv.scan_market(m) for m in markets]
    scans = [s for s in scans if not s.empty]
    if not scans:
        return pd.DataFrame()
    return accumulated_from_scan(pd.concat(scans, ignore_index=True))


def _markets(args) -> list:
    if args.all or not args.market:
        return marketdata.market_list()
    return [args.market]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default=None, help="market code, e.g. US; default: all")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--both", action="store_true", help="also show the intersection")
    ap.add_argument("--csv", default=None, help="write <prefix>_strong.csv / _accum.csv")
    args = ap.parse_args()

    markets = _markets(args)
    strong = build_strong(markets)
    accum = build_accumulated(markets)

    print(f"\n================  FUNDAMENTALLY STRONG  ({len(strong)} names)  ================")
    print("  quality score (AFP/QMJ percentile) + ROE/D-E gate  ·  source: fundamentals")
    if strong.empty:
        print("  (no fundamentals for the selected market)")
    else:
        print(f"  {'mkt':4}{'ticker':13}{'QUAL':>6}{'roe':>7}{'de':>6}  {'★':>2}  sector")
        for _, r in strong.head(args.top).iterrows():
            star = "★" if r["strong_profile"] else ""
            roe = pd.to_numeric(pd.Series([r.get('roe')]), errors='coerce').iloc[0]
            de = pd.to_numeric(pd.Series([r.get('de')]), errors='coerce').iloc[0]
            print(f"  {str(r['market']):4}{str(r['ticker']):13}{r['quality_score']:>6.1f}"
                  f"{(roe if pd.notna(roe) else float('nan')):>7.1f}{(de if pd.notna(de) else float('nan')):>6.2f}"
                  f"  {star:>2}  {str(r.get('sector',''))[:26]}")

    print(f"\n================  BEING ACCUMULATED  ({len(accum)} names)  ================")
    print("  coiling in a Darvas box + volume acquired (OBV/CMF/up-down vol)  ·  source: daily OHLC")
    if accum.empty:
        print("  (no accumulation coils found)")
    else:
        print(f"  {'mkt':4}{'ticker':13}{'close':>9}{'pos':>6}{'OBV↗':>6}{'CMF':>7}"
              f"{'U/D':>6}{'ACC':>7}")
        for _, r in accum.head(args.top).iterrows():
            ud = r["ud_vol_ratio"]; ud_s = "inf" if not np.isfinite(ud) else f"{ud:.2f}"
            print(f"  {str(r['market']):4}{str(r['ticker']):13}{r['close']:>9.2f}"
                  f"{r['position']:>6.2f}{r['obv_trend']:>6.2f}{r['cmf']:>7.2f}"
                  f"{ud_s:>6}{r['accumulation']:>7.2f}")

    if args.both:
        both = intersect(strong, accum)
        print(f"\n================  BOTH — strong AND accumulated  ({len(both)} names)  ============")
        if both.empty:
            print("  (no overlap — the two universes differ; try --all)")
        else:
            print(f"  {'mkt':4}{'ticker':13}{'QUAL':>6}{'ACC':>7}{'pos':>6}")
            for _, r in both.head(args.top).iterrows():
                print(f"  {str(r['market']):4}{str(r['ticker_a']):13}{r['quality_score']:>6.1f}"
                      f"{r['accumulation']:>7.2f}{r['position']:>6.2f}")

    if args.csv:
        if not strong.empty:
            strong.head(args.top).to_csv(f"{args.csv}_strong.csv", index=False)
        if not accum.empty:
            accum.head(args.top).to_csv(f"{args.csv}_accum.csv", index=False)
        print(f"\n  wrote {args.csv}_strong.csv / {args.csv}_accum.csv", file=sys.stderr)


if __name__ == "__main__":
    main()
