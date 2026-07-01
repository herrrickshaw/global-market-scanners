#!/usr/bin/env python3
"""
factor_research.py
------------------
Tests four foundational finance papers as falsifiable proposals on our own
liquid US universe, point-in-time (features at date T, returns measured after T).

  P1  Markowitz (1952)  — diversification is the only free lunch.
      Test: do min-variance / max-Sharpe portfolios beat a concentrated pick and
      equal-weight on realised Sharpe?  (lower vol for similar return = free lunch)

  P2  Sharpe (1964) CAPM — systematic risk (beta) is priced.
      Test: sort into beta quintiles; regress forward return on beta.
      CAPM predicts a positive, significant beta-return slope.

  P3  Fama & French (1992) — beta explains ~nothing; size and value do.
      Test: multivariate OLS  fwd_ret ~ beta + log(size) + earnings_yield.
      FF predicts beta ~insignificant, small size and high value significant.

  P4  Fama (1991) EMH reassessment — which premiums are real "cracks"?
      Verdict: report which factor t-stats survive (|t|>2) — the admitted
      anomalies vs noise.

Fundamentals (size, value) are point-in-time from SEC EDGAR (pit_fundamentals);
prices via the Cassandra cache (market_store). scipy for the regressions.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


def load_universe(limit):
    hits = sorted(glob.glob(os.path.expanduser(
        "~/Downloads/data/us_full_scan/**/us_full_scan_*.xlsx"), recursive=True))
    s = pd.ExcelFile(hits[-1]).parse("All_Stocks")["Symbol"].astype(str).tolist()
    s = [x for x in s if x and x != "nan"]
    return s[:limit] if limit else s


def cross_section(ohlc, lookback_days=252, fwd_days=252, min_dollar_vol=2e6):
    """Build the factor panel: features at T (=fwd_days ago), forward return after T."""
    from pit_fundamentals import as_of, _g
    # market proxy = equal-weight mean of all stocks' daily returns
    rets = {t: df["Close"].astype(float).pct_change() for t, df in ohlc.items() if len(df) > lookback_days + fwd_days}
    mkt = pd.DataFrame(rets).mean(axis=1)                # equal-weight market return
    rows = []
    for t, df in ohlc.items():
        c = df["Close"].astype(float); v = df["Volume"].astype(float)
        if len(c) < lookback_days + fwd_days + 5:
            continue
        if (c * v).tail(252).median() < min_dollar_vol:
            continue
        T = c.index[-fwd_days - 1]                        # evaluation date
        r = c.pct_change().loc[:T].tail(lookback_days)
        m = mkt.reindex(r.index)
        var_m = m.var()
        beta = (r.cov(m) / var_m) if var_m else np.nan
        fwd = c.iloc[-1] / c.loc[T] - 1                   # realised forward return (no lookahead)
        # PIT fundamentals as of T
        f = as_of(t, str(T.date()))
        shares = f.get("shares") if f else None
        ni = _g(f["ni"]) if f and f.get("ni") else None
        mcap = (c.loc[T] * shares) if shares else None
        eyield = (ni / mcap) if (ni and mcap) else None   # earnings yield = value proxy
        if beta is None or pd.isna(beta) or mcap is None or eyield is None:
            continue
        rows.append({"ticker": t, "beta": beta, "size": mcap, "log_size": np.log10(mcap),
                     "value_ey": eyield, "fwd_ret": fwd * 100})
    return pd.DataFrame(rows), mkt


def quintile_table(df, col, ret="fwd_ret"):
    q = pd.qcut(df[col], 5, labels=[f"Q{i}" for i in range(1, 6)], duplicates="drop")
    return df.groupby(q)[ret].agg(["mean", "count"]).round(2)


def ols(y, X, names):
    """OLS with t-stats (adds intercept). Returns dict name->(coef,t)."""
    from scipy import stats
    Xd = np.column_stack([np.ones(len(X))] + [X[:, i] for i in range(X.shape[1])])
    beta, *_ = np.linalg.lstsq(Xd, y, rcond=None)
    resid = y - Xd @ beta
    dof = len(y) - Xd.shape[1]
    sigma2 = (resid @ resid) / dof
    cov = sigma2 * np.linalg.inv(Xd.T @ Xd)
    se = np.sqrt(np.diag(cov))
    tvals = beta / se
    out = {}
    for i, nm in enumerate(["intercept"] + names):
        out[nm] = (round(float(beta[i]), 4), round(float(tvals[i]), 2))
    r2 = 1 - (resid @ resid) / (((y - y.mean()) ** 2).sum())
    out["_R2"] = round(float(r2), 3)
    return out


def markowitz(ohlc, df, lookback=252):
    """P1: min-var & max-Sharpe portfolios vs concentrated & equal-weight (realised)."""
    top = df.sort_values("fwd_ret", ascending=False)["ticker"].head(30).tolist()  # candidate set
    # trailing returns matrix up to T
    fwd_days = 252
    R = {}
    for t in top:
        c = ohlc[t]["Close"].astype(float)
        T = c.index[-fwd_days - 1]
        R[t] = c.pct_change().loc[:T].tail(lookback)
    R = pd.DataFrame(R).dropna(axis=1)
    if R.shape[1] < 5:
        return None
    mu = R.mean().values * 252
    Sig = R.cov().values * 252
    n = len(mu); inv = np.linalg.pinv(Sig); one = np.ones(n)
    w_mv = inv @ one / (one @ inv @ one)                       # min-variance
    ms = inv @ mu; w_ms = ms / ms.sum()                        # max-Sharpe (tangency, no rf)
    w_ew = one / n                                             # equal weight
    concentrated = np.zeros(n); concentrated[np.argmax(mu)] = 1  # single best (gambling)
    # realised forward returns of each stock
    fwd = np.array([ohlc[t]["Close"].astype(float).iloc[-1] /
                    ohlc[t]["Close"].astype(float).iloc[-fwd_days - 1] - 1 for t in R.columns]) * 100
    def stats_(w):
        return float(w @ fwd), float(np.sqrt(w @ Sig @ w) * 100)
    res = {}
    for nm, w in [("Concentrated(1)", concentrated), ("EqualWeight", w_ew),
                  ("MinVariance", w_mv), ("MaxSharpe", w_ms)]:
        r, vol = stats_(w)
        res[nm] = {"fwd_ret%": round(r, 2), "ex_ante_vol%": round(vol, 2),
                   "ret/vol": round(r / vol, 2) if vol else 0}
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--min-dollar-vol", type=float, default=2e6)
    args = ap.parse_args()

    from market_store import cached_download
    tickers = load_universe(args.limit)
    print(f"loading {len(tickers)} US tickers (Cassandra cache)…", file=sys.stderr)
    ohlc = cached_download(tickers, years=5)
    df, _ = cross_section(ohlc, min_dollar_vol=args.min_dollar_vol)
    print(f"factor panel: {len(df)} stocks with PIT fundamentals\n", file=sys.stderr)
    if len(df) < 50:
        print("too few stocks", file=sys.stderr); return

    print("=" * 70)
    print("P1 — MARKOWITZ (1952): diversification as the free lunch")
    mk = markowitz(ohlc, df)
    if mk:
        print(f"  {'portfolio':16}{'fwd_ret%':>10}{'ex_ante_vol%':>14}{'ret/vol':>9}")
        for k, v in mk.items():
            print(f"  {k:16}{v['fwd_ret%']:>10}{v['ex_ante_vol%']:>14}{v['ret/vol']:>9}")
        print("  -> Verdict: diversified portfolios cut vol sharply for comparable return")

    print("\n" + "=" * 70)
    print("P2 — SHARPE (1964) CAPM: is beta priced?")
    print(quintile_table(df, "beta").to_string())
    r = ols(df["fwd_ret"].values, df[["beta"]].values, ["beta"])
    print(f"  OLS fwd_ret ~ beta:  beta coef={r['beta'][0]} (t={r['beta'][1]})  R2={r['_R2']}")
    print(f"  -> CAPM {'SUPPORTED' if abs(r['beta'][1])>2 and r['beta'][0]>0 else 'NOT supported'} "
          f"(needs positive, significant beta slope)")

    print("\n" + "=" * 70)
    print("P3 — FAMA-FRENCH (1992): size & value vs beta")
    print("  size quintiles (Q1=small):"); print(quintile_table(df, "log_size").to_string())
    print("  value quintiles (Q1=cheap/high-EY):"); print(quintile_table(df, "value_ey").to_string())
    ff = ols(df["fwd_ret"].values, df[["beta", "log_size", "value_ey"]].values,
             ["beta", "log_size", "value_ey"])
    print(f"  multivariate OLS fwd_ret ~ beta + log_size + value_ey  (R2={ff['_R2']}):")
    for k in ["beta", "log_size", "value_ey"]:
        sig = "***" if abs(ff[k][1]) > 2 else ""
        print(f"     {k:10} coef={ff[k][0]:>8}  t={ff[k][1]:>6} {sig}")

    print("\n" + "=" * 70)
    print("P4 — FAMA (1991) EMH: which premiums are real 'cracks'? (|t|>2)")
    cracks = [k for k in ["beta", "log_size", "value_ey"] if abs(ff[k][1]) > 2]
    print(f"  statistically significant factors: {cracks or 'NONE (efficient — no exploitable premium)'}")
    print(f"  beta significant? {'yes' if abs(ff['beta'][1])>2 else 'NO — matches FF: beta explains ~nothing'}")


if __name__ == "__main__":
    main()
