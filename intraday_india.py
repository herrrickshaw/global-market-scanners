#!/usr/bin/env python3
"""
intraday_india.py — India open-to-close quintile picks (no overnight holding).

Motivation: the signal re-test (SIGNAL_RESEARCH_RESULTS.md §5c) found that
India's `hi52`, `lowvol` and `mom12_1` quintile spreads are earned intraday and
lose overnight. This backtest trades only that leg: each signal is computed
from closes up to day t-1, the book is entered at the open of day t and squared
off at the close of day t. Indian intraday (MIS) trading lets a retail account
short the cash market as long as the position is closed the same day, so the
long-short book is implementable without SLB or futures.

Book, per day and signal:
  long     equal-weight top quintile, open→close
  short    equal-weight bottom quintile, open→close
  LS       long − short ($1 each side)
  LO-exc   long − equal-weight universe (what the signal adds to a day trader)

Costs: every position is opened and closed every day, so each leg pays one
full intraday round trip per day:
  fees      `intraday_fees_bps()`: STT 0.025% sell side, NSE transaction
            0.00297% per side, SEBI 0.0001% per side, stamp 0.003% buy side,
            brokerage min(₹20, 0.03%) per order on a ₹1 lakh ticket, GST 18%
            on brokerage + transaction + SEBI
  slippage  scenarios of 0 / 5 / 10 / 20 bps per side (entry at the open
            auction, exit near the close), reported side by side

Universe: per day, the top 750 by trailing 63-day median rupee volume with
≥ 240 days of history (as of t-1). Names whose day-t bar has High == Low are
dropped (locked at a price band all day, so not fillable), as are ETF-like
symbols, and any name whose last 253 days contain a raw-close move outside
[-50%, +100%] (bhavcopy corporate-action guard, as in signal_research.py).

Run:
  python intraday_india.py                 # writes reports/intraday_india/ + INTRADAY_INDIA_RESULTS.md
  python intraday_india.py --ohlcv /path/to/ohlcv
"""

from __future__ import annotations

import argparse
import math
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

import signal_validation as sv
from signal_research import ETF_RE, START, ohlcv_root

HERE = Path(__file__).resolve().parent
OUT = HERE / "reports" / "intraday_india"
UNIVERSE_N = 750
SIGNALS = ("hi52", "lowvol", "mom12_1")
SLIPPAGE_BPS = (0, 5, 10, 20)        # per side
DAYS_PER_YEAR = 250


# ── costs ────────────────────────────────────────────────────────────────────
def intraday_fees_bps(ticket_inr: float = 100_000.0) -> float:
    """Statutory + broker fees for one intraday round trip (buy and sell of the
    same notional), in bps of notional. Rates as listed in the module doc."""
    brokerage = 2 * min(20.0, 0.0003 * ticket_inr) / ticket_inr
    txn = 2 * 0.0000297
    sebi = 2 * 0.000001
    stt = 0.00025
    stamp = 0.00003
    gst = 0.18 * (brokerage + txn + sebi)
    return 1e4 * (brokerage + txn + sebi + stt + stamp + gst)


# ── data ─────────────────────────────────────────────────────────────────────
def load_india(root: Path) -> dict[str, pd.DataFrame]:
    glob = f"{root}/IN/*.parquet"
    df = duckdb.connect().sql(f"""
        WITH dv AS (
          SELECT Symbol, year(Date) y, median(Close * Volume) mdv
          FROM '{glob}' WHERE Close > 0 AND Volume > 0 GROUP BY 1, 2),
        keep AS (
          SELECT DISTINCT Symbol FROM (
            SELECT Symbol, rank() OVER (PARTITION BY y ORDER BY mdv DESC) r FROM dv)
          WHERE r <= {int(UNIVERSE_N * 1.3)})
        SELECT Date, o.Symbol, Open, High, Low, Close, Volume
        FROM '{glob}' o JOIN keep USING (Symbol)
        WHERE Close > 0
    """).df()
    df = df[~df["Symbol"].str.upper().str.contains(ETF_RE)]
    df["Date"] = pd.to_datetime(df["Date"])
    return {c: df.pivot_table(index="Date", columns="Symbol", values=c, aggfunc="last")
                 .sort_index().astype("float64")
            for c in ("Open", "High", "Low", "Close", "Volume")}


def build(p: dict[str, pd.DataFrame]):
    """Signals as of close t-1 (already lagged one day), day-t open→close
    return, and the tradeable-universe mask for day t."""
    C, O, H, L, V = (p[k] for k in ("Close", "Open", "High", "Low", "Volume"))
    r1 = C.pct_change(fill_method=None)
    bad = ((r1 < -0.5) | (r1 > 1.0)).astype(float)
    hit = bad.rolling(253, min_periods=1).max() > 0
    sig = {
        "hi52": C / H.rolling(252, min_periods=200).max(),
        "lowvol": -r1.rolling(63, min_periods=50).std(),
        "mom12_1": C.shift(21) / C.shift(252) - 1,
    }
    sig = {k: v.mask(hit).shift(1) for k, v in sig.items()}       # known at t-1 close
    oc = (C / O - 1).where((O > 0) & (H > L))                       # day t, fillable
    mdv = (C * V).rolling(63, min_periods=40).median().shift(1)
    hist = C.notna().rolling(252, min_periods=1).sum().shift(1)
    gate = (mdv.rank(axis=1, ascending=False) <= UNIVERSE_N) & (hist >= 240)
    days = oc.index[oc.index >= pd.Timestamp(START)]
    return ({k: v.loc[days].where(gate.loc[days]) for k, v in sig.items()},
            oc.loc[days].where(gate.loc[days]))


# ── book ─────────────────────────────────────────────────────────────────────
def daily_book(signal: pd.DataFrame, ret: pd.DataFrame, q: float = 0.2,
               min_names: int = 100) -> pd.DataFrame:
    """Per-day gross returns of the long, short and universe legs."""
    s, r = signal.align(ret, join="inner")
    ok = s.notna() & r.notna()
    s, r = s.where(ok), r.where(ok)
    pct = s.rank(axis=1, pct=True)
    n = ok.sum(axis=1)
    out = pd.DataFrame({
        "long": r.where(pct > 1 - q).mean(axis=1),
        "short": r.where(pct <= q).mean(axis=1),
        "univ": r.mean(axis=1),
        "n": n,
    })
    return out[out["n"] >= min_names]


def perf(x: pd.Series, lags: int = 5) -> dict:
    """Annualised mean, Sharpe, HAC t and tail stats of a daily series."""
    x = x.dropna()
    mu, t, _ = sv.newey_west_t(x, lags)
    tr = sv.tail_risk(x.to_numpy())
    return {"ann_pct": 100 * DAYS_PER_YEAR * mu,
            "sharpe": mu / x.std(ddof=1) * math.sqrt(DAYS_PER_YEAR) if x.std(ddof=1) else np.nan,
            "t": t, "hit": float((x > 0).mean()),
            "maxdd_pct": 100 * tr["max_dd"], "cvar95_bps": 1e4 * tr["cvar"], "days": len(x)}


# ── run ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--ohlcv", default=None, help="override OHLCV root")
    a = ap.parse_args()
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    p = load_india(ohlcv_root(a.ohlcv))
    S, R = build(p)
    del p
    fee = intraday_fees_bps() / 1e4

    rows, series, yearly = [], {}, []
    for k in SIGNALS:
        b = daily_book(S[k], R)
        legs = {"LS": b["long"] - b["short"], "LO-exc": b["long"] - b["univ"],
                "long": b["long"]}
        # round trips per $1 of capital per day: LS trades both legs
        rts = {"LS": 2, "LO-exc": 1, "long": 1}
        for book, g in legs.items():
            for slip in SLIPPAGE_BPS:
                cost = rts[book] * (fee + 2 * slip / 1e4)
                net = g - cost          # LO-exc pays the long leg's costs
                rows.append({"signal": k, "book": book, "slip_bps_side": slip,
                             "avg_names": float(b["n"].mean()),
                             "cost_bps_day": 1e4 * cost, "gross_bps_day": 1e4 * g.mean(),
                             "breakeven_slip_bps": (1e4 * g.mean() / rts[book] - 1e4 * fee) / 2,
                             **perf(net)})
                series[f"{k}|{book}|{slip}"] = net
            if book == "LS":
                for y, v in g.groupby(g.index.year):
                    yearly.append({"signal": k, "year": int(y),
                                   "ls_gross_bps_day": 1e4 * v.mean(),
                                   "ls_net_fees_ann_pct": 100 * DAYS_PER_YEAR * (v.mean() - 2 * fee),
                                   "days": len(v)})

    res = pd.DataFrame(rows)
    srs = [s.mean() / s.std(ddof=1) for s in series.values()]
    var = float(np.nanvar(srs, ddof=1))
    res["dsr"] = [sv.deflated_sharpe(series[f"{r.signal}|{r.book}|{r.slip_bps_side}"],
                                     n_trials=len(series), sr_trials_var=var)[1]
                  for r in res.itertuples()]
    res.to_csv(OUT / "results.csv", index=False)
    yr = pd.DataFrame(yearly)
    yr.to_csv(OUT / "by_year.csv", index=False)
    write_markdown(res, yr, fee)
    print(f"wrote {OUT}/ and INTRADAY_INDIA_RESULTS.md in {time.time() - t0:.0f}s")


def fmt(x, d=2):
    return "" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{d}f}"


def write_markdown(res: pd.DataFrame, yr: pd.DataFrame, fee: float):
    L = ["# India open-to-close quintile picks — backtest results", "",
         f"_Generated by `intraday_india.py` on {time.strftime('%Y-%m-%d')}. Method in the "
         "module docstring._", "",
         f"Signals known at the prior close; entry at the open, exit at the close, daily. "
         f"Fees {1e4 * fee:.1f} bps per intraday round trip; slippage is per side on top. "
         "LS pays two round trips a day per $1 a side; LO-exc and long pay one.", "",
         "| signal | book | slippage bps/side | gross bps/day | cost bps/day | net ann % | "
         "Sharpe | HAC t | hit | max DD % | CVaR95 bps/day | DSR | break-even slippage bps/side |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in res.itertuples():
        L.append(f"| {r.signal} | {r.book} | {r.slip_bps_side} | {fmt(r.gross_bps_day, 1)} | "
                 f"{fmt(r.cost_bps_day, 1)} | {fmt(r.ann_pct, 1)} | {fmt(r.sharpe)} | "
                 f"{fmt(r.t, 1)} | {fmt(r.hit)} | {fmt(r.maxdd_pct, 0)} | {fmt(r.cvar95_bps, 0)} | "
                 f"{fmt(r.dsr)} | {fmt(r.breakeven_slip_bps, 1)} |")
    L += ["", "## Long-short by year: gross bps/day, and net of fees only (ann %)", "",
          "| year | " + " | ".join(f"{k} gross | {k} net" for k in SIGNALS) + " |",
          "|---|" + "---|" * (2 * len(SIGNALS))]
    g = yr.pivot(index="year", columns="signal", values="ls_gross_bps_day")
    n = yr.pivot(index="year", columns="signal", values="ls_net_fees_ann_pct")
    for y in g.index:
        L.append(f"| {y} | " + " | ".join(f"{fmt(g.loc[y, k], 1)} | {fmt(n.loc[y, k], 1)}"
                                          for k in SIGNALS) + " |")
    big = intraday_fees_bps(10_000_000)
    L += ["", f"_Fees fall to {big:.1f} bps per round trip on a ₹1 crore ticket (the ₹20 "
          "brokerage cap). Break-even slippage per side is then (gross/legs − fees)/2._"]
    (HERE / "INTRADAY_INDIA_RESULTS.md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
