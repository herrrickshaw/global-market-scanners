#!/usr/bin/env python3
"""
signal_research.py
------------------
Desk-grade validation of the signals the daily mailer actually trades on,
across 10 years x 5 markets (IN/US/JP/KR/EU), using signal_validation.py.

What the mailer uses today (market-pipeline watchlist_digest.py +
cache_seed/zone_rules.json / zone_regime.json):
  revert   BUY bottom-tercile 5-day return       -> signal `rev5`
  mom_st   BUY top-tercile 21-day return          -> `mom21`
  mom126   BUY top-tercile 6-month return         -> `mom126`
  trend    BUY close>EMA20>EMA50                  -> `trend` (close/EMA50 - 1)
  golden_cross, darvas breakout                   -> `golden`, `hi52`
plus five families the repo's research flags but the mailer does not use:
  `rsi_os` (RSI-14 oversold), `mom12_1` (12-1 momentum), `lowvol`, `illiq` (Amihud),
  `volsurge` (Darvas volume).

Studies (each answers a requirement from the job descriptions, see
SIGNAL_RESEARCH.md):
  1. IC decay      rank IC at 5/10/21/63d, HAC t, BH-FDR q across every test
  2. Neutral IC    IC after removing liquidity + volatility exposure
  3. Regime IC     bull vs bear (breadth < 45% of names above 200d SMA,
                   the mailer's own regime definition)
  4. Orthogonality Fama-MacBeth joint coefficients (incremental value)
  5. Costs         quintile spread, turnover, break-even and net-of-cost
                   using apply_costs.COSTS_PCT, plus Deflated Sharpe
  6. Combination   purged walk-forward: best single vs IC-weighted vs ridge
  7. Health        trailing-52-week IC vs full history -> signal_health.json

Data: the warehouse OHLCV panels resolved through ~/scripts/market_paths.py
(same seam the pipeline uses). India is raw-close (bhavcopy), so any window
containing a 1-day move outside [-50%, +100%] is dropped (corporate-action
guard, as in piotroski-liquidity-research/backtest_liquidity_forward.py), and
ETF-like symbols (BEES/ETF/LIQUID/GILT/NIFTY/SENSEX) are excluded.

Run:
  python signal_research.py                      # all markets, writes reports
  python signal_research.py --markets IN US      # subset
  python signal_research.py --ohlcv /path/to/ohlcv
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

import signal_validation as sv
from apply_costs import COSTS_PCT

HERE = Path(__file__).resolve().parent
OUT = HERE / "reports" / "signal_research"

MARKETS = ("IN", "US", "JP", "KR", "EU")
MARKET_NAME = {"IN": "India", "US": "US", "JP": "Japan", "KR": "Korea", "EU": "Europe"}
UNIVERSE_N = {"IN": 750, "US": 1500, "JP": 1000, "KR": 750, "EU": 600}
HORIZONS = (5, 10, 21, 63)          # trading days; mailer's rules are scored at 10
START = "2017-07-01"                # 1y warm-up for the 252d lookbacks
ETF_RE = re.compile(r"BEES|ETF|LIQUID|GILT|NIFTY|SENSEX")
MAILER_RULE_SIGNAL = {"revert": "rev5", "mom_st": "mom21", "mom126": "mom126",
                      "trend": "trend"}


def ohlcv_root(cli: str | None) -> Path:
    if cli:
        return Path(cli)
    sys.path.insert(0, str(Path.home() / "scripts"))
    from market_paths import resolve  # the pipeline's warehouse seam
    return Path(resolve("ohlcv"))


# ── data ─────────────────────────────────────────────────────────────────────
def load_market(root: Path, mkt: str) -> dict[str, pd.DataFrame]:
    """Daily Close/High/Volume panels for names that were in the top-N by
    median dollar volume in at least one calendar year (PIT-friendly pre-filter;
    the per-date universe gate below is strictly trailing)."""
    n = UNIVERSE_N[mkt]
    glob = f"{root}/{mkt}/*.parquet"
    con = duckdb.connect()
    df = con.sql(f"""
        WITH dv AS (
          SELECT Symbol, year(Date) y, median(Close * Volume) mdv
          FROM '{glob}' WHERE Close > 0 AND Volume > 0 GROUP BY 1, 2),
        keep AS (
          SELECT DISTINCT Symbol FROM (
            SELECT Symbol, rank() OVER (PARTITION BY y ORDER BY mdv DESC) r FROM dv)
          WHERE r <= {int(n * 1.3)})
        SELECT Date, o.Symbol, Close, High, Volume
        FROM '{glob}' o JOIN keep USING (Symbol)
        WHERE Close > 0
    """).df()
    if mkt == "IN":
        df = df[~df["Symbol"].str.upper().str.contains(ETF_RE)]
    df["Date"] = pd.to_datetime(df["Date"])
    out = {}
    for col in ("Close", "High", "Volume"):
        out[col] = (df.pivot_table(index="Date", columns="Symbol", values=col, aggfunc="last")
                      .sort_index().astype("float32"))
    return out


def build_signals(p: dict[str, pd.DataFrame], mkt: str):
    """Signals, forward returns and gating exposures sampled on Fridays."""
    C = p["Close"].ffill(limit=3)
    H = p["High"].reindex_like(C)
    V = p["Volume"].reindex_like(C).fillna(0)
    r1 = C.pct_change(fill_method=None)
    bad = ((r1 < -0.5) | (r1 > 1.0)).astype("float32")     # corporate-action guard
    dv = (C * V)

    def clean(x: pd.DataFrame, lookback: int) -> pd.DataFrame:
        hit = bad.rolling(lookback, min_periods=1).max() > 0
        return x.mask(hit)

    up = r1.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    dn = (-r1).clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rsi14 = 100 - 100 / (1 + up / dn.where(dn > 0))       # Wilder RSI on returns
    ema20 = C.ewm(span=20, adjust=False).mean()
    ema50 = C.ewm(span=50, adjust=False).mean()
    ema200 = C.ewm(span=200, adjust=False).mean()
    sig = {
        "rev5": clean(-(C / C.shift(5) - 1), 6),
        "rsi_os": clean(-rsi14, 30),       # oversold = high; BACKTEST_FINDINGS' rsi_oversold
        "mom21": clean(C / C.shift(21) - 1, 22),
        "mom126": clean(C / C.shift(126) - 1, 127),
        "mom12_1": clean(C.shift(21) / C.shift(252) - 1, 253),
        "trend": clean(C / ema50 - 1, 60),
        "golden": clean(ema50 / ema200 - 1, 200),
        "hi52": clean(C / H.rolling(252, min_periods=200).max(), 253),
        "lowvol": clean(-r1.rolling(63, min_periods=50).std(), 64),
        "illiq": clean((r1.abs() / dv.replace(0, np.nan)).rolling(63, min_periods=50).mean(), 64),
        "volsurge": np.log((V.rolling(20, min_periods=15).mean()
                            / V.rolling(126, min_periods=100).mean()).where(lambda x: x > 0)),
    }
    # mailer zone state for reference (+1 BUY, -1 SELL, 0 HOLD)
    zone = pd.DataFrame(0.0, index=C.index, columns=C.columns)
    zone[(C > ema20) & (ema20 > ema50)] = 1.0
    zone[C < ema50] = -1.0
    sig["zone"] = zone.where(C.notna())

    # forward returns: enter next close (the mailer is read in the morning)
    fwd = {}
    for h in HORIZONS:
        f = C.shift(-(h + 1)) / C.shift(-1) - 1
        fbad = bad[::-1].rolling(h + 1, min_periods=1).max()[::-1].shift(-1) > 0
        fwd[h] = f.mask(fbad)

    mdv63 = dv.rolling(63, min_periods=40).median()
    vol63 = r1.rolling(63, min_periods=50).std()
    above200 = (C > C.rolling(200, min_periods=150).mean()).where(C.notna())

    fridays = C.resample("W-FRI").last().index
    idx = C.index[C.index.searchsorted(fridays, side="right") - 1]
    idx = pd.DatetimeIndex(sorted(set(idx[idx >= pd.Timestamp(START)])))
    # trailing liquidity gate: top-N by 63d median dollar volume, >= 1y history
    hist = C.notna().rolling(252, min_periods=1).sum()
    gate_rank = mdv63.loc[idx].rank(axis=1, ascending=False)
    gate = (gate_rank <= UNIVERSE_N[mkt]) & (hist.loc[idx] >= 240)

    S = {k: v.loc[idx].where(gate) for k, v in sig.items()}
    F = {h: v.loc[idx].where(gate) for h, v in fwd.items()}
    X = {"mdv": mdv63.loc[idx].where(gate), "vol": vol63.loc[idx].where(gate)}
    breadth = above200.loc[idx].where(gate).mean(axis=1)
    regime = pd.Series(np.where(breadth < 0.45, "bear", "bull"), index=idx)
    return S, F, X, regime


# ── studies ──────────────────────────────────────────────────────────────────
def lags_for(h: int) -> int:
    return max(1, math.ceil(h / 5))


def study_market(mkt: str, root: Path) -> dict:
    t0 = time.time()
    p = load_market(root, mkt)
    S, F, X, regime = build_signals(p, mkt)
    del p
    sigs = [k for k in S if k != "zone"]
    res = {"ic": [], "ic_series": {}, "fm": None, "cost": [], "wf": None,
           "health": {}, "regime_share_bear": float((regime == "bear").mean()),
           "n_dates": len(regime), "universe": UNIVERSE_N[mkt],
           "current_regime": regime.iloc[-1] if len(regime) else None}

    neut = {k: sv.neutralize(S[k], [X["mdv"], X["vol"]]) for k in sigs}
    for k in sigs + ["zone"]:
        for h in HORIZONS:
            L = lags_for(h)
            ic = sv.rank_ic_series(S[k], F[h])
            mu, t, pv = sv.newey_west_t(ic, L)
            row = {"market": mkt, "signal": k, "h": h, "mean_ic": mu, "nw_t": t, "p": pv,
                   "icir_ann": mu / ic.std() * math.sqrt(52 / L) if ic.std() else np.nan,
                   "hit": float((ic > 0).mean()), "n_dates": int(ic.notna().sum())}
            if k != "zone":
                nic = sv.rank_ic_series(neut[k], F[h])
                row["neutral_ic"], row["neutral_t"], _ = sv.newey_west_t(nic, L)
            for reg in ("bull", "bear"):
                m = regime == reg
                row[f"{reg}_ic"], row[f"{reg}_t"], _ = sv.newey_west_t(ic[m], L)
            res["ic"].append(row)
            if h == 10:
                res["ic_series"][k] = ic
        if k != "zone":
            res["health"][k] = sv.ic_health(res["ic_series"][k], window=52, lags=lags_for(10))

    # orthogonality at the mailer's 10d horizon
    res["fm"] = sv.fama_macbeth({k: S[k] for k in sigs}, F[10], lags=lags_for(10))
    res["fm"].insert(0, "market", mkt)

    # costs on a non-overlapping grid, per horizon
    cost_rt = COSTS_PCT.get(MARKET_NAME[mkt], 0.3) / 100.0
    for h in (5, 21, 63):
        step = lags_for(h)
        for k in sigs:
            sgn = 1.0 if (S[k].pipe(sv.rank_ic_series, F[h]).mean() >= 0) else -1.0
            q = sv.quantile_spread_turnover(S[k].iloc[::step] * sgn, F[h].iloc[::step])
            ppy = 252 / h
            ls_net = q["ls_spread"] - 2 * q["turnover_ls"] * cost_rt
            lo_net = q["long_excess"] - q["turnover_long"] * cost_rt
            res["cost"].append({
                "market": mkt, "signal": k, "h": h, "direction": "+" if sgn > 0 else "-",
                "ls_spread_pct": 100 * q["ls_spread"], "turnover_ls": q["turnover_ls"],
                "breakeven_bps_ls": 1e4 * q["breakeven_rt_cost_ls"],
                "cost_bps": 1e4 * cost_rt,
                "ls_net_ann_pct": 100 * ls_net * ppy,
                "long_excess_pct": 100 * q["long_excess"], "turnover_long": q["turnover_long"],
                "long_net_ann_pct": 100 * lo_net * ppy,
                "ls_sharpe_ann": q["ls_sharpe"] * math.sqrt(ppy),
                "_ls_series": q["ls_series"] - 2 * q["turnover_ls"] * cost_rt,
                "n_periods": q["n_periods"]})

    # purged walk-forward combination (10d horizon, embargo = overlap + 1)
    res["wf"] = sv.purged_walk_forward({k: S[k] for k in sigs}, F[10],
                                       embargo=lags_for(10) + 1, n_folds=5)
    if res["wf"] is not None and len(res["wf"]):
        res["wf"].insert(0, "market", mkt)
    print(f"  {mkt}: {len(regime)} dates, {time.time() - t0:.0f}s", flush=True)
    return res


# ── reporting ────────────────────────────────────────────────────────────────
def mailer_rules() -> dict:
    base = Path.home() / "market-pipeline" / "code" / "python_files" / "cache_seed"
    out = {}
    for f in ("zone_rules.json", "zone_regime.json"):
        try:
            out[f] = json.loads((base / f).read_text())
        except (OSError, ValueError):
            out[f] = {}
    return out


def fmt(x, d=3):
    return "" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{d}f}"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--markets", nargs="*", default=list(MARKETS))
    ap.add_argument("--ohlcv", default=None, help="override OHLCV root")
    a = ap.parse_args()
    root = ohlcv_root(a.ohlcv)
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"OHLCV root: {root}")

    results = {m: study_market(m, root) for m in a.markets}

    ic = pd.DataFrame([r for m in results.values() for r in m["ic"]])
    ic["q_bh"] = sv.bh_fdr(ic["p"])
    ic["hlz_t3"] = ic["nw_t"].abs() >= 3.0          # Harvey-Liu-Zhu (2016) hurdle
    ic.to_csv(OUT / "ic_table.csv", index=False)
    fm = pd.concat([m["fm"] for m in results.values()], ignore_index=True)
    fm.to_csv(OUT / "fama_macbeth_10d.csv", index=False)
    wf = pd.concat([m["wf"] for m in results.values() if m["wf"] is not None and len(m["wf"])],
                   ignore_index=True)
    wf.to_csv(OUT / "walk_forward_10d.csv", index=False)

    cost = pd.DataFrame([r for m in results.values() for r in m["cost"]])
    # Deflated Sharpe across ALL long-short strategies tried (every market x
    # signal x horizon is one trial), on per-period net returns
    srs = [np.nanmean(s) / np.nanstd(s, ddof=1) for s in cost["_ls_series"] if len(s) > 10]
    sr_var = float(np.nanvar(srs, ddof=1))
    cost["dsr"] = [sv.deflated_sharpe(s, n_trials=len(cost), sr_trials_var=sr_var)[1]
                   if len(s) > 10 else np.nan for s in cost["_ls_series"]]
    cost.drop(columns="_ls_series").to_csv(OUT / "costs.csv", index=False)

    health = {m: {"asof": str(results[m]["ic_series"]["rev5"].index[-1].date()),
                  "current_regime": results[m]["current_regime"],
                  "signals": results[m]["health"]} for m in results}
    (OUT / "signal_health.json").write_text(json.dumps(health, indent=1, default=float))

    write_markdown(ic, fm, wf, cost, results, mailer_rules())
    print(f"wrote {OUT}/ and SIGNAL_RESEARCH_RESULTS.md")


def write_markdown(ic, fm, wf, cost, results, rules):
    L = ["# Signal research results — mailer signals under desk-grade validation", "",
         f"_Generated by `signal_research.py` on {time.strftime('%Y-%m-%d')}. "
         "Method and job-description mapping: [SIGNAL_RESEARCH.md](SIGNAL_RESEARCH.md)._", ""]
    n_tests = int(ic["p"].notna().sum())
    surv = ic[(ic["q_bh"] < 0.05) & (ic["signal"] != "zone")]
    L += ["## 1. Universe", "",
          "| Market | formation dates | liquid names / date | bear-regime share | regime now |",
          "|---|---|---|---|---|"]
    for m, r in results.items():
        L.append(f"| {m} | {r['n_dates']} | top {r['universe']} by 63d median $-volume | "
                 f"{100 * r['regime_share_bear']:.0f}% | {r['current_regime']} |")
    L += ["", "## 2. Rank IC by horizon (HAC t, BH-FDR across all tests)", "",
          f"{n_tests} (market x signal x horizon) tests. **{len(surv)} survive BH-FDR q<0.05**, "
          f"{int(ic.loc[ic.signal != 'zone', 'hlz_t3'].sum())} clear the Harvey-Liu-Zhu |t|>=3 hurdle.", ""]
    for m in results:
        sub = ic[ic.market == m]
        L += [f"### {m}", "", "| signal | " + " | ".join(f"IC {h}d (t)" for h in HORIZONS)
              + " | neutral IC 10d (t) | bull IC 10d | bear IC 10d |",
              "|---|" + "---|" * (len(HORIZONS) + 3)]
        for k in sub.signal.unique():
            s = sub[sub.signal == k].set_index("h")
            cells = []
            for h in HORIZONS:
                star = "**" if s.loc[h, "q_bh"] < 0.05 else ""
                cells.append(f"{star}{fmt(s.loc[h, 'mean_ic'])} ({fmt(s.loc[h, 'nw_t'], 1)}){star}")
            r10 = s.loc[10]
            L.append(f"| {k} | " + " | ".join(cells)
                     + f" | {fmt(r10.get('neutral_ic'))} ({fmt(r10.get('neutral_t'), 1)})"
                     + f" | {fmt(r10['bull_ic'])} ({fmt(r10['bull_t'], 1)})"
                     + f" | {fmt(r10['bear_ic'])} ({fmt(r10['bear_t'], 1)}) |")
        L.append("")
    L += ["**bold** = survives BH-FDR at 5%. Sign convention: positive IC means a higher "
          "signal value predicts a higher forward return (rev5 is already negated, so a "
          "positive rev5 IC means short-term reversal works).", ""]

    L += ["## 3. What the mailer is using vs what the evidence says (10d horizon)", "",
          "The mailer (`zone_regime.json`) picks one rule per market per regime. Each is "
          "scored here by its rank IC *inside that regime*; a mailer BUY rule needs a "
          "**positive** IC. `VERDICT`: OK = IC>0 with |t|>=2, WEAK = IC>0 but |t|<2, "
          "WRONG-WAY = IC<0 (the rule buys what underperforms).", "",
          "| Market | regime | mailer rule | signal | IC in regime (t) | verdict | "
          "health (last 52w) | strongest signal in regime (trade direction) |",
          "|---|---|---|---|---|---|---|---|"]
    zr = rules.get("zone_regime.json", {})
    for m, r in results.items():
        sub = ic[(ic.market == m) & (ic.h == 10) & (ic.signal != "zone")]
        for reg in ("bull", "bear"):
            rule = zr.get(m, {}).get(f"{reg}_rule", "n/a")
            sg = MAILER_RULE_SIGNAL.get(rule)
            now = " ◀ now" if reg == r["current_regime"] else ""
            if sg is not None and sg in set(sub.signal):
                row = sub[sub.signal == sg].iloc[0]
                mu, t = row[f"{reg}_ic"], row[f"{reg}_t"]
                cur = f"{fmt(mu)} ({fmt(t, 1)})"
                verdict = ("WRONG-WAY" if mu < 0 else "OK" if abs(t) >= 2 else "WEAK")
                hl = r["health"][sg]["status"]
            else:
                cur, verdict, hl = "n/a", "n/a", "n/a"
            cand = sub[sub[f"{reg}_t"].abs() >= 2]
            if len(cand):
                b = cand.loc[cand[f"{reg}_t"].abs().idxmax()]
                d = "long high values" if b[f"{reg}_ic"] > 0 else "long low values"
                best = f"{b.signal} {fmt(b[f'{reg}_ic'])} (t {fmt(b[f'{reg}_t'], 1)}, {d})"
            else:
                best = "none with |t|>=2"
            L.append(f"| {m} | {reg}{now} | {rule} | {sg or 'n/a'} | {cur} | {verdict} | "
                     f"{hl} | {best} |")
    L += ["", "_`lowvol` usually tops this column; it is a low-risk anomaly, not a timing "
          "rule, and is listed so the mailer's choice can be compared against the best "
          "available family._", ""]

    L += ["## 4. Orthogonality — Fama-MacBeth joint coefficients (10d)", "",
          "Each coefficient is the signal's contribution with all others held fixed. "
          "Univariate-significant but FM-insignificant = redundant.", "",
          "| signal | " + " | ".join(results) + " |", "|---|" + "---|" * len(results)]
    for k in fm.signal.unique():
        cells = []
        for m in results:
            r = fm[(fm.market == m) & (fm.signal == k)]
            cells.append(f"{fmt(r.fm_coef.iloc[0], 4)} ({fmt(r.fm_t.iloc[0], 1)})" if len(r) else "")
        L.append(f"| {k} | " + " | ".join(cells) + " |")
    L.append("")

    L += ["## 5. Costs, turnover and Deflated Sharpe (non-overlapping rebalances)", "",
          "Signal oriented by its full-sample IC sign. Round-trip cost from "
          "`apply_costs.COSTS_PCT`. DSR = probability the Sharpe beats the best of "
          f"{len(cost)} zero-skill trials (>0.95 = credible).", "",
          "| market | signal | h | dir | LS spread/period % | turnover | break-even bps | "
          "cost bps | LS net ann % | long-only net ann % | DSR |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    top = cost.sort_values("ls_net_ann_pct", ascending=False)
    for _, r in top.iterrows():
        if r.ls_net_ann_pct <= 0 and r.long_net_ann_pct <= 0:
            continue
        L.append(f"| {r.market} | {r.signal} | {r.h} | {r.direction} | {fmt(r.ls_spread_pct, 2)} | "
                 f"{fmt(r.turnover_ls, 2)} | {fmt(r.breakeven_bps_ls, 0)} | {fmt(r.cost_bps, 0)} | "
                 f"{fmt(r.ls_net_ann_pct, 1)} | {fmt(r.long_net_ann_pct, 1)} | {fmt(r.dsr, 2)} |")
    L += ["", f"_Rows where both long-short and long-only lose after costs are omitted "
          f"({int(((cost.ls_net_ann_pct <= 0) & (cost.long_net_ann_pct <= 0)).sum())} of "
          f"{len(cost)}); full table in `reports/signal_research/costs.csv`._", ""]

    L += ["## 6. Purged walk-forward combination (10d, OOS mean IC per fold)", "",
          "| market | fold | test window | best single (train-chosen) | best_single | "
          "ic_weighted | ridge |", "|---|---|---|---|---|---|---|"]
    for _, r in wf.iterrows():
        L.append(f"| {r.market} | {r.fold} | {r.test_start}..{r.test_end} | {r.best_single_name} | "
                 f"{fmt(r.oos_ic_best_single)} | {fmt(r.oos_ic_ic_weighted)} | {fmt(r.oos_ic_ridge)} |")
    if len(wf):
        g = wf.groupby("market")[["oos_ic_best_single", "oos_ic_ic_weighted", "oos_ic_ridge"]].mean()
        L += ["", "Mean across folds:", "", "| market | best_single | ic_weighted | ridge |",
              "|---|---|---|---|"]
        for m, r in g.iterrows():
            L.append(f"| {m} | {fmt(r.oos_ic_best_single)} | {fmt(r.oos_ic_ic_weighted)} | "
                     f"{fmt(r.oos_ic_ridge)} |")
    L.append("")

    L += ["## 7. Live signal health (trailing 52 weeks vs full history, 10d IC)", "",
          "| signal | " + " | ".join(results) + " |", "|---|" + "---|" * len(results)]
    for k in next(iter(results.values()))["health"]:
        L.append(f"| {k} | " + " | ".join(
            f"{results[m]['health'][k]['status']} ({fmt(results[m]['health'][k]['recent_ic'])})"
            for m in results) + " |")
    L += ["", "Machine-readable copy for the mailer: `reports/signal_research/signal_health.json`.", ""]
    (HERE / "SIGNAL_RESEARCH_RESULTS.md").write_text("\n".join(L))


if __name__ == "__main__":
    main()
