#!/usr/bin/env python3
"""
dvm_global.py
-------------
Extends the Trendlyne-style DVM / screening approach to all ~20 markets that have
local OHLC data (cache_seed/cleaned_long_*.parquet), using the Trendlyne technical
metrics as filter criteria. Fully local (no network), so it runs anywhere.

Per stock (from OHLC): Momentum score (0-100) + the Trendlyne technical filters —
RSI(14), MACD histogram, DMA-stack (50/200), price-vs-52w-high, ADX/DMI, volume
thrust, beta (vs equal-weight market). Durability/Valuation need fundamentals and
are only available for US (EDGAR) — global DVM here is Momentum-led with the
technical filters as the screen, which is the cross-market-computable subset.

Screens (Trendlyne pre-built types) applied as filter criteria:
  momentum_breakout : M>=70 & within 10% of 52w-high & ADX>=25 & volume thrust
  high_momentum     : M>=75
  golden_crossover  : 50DMA crossed above 200DMA in last 5 sessions
  uptrend_quality   : above 200DMA & RSI 50-70 & ADX>=20

Output: compact SQLite (all metrics) + per-market screen-hit summary.

Usage:
  python dvm_global.py --screen momentum_breakout
  python dvm_global.py --markets US JP KR --screen high_momentum
"""

from __future__ import annotations

import argparse
import glob
import os
import sqlite3
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

SEED = os.path.expanduser("~/Downloads/code/python_files/cache_seed")
MIN_BARS = 200


def _tech(c: pd.Series, h: pd.Series, low: pd.Series, v: pd.Series, mkt: pd.Series):
    """Trendlyne technical metrics + momentum score for one stock. Returns dict or None."""
    if len(c) < MIN_BARS:
        return None
    d = c.diff()
    rsi = (100 - 100 / (1 + d.clip(lower=0).rolling(14).mean() /
                        (-d.clip(upper=0)).rolling(14).mean().replace(0, np.nan))).iloc[-1]
    ema12 = c.ewm(span=12).mean(); ema26 = c.ewm(span=26).mean()
    macd = ema12 - ema26; macd_hist = (macd - macd.ewm(span=9).mean()).iloc[-1]
    dma50 = c.rolling(50).mean(); dma200 = c.rolling(200).mean()
    px = c.iloc[-1]; hi52 = c.rolling(252, min_periods=150).max().iloc[-1]
    dist52 = (px / hi52 - 1) * 100
    gc = bool((dma50.iloc[-1] > dma200.iloc[-1]) and
              (dma50.iloc[-6] <= dma200.iloc[-6]) if len(dma50) > 6 else False)
    # ADX/DMI(14)
    up = h.diff(); dn = -low.diff()
    plus = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=c.index)
    minus = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=c.index)
    tr = (h - low).rolling(14).mean()
    pdi = 100 * plus.rolling(14).mean() / tr; mdi = 100 * minus.rolling(14).mean() / tr
    adx = (100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)).rolling(14).mean().iloc[-1]
    volr = (v.iloc[-1] / v.rolling(20).mean().iloc[-1]) if v.rolling(20).mean().iloc[-1] else 1.0
    # MFI(14) — Money Flow Index (Trendlyne's TechRSIMFIMACD uses it): volume-weighted RSI
    tp = (h + low + c) / 3
    rmf = tp * v
    pos = rmf.where(tp.diff() > 0, 0.0).rolling(14).sum()
    neg = rmf.where(tp.diff() < 0, 0.0).rolling(14).sum().replace(0, np.nan)
    mfi = (100 - 100 / (1 + pos / neg)).iloc[-1]
    sma50_above = bool(dma50.iloc[-1] > dma200.iloc[-1]) if pd.notna(dma200.iloc[-1]) else False
    # beta vs equal-weight market
    r = c.pct_change(); m = mkt.reindex(r.index)
    beta = (r.tail(200).cov(m.tail(200)) / m.tail(200).var()) if m.tail(200).var() else np.nan
    above200 = bool(px > dma200.iloc[-1]) if pd.notna(dma200.iloc[-1]) else False

    subs = [
        min(100, max(0, rsi if rsi <= 70 else 70 - (rsi - 70) * 2)),
        100 if macd_hist > 0 else 25,
        100 if (px > dma50.iloc[-1] > dma200.iloc[-1]) else (60 if above200 else 20),
        min(100, max(0, 100 + dist52 * 3)),                    # 52w-high proximity
        min(100, max(0, (adx if pd.notna(adx) else 20) * 2)),
        min(100, 50 * volr),
    ]
    M = float(np.mean(subs))
    return {"M": round(M, 1), "rsi": round(float(rsi), 1) if pd.notna(rsi) else None,
            "mfi": round(float(mfi), 1) if pd.notna(mfi) else None,
            "adx": round(float(adx), 1) if pd.notna(adx) else None,
            "dist_52w": round(float(dist52), 1), "above_200dma": above200,
            "golden_cross": gc, "sma50_above_200": sma50_above,
            "macd_bull": bool(macd_hist > 0), "vol_ratio": round(float(volr), 2),
            "beta": round(float(beta), 2) if pd.notna(beta) else None}


SCREENS = {
    "momentum_breakout": lambda r: r["M"] >= 70 and r["dist_52w"] >= -10 and (r["adx"] or 0) >= 25 and r["vol_ratio"] >= 1.2,
    "high_momentum":     lambda r: r["M"] >= 75,
    "golden_crossover":  lambda r: r["golden_cross"],
    "uptrend_quality":   lambda r: r["above_200dma"] and 50 <= (r["rsi"] or 0) <= 70 and (r["adx"] or 0) >= 20,
    # Trendlyne public "TechRSIMFIMACD" technical screener
    "trendlyne_technical": lambda r: 50 <= (r["rsi"] or 0) <= 70 and (r["mfi"] or 0) >= 50 and r["macd_bull"],
    # Trendlyne public "sma50-above-sma200" moving-average screener
    "sma_golden":        lambda r: r["sma50_above_200"] and r["above_200dma"],
}


def process_market(mkt: str, screen: str):
    """Vectorised (columnar) technical metrics for a whole market at once — pandas
    rolling/ewm operate per-column, so RSI/MACD/DMA/MFI/ADX/beta compute across all
    tickers in a handful of ops instead of a per-ticker Python loop (see PERFORMANCE.md)."""
    df = pd.read_parquet(os.path.join(SEED, f"cleaned_long_{mkt}.parquet")).sort_values("Date")
    piv = lambda col: df.pivot_table(index="Date", columns="Symbol", values=col, aggfunc="last").sort_index()
    c, h, low, v = piv("Close"), piv("High"), piv("Low"), piv("Volume")

    d = c.diff()
    rsi = 100 - 100 / (1 + d.clip(lower=0).rolling(14).mean() /
                       (-d.clip(upper=0)).rolling(14).mean().replace(0, np.nan))
    macd = c.ewm(span=12).mean() - c.ewm(span=26).mean()
    macd_hist = macd - macd.ewm(span=9).mean()
    dma50, dma200 = c.rolling(50).mean(), c.rolling(200).mean()
    dist52 = (c / c.rolling(252, min_periods=150).max() - 1) * 100
    tp = (h + low + c) / 3; rmf = tp * v; tpd = tp.diff()
    mfi = 100 - 100 / (1 + rmf.where(tpd > 0, 0.0).rolling(14).sum() /
                       rmf.where(tpd < 0, 0.0).rolling(14).sum().replace(0, np.nan))
    up, dn = h.diff(), -low.diff()
    tr = (h - low).rolling(14).mean()
    pdi = 100 * up.where((up > dn) & (up > 0), 0.0).rolling(14).mean() / tr
    mdi = 100 * dn.where((dn > up) & (dn > 0), 0.0).rolling(14).mean() / tr
    adx = (100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)).rolling(14).mean()
    volr = v / v.rolling(20).mean()

    # beta vs equal-weight market over the last 200 returns (vectorised covariance)
    R = c.pct_change(); m = R.mean(axis=1)
    R2, m2 = R.tail(200), m.tail(200)
    beta = R2.sub(R2.mean()).mul(m2 - m2.mean(), axis=0).mean() / (m2.var() or np.nan)

    # last-row snapshot (Series indexed by ticker)
    idx = c.columns
    px, RSI, MFI, ADX = c.iloc[-1], rsi.iloc[-1], mfi.iloc[-1], adx.iloc[-1]
    D50, D200, DIST, MH, VOLR = dma50.iloc[-1], dma200.iloc[-1], dist52.iloc[-1], macd_hist.iloc[-1], volr.iloc[-1]
    above200 = px > D200
    sma_above = D50 > D200
    gc = ((dma50 > dma200) & (dma50.shift(1) <= dma200.shift(1))).iloc[-1]

    def clip01(s): return np.minimum(100, np.maximum(0, s))
    subs = pd.DataFrame({
        1: clip01(np.where(RSI.values <= 70, RSI.values, 70 - (RSI.values - 70) * 2)),
        2: np.where(MH.values > 0, 100, 25),
        3: np.where((px.values > D50.values) & (D50.values > D200.values), 100,
                    np.where(above200.values, 60, 20)),
        4: clip01(100 + DIST.values * 3),
        5: clip01(np.nan_to_num(ADX.values, nan=20.0) * 2),
        6: np.minimum(100, 50 * VOLR.values),
    }, index=idx)
    M = subs.mean(axis=1)

    res = pd.DataFrame({
        "M": M.round(1), "rsi": RSI.round(1), "mfi": MFI.round(1), "adx": ADX.round(1),
        "dist_52w": DIST.round(1), "above_200dma": above200, "golden_cross": gc,
        "sma50_above_200": sma_above, "macd_bull": MH > 0, "vol_ratio": VOLR.round(2),
        "beta": beta.round(2),
    }).dropna(subset=["M"])
    res = res[c.count().reindex(res.index) >= MIN_BARS]     # >=200 bars, matches the loop version
    res["market"] = mkt; res["ticker"] = res.index
    rows = res.to_dict("records")
    hits = [r for r in rows if SCREENS[screen](r)]
    return rows, hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", nargs="*", default=None, help="default: all with data")
    ap.add_argument("--screen", choices=list(SCREENS), default="momentum_breakout")
    ap.add_argument("--db", default="dvm_global.db")
    ap.add_argument("--workers", type=int, default=os.cpu_count(),
                    help="parallel market workers (CPU-bound; default = all cores)")
    args = ap.parse_args()

    all_mkts = sorted(os.path.basename(p).replace("cleaned_long_", "").replace(".parquet", "")
                      for p in glob.glob(os.path.join(SEED, "cleaned_long_*.parquet")))
    markets = args.markets or all_mkts
    print(f"markets with data: {all_mkts}", file=sys.stderr)

    conn = sqlite3.connect(args.db); conn.execute("PRAGMA journal_mode=DELETE;")
    conn.execute("DROP TABLE IF EXISTS dvm_global")
    conn.execute("""CREATE TABLE dvm_global(market TEXT, ticker TEXT, M REAL, rsi REAL,
        mfi REAL, adx REAL, dist_52w REAL, above_200dma INT, golden_cross INT,
        sma50_above_200 INT, macd_bull INT, vol_ratio REAL, beta REAL)""")

    summary = []
    # markets processed in parallel across cores (CPU-bound per-ticker compute);
    # SQLite writes stay in the main process. ~Ncores speedup.
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(process_market, m, args.screen): m for m in markets}
        for fut in as_completed(futs):
            mkt = futs[fut]
            rows, hits = fut.result()
            conn.executemany("INSERT INTO dvm_global VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(r["market"], r["ticker"], r["M"], r["rsi"], r["mfi"], r["adx"], r["dist_52w"],
                  int(r["above_200dma"]), int(r["golden_cross"]), int(r["sma50_above_200"]),
                  int(r["macd_bull"]), r["vol_ratio"], r["beta"]) for r in rows])
            conn.commit()
            avgM = round(float(np.mean([r["M"] for r in rows])), 1) if rows else 0
            summary.append((mkt, len(rows), len(hits), avgM))
            print(f"  {mkt}: scored {len(rows):>5} | {args.screen} hits {len(hits):>4} | avg momentum {avgM}",
                  file=sys.stderr, flush=True)

    conn.close()
    print(f"\n=== GLOBAL DVM / TRENDLYNE SCREEN: {args.screen} ({len(markets)} markets) ===")
    print(f"  {'market':8}{'scored':>8}{'hits':>7}{'avgM':>7}")
    for mkt, n, h, a in sorted(summary, key=lambda x: -x[2]):
        print(f"  {mkt:8}{n:>8}{h:>7}{a:>7}")
    tot_scored = sum(x[1] for x in summary); tot_hits = sum(x[2] for x in summary)
    print(f"  {'TOTAL':8}{tot_scored:>8}{tot_hits:>7}")
    print(f"\n  all metrics saved to {args.db} ({os.path.getsize(args.db)//1024} KB)")


if __name__ == "__main__":
    main()
