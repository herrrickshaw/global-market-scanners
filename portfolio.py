#!/usr/bin/env python3
"""
portfolio.py
------------
The last mile the platform never had: turn a set of *signals* (e.g. today's GGG
Strong Performers) into an actual *portfolio* — constrained weights you could
trade. factor_research proved min-variance / max-Sharpe cut vol for free; this
productionises that with the constraints a real book needs:

  * long-only (short legs clipped)             --long-only (default)
  * per-position cap                           --cap 0.10
  * per-sector cap                             --sector-cap 0.30
  * turnover control vs an existing book       --prev prev_weights.json --max-turnover 0.4

Weights come from the trailing covariance of the candidates' returns. Pure,
unit-testable core (numpy); the CLI sources candidates from dvm_composite.db and
prices from the local cleaned_long parquets, then reports the book's risk via
risk.py.

Usage:
  python portfolio.py --market US --n 20 --method min_var --cap 0.10
  python portfolio.py --market JP --n 15 --method max_sharpe --sector-cap 0.30
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = os.path.expanduser("~/Downloads/code/python_files/cache_seed")


# ── pure optimiser core ───────────────────────────────────────────────────────
def min_variance_weights(cov: np.ndarray) -> np.ndarray:
    inv = np.linalg.pinv(cov)
    one = np.ones(cov.shape[0])
    w = inv @ one
    return w / w.sum()


def max_sharpe_weights(mu: np.ndarray, cov: np.ndarray) -> np.ndarray:
    """Tangency portfolio (no risk-free): w ∝ Σ⁻¹ μ."""
    inv = np.linalg.pinv(cov)
    w = inv @ mu
    s = w.sum()
    return w / s if s != 0 else np.ones_like(mu) / len(mu)


def long_only(w: np.ndarray) -> np.ndarray:
    """Clip shorts to zero and renormalise; fall back to equal-weight if all <=0."""
    w = np.where(w > 0, w, 0.0)
    s = w.sum()
    return w / s if s > 0 else np.ones_like(w) / len(w)


def cap_weights(w: np.ndarray, cap: float, iters: int = 100) -> np.ndarray:
    """Enforce a per-position cap, iteratively pushing spillover onto the
    uncapped names until every weight <= cap (needs n*cap >= 1)."""
    w = np.array(w, dtype=float)
    n = len(w)
    if cap * n < 1 - 1e-9:
        return np.ones(n) / n            # infeasible -> equal weight (closest feasible)
    for _ in range(iters):
        over = w > cap + 1e-12
        if not over.any():
            break
        excess = (w[over] - cap).sum()
        w[over] = cap
        free = ~over & (w > 0)
        if not free.any():
            w[:] = cap
            break
        w[free] += excess * w[free] / w[free].sum()
    return w / w.sum()


def apply_sector_cap(w: np.ndarray, sectors: list, cap: float, iters: int = 100) -> np.ndarray:
    """Cap the summed weight of any one sector at `cap`, redistributing the
    excess proportionally to names in under-cap sectors."""
    w = np.array(w, dtype=float)
    sec = np.array(sectors, dtype=object)
    uniq = pd.unique(sec)
    if cap * len(uniq) < 1 - 1e-9:
        return w / w.sum()               # infeasible; leave as-is
    for _ in range(iters):
        totals = {s: w[sec == s].sum() for s in uniq}
        over = [s for s, tot in totals.items() if tot > cap + 1e-12]
        if not over:
            break
        excess = 0.0
        for s in over:
            m = sec == s
            excess += w[m].sum() - cap
            w[m] *= cap / w[m].sum()
        free = np.isin(sec, [s for s in uniq if s not in over]) & (w > 0)
        if not free.any():
            break
        w[free] += excess * w[free] / w[free].sum()
    return w / w.sum()


def turnover(w_new: np.ndarray, w_old: np.ndarray) -> float:
    """One-way turnover = 0.5 * Σ|Δw| (0 = identical book, 1 = full replacement)."""
    return float(0.5 * np.abs(np.asarray(w_new) - np.asarray(w_old)).sum())


def blend_to_turnover(w_new: np.ndarray, w_old: np.ndarray, max_turnover: float) -> np.ndarray:
    """Move from the old book toward the target only as far as the turnover budget
    allows: w = w_old + λ(w_new − w_old), λ chosen so turnover <= budget."""
    t = turnover(w_new, w_old)
    if t <= max_turnover or t == 0:
        return w_new
    lam = max_turnover / t
    w = w_old + lam * (w_new - w_old)
    return w / w.sum()


def build_weights(returns: pd.DataFrame, method: str = "min_var", cap: float | None = None,
                  sectors: list | None = None, sector_cap: float | None = None,
                  prev: dict | None = None, max_turnover: float | None = None) -> pd.Series:
    """Full pipeline: covariance -> optimiser -> long-only -> caps -> turnover.
    `returns` columns are tickers; index is dates. Returns a weight Series."""
    R = returns.dropna(axis=1, how="any")
    if R.shape[1] < 2:
        raise ValueError("need >=2 assets with complete return history")
    cov = R.cov().values * 252
    if method == "max_sharpe":
        mu = R.mean().values * 252
        w = max_sharpe_weights(mu, cov)
    else:
        w = min_variance_weights(cov)
    w = long_only(w)
    if cap:
        w = cap_weights(w, cap)
    if sectors is not None and sector_cap:
        secmap = dict(zip(returns.columns, sectors))
        w = apply_sector_cap(w, [secmap[c] for c in R.columns], sector_cap)
        if cap:                                   # re-tighten position cap after sector pass
            w = cap_weights(w, cap)
    if prev is not None and max_turnover is not None:
        w_old = np.array([prev.get(c, 0.0) for c in R.columns])
        if w_old.sum() > 0:
            w = blend_to_turnover(w, w_old / w_old.sum(), max_turnover)
    return pd.Series(w, index=R.columns).sort_values(ascending=False)


# ── data helpers (offline) ────────────────────────────────────────────────────
def candidates_from_composite(market: str, n: int, db: str) -> pd.DataFrame:
    q = ("SELECT ticker, sector, composite FROM dvm_composite "
         f"WHERE market=? AND code='GGG' ORDER BY composite DESC LIMIT {int(n)}")
    con = sqlite3.connect(db)
    try:
        df = pd.read_sql(q, con, params=(market,))
    finally:
        con.close()
    return df


def returns_for(market: str, tickers: list) -> pd.DataFrame:
    p = os.path.join(SEED, f"cleaned_long_{market}.parquet")
    px = pd.read_parquet(p)
    px = px[px["Symbol"].isin(set(tickers))]
    wide = px.pivot_table(index="Date", columns="Symbol", values="Close", aggfunc="last")
    return wide.astype(float).pct_change().tail(252)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="US")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--method", choices=["min_var", "max_sharpe"], default="min_var")
    ap.add_argument("--cap", type=float, default=0.10, help="per-position cap (fraction)")
    ap.add_argument("--sector-cap", type=float, default=None, help="per-sector cap (fraction)")
    ap.add_argument("--prev", default=None, help="prev weights JSON for turnover control")
    ap.add_argument("--max-turnover", type=float, default=None)
    ap.add_argument("--db", default=os.path.join(HERE, "dvm_composite.db"))
    args = ap.parse_args()

    if not os.path.exists(args.db):
        raise SystemExit("no dvm_composite.db — run dvm_composite.py first")
    cand = candidates_from_composite(args.market, args.n, args.db)
    if cand.empty:
        raise SystemExit(f"no GGG candidates for {args.market}")
    R = returns_for(args.market, cand["ticker"].tolist())
    sectors = cand.set_index("ticker")["sector"].reindex(R.columns).fillna("Unknown").tolist()
    prev = json.load(open(args.prev)) if args.prev else None

    w = build_weights(R, method=args.method, cap=args.cap, sectors=sectors,
                      sector_cap=args.sector_cap, prev=prev, max_turnover=args.max_turnover)

    import risk
    port_ret = (R[w.index].fillna(0.0) * w.values).sum(axis=1).dropna().values

    print(f"\n=== {args.method} portfolio — {args.market}, {len(w)} names "
          f"(cap {args.cap}) ===")
    for t, wt in w.items():
        print(f"  {t:14} {wt*100:6.2f}%   {dict(zip(cand.ticker, cand.sector)).get(t,'')}")
    print(f"\n  turnover vs prev: "
          f"{turnover(w.values, np.array([(prev or {}).get(t,0) for t in w.index])):.2f}"
          if prev else "")
    print("  --- portfolio risk (trailing 1y) ---")
    for k, v in risk.risk_report(port_ret).items():
        print(f"    {k:<12} {v}")
    outp = os.path.join(HERE, f"portfolio_{args.market}_{args.method}.json")
    json.dump({t: round(float(x), 6) for t, x in w.items()}, open(outp, "w"), indent=2)
    print(f"\n  saved weights -> {outp}")


if __name__ == "__main__":
    main()
