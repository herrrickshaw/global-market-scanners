#!/usr/bin/env python3
"""
pit_backtest.py
---------------
Does the fundamental gate earn its keep? 5-year, monthly-rebalanced, net-of-cost
US backtest comparing three arms on Darvas breakouts:

  A  Darvas breakout only
  B  Darvas breakout + Piotroski F-score >= 7          (point-in-time, no lookahead)
  C  Darvas breakout + F >= 7 + Coffee-Can  (= Triple-Hit)

Fundamentals are strictly point-in-time via pit_fundamentals.as_of (filed <= date).
Each month we equal-weight the arm's picks, hold to the next rebalance, and book
returns net of a US round-trip cost (0.10%). Benchmark = equal-weight whole universe.

Universe: liquid US large-caps (guaranteed EDGAR 10-K coverage). Survivorship
caveat: these are *current* large caps, so absolute returns skew optimistic — but
the A-vs-B-vs-C *comparison* (the actual question) is on a level field.

Usage:
  python pit_backtest.py                 # 5y, monthly
  python pit_backtest.py --years 5 --limit 120
"""

import argparse
import glob
import os
import sqlite3
import sys
import warnings

import numpy as np
import pandas as pd

from pit_fundamentals import piotroski_asof, coffeecan_asof, as_of, _g

warnings.filterwarnings("ignore")

US_COST = 0.10          # % round-trip (matches apply_costs.py US)
BREAKOUT_LB = 60        # Darvas breakout = new high vs prior 60 trading days

# Liquid US large-caps with reliable EDGAR 10-K history (S&P-100-ish).
UNIVERSE = ("AAPL MSFT NVDA AMZN GOOGL META AVGO TSLA JPM V UNH XOM JNJ WMT MA PG "
            "HD COST ORCL CVX MRK ABBV KO PEP BAC ADBE CRM AMD NFLX TMO LIN ACN MCD "
            "ABT CSCO DHR WFC TXN QCOM PM INTC INTU AMGN CAT IBM GE VZ NKE NOW UNP "
            "HON GS LOW SPGI BKNG MS BLK AXP SBUX PLD MDT GILD ADP TJX VRTX C SYK "
            "REGN CB MMC LMT BMY MDLZ ADI CI SO ETN ZTS DUK BSX SLB MO BDX ITW WM "
            "AON CME EQIX MU FDX NSC PNC USB EMR GD FCX APD ORLY MCO PH").split()


def load_scan_universe(limit=None):
    """Full US scan universe (all are SEC filers)."""
    hits = sorted(glob.glob(os.path.expanduser(
        "~/Downloads/data/us_full_scan/**/us_full_scan_*.xlsx"), recursive=True))
    if not hits:
        return []
    syms = pd.ExcelFile(hits[-1]).parse("All_Stocks")["Symbol"].astype(str).str.strip()
    syms = [s for s in syms.tolist() if s and s != "nan"]
    return syms[:limit] if limit else syms


def darvas_breakout(close: pd.Series, t) -> bool:
    """New high vs the prior BREAKOUT_LB closes (Darvas box breakout proxy)."""
    loc = close.index.get_loc(t)
    if loc < BREAKOUT_LB:
        return False
    prior = close.iloc[loc - BREAKOUT_LB:loc]
    return bool(close.iloc[loc] >= prior.max())


def metrics(rets: list) -> dict:
    if not rets:
        return {"n_months": 0}
    r = np.array(rets) / 100.0
    eq = np.cumprod(1 + r)
    ann = (eq[-1] ** (12 / len(r)) - 1) * 100
    sharpe = (r.mean() / r.std() * np.sqrt(12)) if r.std() > 0 else 0
    dd = ((eq - np.maximum.accumulate(eq)) / np.maximum.accumulate(eq)).min() * 100
    return {"n_months": len(r), "avg_mth%": round(r.mean() * 100, 3),
            "ann_return%": round(ann, 2), "hit_rate%": round((r > 0).mean() * 100, 1),
            "sharpe": round(sharpe, 2), "max_dd%": round(dd, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--universe", choices=["builtin", "scan"], default="builtin",
                    help="builtin=~100 large caps; scan=full US scan universe (~6200)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--min-dollar-vol", type=float, default=2e6,
                    help="liquidity filter: min median daily $-volume (default $2M)")
    ap.add_argument("--db", default="pit_backtest.db")
    args = ap.parse_args()

    import yfinance as yf
    if args.universe == "scan":
        tickers = load_scan_universe(args.limit)
    else:
        tickers = UNIVERSE[:(args.limit or len(UNIVERSE))]
    print(f"Downloading {len(tickers)} tickers ({args.years}y) in batches…",
          file=sys.stderr, flush=True)
    closes, liq = {}, {}
    for i in range(0, len(tickers), 250):
        batch = tickers[i:i + 250]
        try:
            data = yf.download(batch, period=f"{args.years}y", auto_adjust=True,
                               progress=False, group_by="ticker", threads=True)
        except Exception:
            continue
        for t in batch:
            try:
                df = data[t] if isinstance(data.columns, pd.MultiIndex) else data
                s = df["Close"].dropna()
                if len(s) > BREAKOUT_LB + 20:
                    closes[t] = s
                    dv = (df["Close"] * df["Volume"]).dropna()   # daily $-volume
                    liq[t] = float(dv.tail(252).median())        # median over last ~1y
            except Exception:
                continue
        print(f"  downloaded {min(i+250,len(tickers))}/{len(tickers)}; usable={len(closes)}",
              file=sys.stderr, flush=True)

    # ── liquidity filter ──────────────────────────────────────────────────────
    n_before = len(closes)
    closes = {t: s for t, s in closes.items() if liq.get(t, 0) >= args.min_dollar_vol}
    print(f"liquidity filter (median $-vol >= ${args.min_dollar_vol:,.0f}/day): "
          f"{n_before} -> {len(closes)} tradeable names", file=sys.stderr, flush=True)

    # monthly rebalance dates = first trading day of each month, need next month for return
    all_idx = sorted(set().union(*[s.index for s in closes.values()]))
    cal = pd.Series(1, index=pd.DatetimeIndex(all_idx))
    rebal = cal.resample("MS").first().index
    rebal = [d for d in rebal if d >= pd.Timestamp(all_idx[BREAKOUT_LB + 1])]

    arms = {"A_darvas": [], "B_darvas_F7": [], "C_triple_hit": [], "BENCH_all": []}
    picks_log = {"A_darvas": [], "B_darvas_F7": [], "C_triple_hit": []}

    for i in range(len(rebal) - 1):
        t0, t1 = rebal[i], rebal[i + 1]
        # snap to actual trading days
        breakout, held = [], []
        fwd_by_tkr = {}
        for tkr, s in closes.items():
            idx = s.index
            e0 = idx[idx.get_indexer([t0], method="ffill")[0]] if len(idx[idx <= t0]) else None
            e1 = idx[idx.get_indexer([t1], method="ffill")[0]] if len(idx[idx <= t1]) else None
            if e0 is None or e1 is None or e0 >= e1:
                continue
            fwd = (s.loc[e1] / s.loc[e0] - 1) * 100
            fwd_by_tkr[tkr] = fwd
            held.append(fwd)
            if darvas_breakout(s, e0):
                breakout.append(tkr)

        if held:
            arms["BENCH_all"].append(float(np.mean(held)) - US_COST)

        d = t0.date().isoformat()
        selA, selB, selC = [], [], []
        for tkr in breakout:
            selA.append(tkr)
            F, _ = piotroski_asof(tkr, d)
            if F is not None and F >= 7:
                selB.append(tkr)
                mcap = None
                dd = as_of(tkr, d)
                if dd and dd.get("shares"):
                    mcap = closes[tkr].loc[closes[tkr].index[closes[tkr].index <= t0][-1]] * dd["shares"]
                cc, _ = coffeecan_asof(tkr, d, mktcap=mcap)
                if cc:
                    selC.append(tkr)
        for arm, sel in (("A_darvas", selA), ("B_darvas_F7", selB), ("C_triple_hit", selC)):
            if sel:
                arms[arm].append(float(np.mean([fwd_by_tkr[x] for x in sel])) - US_COST)
                picks_log[arm].append(len(sel))
        print(f"  {d}: breakouts={len(selA)} F7={len(selB)} triple={len(selC)}",
              file=sys.stderr, flush=True)

    rows = []
    for arm, rets in arms.items():
        m = metrics(rets)
        m["arm"] = arm
        m["avg_picks"] = round(np.mean(picks_log.get(arm, [0]) or [0]), 1) if arm in picks_log else "all"
        rows.append(m)
    res = pd.DataFrame(rows)[["arm", "n_months", "avg_picks", "avg_mth%",
                              "ann_return%", "hit_rate%", "sharpe", "max_dd%"]]

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA journal_mode=DELETE;")
    res.to_sql("arm_summary", conn, if_exists="replace", index=False)
    conn.commit(); conn.close()

    print("\n=== TRIPLE-HIT vs GATE COMPONENTS — 5y US, net of cost ===")
    print(res.to_string(index=False))
    print("\nA=Darvas · B=Darvas+F≥7 · C=Darvas+F≥7+CoffeeCan (Triple-Hit) · "
          "BENCH=equal-weight all. Net of 0.10% US round-trip.")


if __name__ == "__main__":
    main()
