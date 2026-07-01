#!/usr/bin/env python3
"""
dvm_engine.py
-------------
Trendlyne-style DVM scoring: Durability / Valuation / Momentum, each 0-100, with
the GGG/GGB/BBG composite classification, standard filters, and pre-built screens.

  Momentum (0-100)   — technical bullishness from RSI, MACD, DMA position, 52w-high
                       proximity, ADX/DMI, volume. Works on any market (OHLC only).
  Durability (0-100) — financial strength: avg ROE, low D/E, positive FCF, revenue
                       growth, Piotroski F. From point-in-time SEC EDGAR (US).
  Valuation (0-100)  — how much strength is priced in: earnings-yield rank (cheap=high).
  Classification     — D/V/M each Good(>=50)/Bad(<50) -> 3-letter code + Trendlyne label
                       (GGG Strong Performer, GGB Value Under Radar, BBG Momentum Trap…).

India-specific Trendlyne inputs (promoter holding/pledge, FII/DII, delivery volume)
need an Indian fundamentals feed (nsepython/Trendlyne API) and are marked N/A here;
Durability/Valuation are EDGAR-backed so are US-complete, Momentum is all-market.

Usage:
  python dvm_engine.py --market US --limit 400 --screen high_dvm
  python dvm_engine.py --market US --screen durable_momentum   # High Durability + High Momentum
"""

from __future__ import annotations

import argparse
import glob
import os
import sqlite3
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

LABELS = {  # Trendlyne DVM composite (D,V,M each G/B)
    "GGG": "Strong Performer", "GGB": "Value Under Radar", "GBG": "Expensive Durable Mover",
    "GBB": "Expensive Quality", "BGG": "Cheap Turnaround Mover", "BGB": "Deep Value / Watch",
    "BBG": "Momentum Trap", "BBB": "Weak / Avoid",
}


def momentum_score(df: pd.DataFrame) -> float:
    """0-100 technical bullishness composite."""
    if df is None or len(df) < 220:
        return np.nan
    c = df["Close"].astype(float); v = df["Volume"].astype(float)
    d = c.diff()
    rsi = (100 - 100 / (1 + d.clip(lower=0).rolling(14).mean() /
                        (-d.clip(upper=0)).rolling(14).mean().replace(0, np.nan))).iloc[-1]
    ema12 = c.ewm(span=12).mean(); ema26 = c.ewm(span=26).mean()
    macd = ema12 - ema26; sig = macd.ewm(span=9).mean()
    macd_hist = (macd - sig).iloc[-1]
    dma50 = c.rolling(50).mean().iloc[-1]; dma200 = c.rolling(200).mean().iloc[-1]
    px = c.iloc[-1]
    hi52 = c.rolling(252).max().iloc[-1]
    # ADX/DMI (14)
    up = df["High"].astype(float).diff(); dn = -df["Low"].astype(float).diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0); minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = (df["High"].astype(float) - df["Low"].astype(float)).rolling(14).mean()
    pdi = 100 * pd.Series(plus, index=df.index).rolling(14).mean() / tr
    mdi = 100 * pd.Series(minus, index=df.index).rolling(14).mean() / tr
    adx = (100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)).rolling(14).mean().iloc[-1]
    volr = (v.iloc[-1] / v.rolling(20).mean().iloc[-1]) if v.rolling(20).mean().iloc[-1] else 1.0

    subs = []
    subs.append(min(100, max(0, rsi if rsi <= 70 else 70 - (rsi - 70) * 2)))   # RSI (penalise overbought)
    subs.append(100 if macd_hist > 0 else 25)                                  # MACD histogram sign
    subs.append(100 if px > dma50 > dma200 else (60 if px > dma200 else 20))   # trend stack
    subs.append(min(100, max(0, 100 - (hi52 / px - 1) * 300)))                 # 52w-high proximity
    subs.append(min(100, max(0, (adx if pd.notna(adx) else 20) * 2)))          # trend strength
    subs.append(min(100, 50 * volr))                                           # volume thrust
    return float(np.mean(subs))


def durability_score(ticker: str, date: str):
    """0-100 financial strength from point-in-time EDGAR, or None (no fundamentals)."""
    from pit_fundamentals import as_of, piotroski_asof, _g
    d = as_of(ticker, date)
    if not d or _g(d["ni"]) is None:
        return None, {}
    roe = np.mean(d["roe_hist"]) if d["roe_hist"] else None
    de = d["de"]; fcf = (d["cfo"] - d["capex"]) if (d["cfo"] is not None and d["capex"] is not None) else None
    rev_g = (_g(d["rev"]) is not None and _g(d["rev"], -1) is not None and _g(d["rev"]) > _g(d["rev"], -1))
    F, _ = piotroski_asof(ticker, date)
    subs = []
    subs.append(90 if (roe and roe > 20) else 70 if (roe and roe > 15) else 45 if (roe and roe > 8) else 15)
    subs.append(85 if (de is not None and de < 1) else 50 if (de is not None and de < 2) else 20)
    subs.append(80 if (fcf is not None and fcf > 0) else 20)
    subs.append(75 if rev_g else 35)
    subs.append((F / 9 * 100) if F is not None else 50)
    return float(np.mean(subs)), {"roe": roe, "de": de, "fcf_pos": fcf and fcf > 0, "piotroski": F}


def load_universe(market, limit):
    g = {"US": "data/us_full_scan/**/us_full_scan_*.xlsx",
         "India": "data/**/indian_full_scan_*.xlsx",
         "Japan": "data/japan_scan/**/japan_market_scan_*.xlsx",
         "Korea": "data/korea_scan/**/korea_market_scan_*.xlsx"}[market]
    a = pd.ExcelFile(sorted(glob.glob(os.path.expanduser(f"~/Downloads/{g}"), recursive=True))[-1]).parse("All_Stocks")
    if market == "India":
        s = (a["Symbol"].astype(str) + a["Suffix"].astype(str)).tolist()
    elif market in ("Japan", "Korea"):
        s = a["YF_Ticker"].astype(str).tolist()
    else:
        s = a["Symbol"].astype(str).tolist()
    s = [x for x in s if x and x != "nan"]
    return s[:limit] if limit else s


SCREENS = {
    "high_dvm":        lambda r: r["D"] and r["V"] and r["M"] and (r["D"] + r["V"] + r["M"]) / 3 >= 65,
    "durable_momentum": lambda r: (r["D"] or 0) >= 60 and r["M"] >= 60,          # High Durability + High Momentum
    "value_under_radar": lambda r: (r["D"] or 0) >= 55 and (r["V"] or 0) >= 55 and r["M"] < 50,
    "momentum_breakout": lambda r: r["M"] >= 75,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="US", choices=["US", "India", "Japan", "Korea"])
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--min-dollar-vol", type=float, default=2e6)
    ap.add_argument("--screen", choices=list(SCREENS), default="high_dvm")
    ap.add_argument("--db", default="dvm_scores.db")
    args = ap.parse_args()

    from market_store import cached_download
    from datetime import date
    tickers = load_universe(args.market, args.limit)
    print(f"loading {len(tickers)} {args.market} tickers (Cassandra cache)…", file=sys.stderr)
    ohlc = cached_download(tickers, years=5)
    ohlc = {t: df for t, df in ohlc.items()
            if len(df) > 250 and (df["Close"] * df["Volume"]).tail(252).median() >= args.min_dollar_vol}
    print(f"{len(ohlc)} liquid stocks scored", file=sys.stderr)

    today = str(date.today())
    ey = {}  # earnings yield for valuation ranking
    rows = []
    for t, df in ohlc.items():
        M = momentum_score(df)
        if pd.isna(M):
            continue
        D, det = (durability_score(t, today) if args.market == "US" else (None, {}))
        # earnings yield for valuation (US)
        eyv = None
        if args.market == "US":
            from pit_fundamentals import as_of, _g
            f = as_of(t, today)
            if f and _g(f["ni"]) and f.get("shares"):
                mcap = df["Close"].astype(float).iloc[-1] * f["shares"]
                if mcap > 0:
                    eyv = _g(f["ni"]) / mcap
        ey[t] = eyv
        rows.append({"ticker": t, "M": round(M, 1), "D": round(D, 1) if D else None, "_ey": eyv, "det": det})

    # Valuation = cross-sectional earnings-yield rank (cheap = high score)
    valid_ey = pd.Series({t: v for t, v in ey.items() if v is not None})
    vrank = valid_ey.rank(pct=True) * 100 if len(valid_ey) else pd.Series(dtype=float)
    for r in rows:
        r["V"] = round(float(vrank[r["ticker"]]), 1) if r["ticker"] in vrank.index else None

    def g(x):
        return "G" if (x is not None and x >= 50) else "B"
    for r in rows:
        code = g(r["D"]) + g(r["V"]) + g(r["M"])
        r["code"] = code; r["label"] = LABELS.get(code, "-")

    # apply chosen screen
    hits = [r for r in rows if SCREENS[args.screen](r)]
    hits.sort(key=lambda r: (r["M"] + (r["D"] or 0) + (r["V"] or 0)), reverse=True)

    conn = sqlite3.connect(args.db); conn.execute("PRAGMA journal_mode=DELETE;")
    conn.execute("DROP TABLE IF EXISTS dvm")
    conn.execute("CREATE TABLE dvm(ticker TEXT, market TEXT, D REAL, V REAL, M REAL, code TEXT, label TEXT)")
    conn.executemany("INSERT INTO dvm VALUES (?,?,?,?,?,?,?)",
                     [(r["ticker"], args.market, r["D"], r["V"], r["M"], r["code"], r["label"]) for r in rows])
    conn.commit(); conn.close()

    from collections import Counter
    print(f"\n=== DVM SCORES — {args.market} ({len(rows)} stocks) ===")
    print("  classification distribution:",
          dict(Counter(r["code"] for r in rows).most_common()))
    print(f"\n=== SCREEN: {args.screen} -> {len(hits)} hits ===")
    print(f"  {'ticker':10}{'D':>6}{'V':>6}{'M':>6}  code  label")
    for r in hits[:15]:
        print(f"  {r['ticker']:10}{str(r['D']):>6}{str(r['V']):>6}{r['M']:>6}  {r['code']}  {r['label']}")
    print(f"\n  saved all scores to {args.db}")


if __name__ == "__main__":
    main()
