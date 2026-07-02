#!/usr/bin/env python3
"""
quality_factor.py
-----------------
Implements the Quality-Minus-Junk (QMJ) factor of Asness, Frazzini & Pedersen
(2019) as adapted for India by Jacob, Pradeep & Varma (IIMA W.P. 2022-11-01,
"Performance of quality factor in Indian Equity Market"), and generalises it to
all 19 markets in this platform.

Their construction (paper §2.1):
  Quality = average( Profitability, Growth, Safety, Payout )
  where each dimension is the average of the *standardised ranks* (z-score of the
  cross-sectional rank) of its sub-metrics:
    Profitability : gpoa, roe, roa, cfoa, gmar, (−)accruals
    Growth        : 5-yr Δ in gpoa, roe, roa, cfoa, gmar
    Safety        : (−)beta, (−)leverage, (−)Ohlson-O, (+)Altman-Z, (−)roe-vol
    Payout        : (−)net equity issuance, (−)net debt issuance, (+)net payout
  Stocks are sorted into deciles; D10 = quality, D1 = junk. A 2×3 size×quality
  sort gives the long-short factor:
    QMJ = ½(small-quality + big-quality) − ½(small-junk + big-junk)   (value-weighted)
  and the long-only quality factor  LQ = ½(small-quality + big-quality).

Data honesty: this platform's fundamentals cache carries a subset of AFP's
sub-metrics (per-market yfinance snapshot), so each dimension is built from the
available proxies (documented in DIMENSIONS below); safety's beta/vol are derived
from the local OHLC. The score is therefore an AFP-*style* quality score, not a
tick-for-tick CMIE Prowess replication. Two of the paper's findings ARE directly
reproducible from a current snapshot and are reported here:
  * the quality **price premium** — a cross-sectional regression of log(M/B) on the
    quality score (paper Table 8): quality stocks trade at a premium; and
  * the **driver breakdown** — which dimension carries that premium (the paper finds
    profitability and payout dominate).

Usage:
  python quality_factor.py --market IN            # India (NS/BO) quality ranking
  python quality_factor.py --all --premium        # all markets + price-premium test
  python quality_factor.py --market US --portfolios --out quality_US.csv
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = os.path.expanduser("~/Downloads/code/python_files/cache_seed")
FUND = os.path.join(HERE, "fundamentals_cache.db")

# AFP dimension -> list of (column, sign). sign=+1 higher-is-better, −1 lower-is-better.
# Columns marked (derived) are computed from OHLC (beta, vol); the rest are cache columns.
DIMENSIONS = {
    "profitability": [("roe", +1), ("roa", +1), ("op_margin", +1)],          # gpoa/cfoa/gmar/acc proxied
    "growth":        [("rev_growth", +1), ("earn_growth", +1)],              # Δ-profitability proxies
    "safety":        [("de", -1), ("beta", -1), ("vol", -1)],                # low leverage / low beta / low vol
    "payout":        [("div_yield", +1)],                                    # issuance not in snapshot
}
TOP_DECILE = 0.90        # quality cut
BOT_DECILE = 0.10        # junk cut
BIG_DECILE = 0.90        # size (big) cut


# ── pure scoring core (AFP standardised ranks) ────────────────────────────────
def z_rank(s: pd.Series) -> pd.Series:
    """AFP 'standardised rank': cross-sectional rank, then z-scored. Rank-based so
    it's robust to outliers/winsorisation; missing values stay NaN."""
    r = pd.to_numeric(s, errors="coerce").rank()
    sd = r.std(ddof=0)
    if not sd or np.isnan(sd):
        return pd.Series(np.nan, index=s.index)
    return (r - r.mean()) / sd


def dimension_score(df: pd.DataFrame, specs) -> pd.Series:
    """Average of the signed standardised ranks of a dimension's sub-metrics
    (per-row mean over whichever sub-metrics are present)."""
    cols = []
    for col, sign in specs:
        if col in df.columns:
            cols.append(sign * z_rank(df[col]))
    if not cols:
        return pd.Series(np.nan, index=df.index)
    return pd.concat(cols, axis=1).mean(axis=1, skipna=True)


def quality_score(df: pd.DataFrame, dimensions=DIMENSIONS) -> pd.DataFrame:
    """Add the four dimension scores + the overall standardised quality score.
    `df` must hold the sub-metric columns; caller supplies beta/vol if using them."""
    out = df.copy()
    dim_cols = []
    for name, specs in dimensions.items():
        out[name] = dimension_score(out, specs)
        dim_cols.append(name)
    # Quality = average of the four dimensions, then re-standardised for clean deciles.
    out["quality_raw"] = out[dim_cols].mean(axis=1, skipna=True)
    out["quality"] = z_rank(out["quality_raw"])
    # 0–100 convenience score for fusion with the platform's other 0–100 signals.
    out["quality_score"] = (out["quality"].rank(pct=True) * 100).round(1)
    return out


def assign_deciles(scores: pd.Series, top=TOP_DECILE, bot=BOT_DECILE) -> pd.Series:
    """Label each stock quality / junk / mid by decile of the quality score."""
    pr = scores.rank(pct=True)
    return pd.Series(np.where(pr >= top, "quality", np.where(pr <= bot, "junk", "mid")),
                     index=scores.index)


def value_weight(mktcap: pd.Series) -> pd.Series:
    w = pd.to_numeric(mktcap, errors="coerce").clip(lower=0)
    s = w.sum()
    return (w / s) if s > 0 else pd.Series(1.0 / len(w), index=w.index)


def build_portfolios(df: pd.DataFrame) -> dict:
    """2×3 size×quality sort -> the four QMJ legs + LQ, as {name: DataFrame(ticker,weight)}.
    Value-weighted within each leg (paper Eq. 6/7)."""
    d = df.dropna(subset=["quality", "mktcap"]).copy()
    d["decile"] = assign_deciles(d["quality"])
    big_cut = pd.to_numeric(d["mktcap"], errors="coerce").rank(pct=True) >= BIG_DECILE
    d["size"] = np.where(big_cut, "big", "small")
    legs = {}
    for size in ("small", "big"):
        for q in ("quality", "junk"):
            leg = d[(d["size"] == size) & (d["decile"] == q)]
            if len(leg):
                legs[f"{size}_{q}"] = pd.DataFrame({
                    "ticker": leg["ticker"].values, "weight": value_weight(leg["mktcap"]).values})
    return legs


def qmj_combo(leg_returns: dict) -> float:
    """QMJ = ½(sq+bq) − ½(sj+bj) from a dict of leg *returns* (paper Eq. 6)."""
    g = 0.5 * (leg_returns.get("small_quality", 0) + leg_returns.get("big_quality", 0))
    j = 0.5 * (leg_returns.get("small_junk", 0) + leg_returns.get("big_junk", 0))
    return float(g - j)


def lq_combo(leg_returns: dict) -> float:
    """LQ = ½(small-quality + big-quality) (paper Eq. 7)."""
    return float(0.5 * (leg_returns.get("small_quality", 0) + leg_returns.get("big_quality", 0)))


# ── safety inputs from OHLC (beta, vol) ───────────────────────────────────────
from marketdata import clean_key as _clean


def price_risk(market: str) -> pd.DataFrame:
    """Per-ticker CAPM beta vs an equal-weight market proxy and annualised vol,
    from the local cleaned_long parquet (the 'safety' dimension's price inputs)."""
    p = os.path.join(SEED, f"cleaned_long_{market}.parquet")
    if not os.path.exists(p):
        return pd.DataFrame(columns=["key", "beta", "vol"])
    px = pd.read_parquet(p)
    wide = px.pivot_table(index="Date", columns="Symbol", values="Close", aggfunc="last")
    rets = wide.astype(float).pct_change(fill_method=None).tail(252)
    mkt = rets.mean(axis=1)
    var_m = mkt.var()
    rows = []
    for sym in rets.columns:
        r = rets[sym].dropna()
        if len(r) < 60:
            continue
        m = mkt.reindex(r.index)
        beta = (r.cov(m) / var_m) if var_m else np.nan
        rows.append({"key": _clean(sym), "beta": float(beta), "vol": float(r.std() * np.sqrt(252))})
    return pd.DataFrame(rows)


# ── data assembly ─────────────────────────────────────────────────────────────
# NOTE: the paper studies India, but this platform's global cache covers 19 OTHER
# markets (US/JP/KR/EU/CN/…); India (NS/BO) lives in the separate India repo. The
# method below is the paper's, generalised to whichever markets are in the cache.
def load_fundamentals(markets=None) -> pd.DataFrame:
    con = sqlite3.connect(FUND)
    try:
        f = pd.read_sql("SELECT * FROM fund", con)
    finally:
        con.close()
    for c in ["pe", "pb", "roe", "roa", "de", "rev_growth", "earn_growth",
              "op_margin", "div_yield", "mktcap"]:
        f[c] = pd.to_numeric(f[c], errors="coerce")
    if markets:
        f = f[f["market"].isin(markets)]
    return f


def attach_price_risk(f: pd.DataFrame) -> pd.DataFrame:
    f = f.copy()
    f["key"] = f["ticker"].map(_clean)
    pr = pd.concat([price_risk(m) for m in f["market"].unique()], ignore_index=True) \
        if len(f) else pd.DataFrame(columns=["key", "beta", "vol"])
    if len(pr):
        pr = pr.drop_duplicates("key")
        f = f.merge(pr, on="key", how="left")
    else:
        f["beta"] = np.nan; f["vol"] = np.nan
    return f


def score_universe(f: pd.DataFrame, by_market=True) -> pd.DataFrame:
    """Compute quality scores; cross-section within each market by default (quality
    is relative within a market), else pooled across all markets."""
    if by_market:
        return f.groupby("market", group_keys=False).apply(lambda g: quality_score(g))
    return quality_score(f)


# ── price-premium test (paper Table 8) ────────────────────────────────────────
def price_premium(df: pd.DataFrame) -> dict:
    """Cross-sectional regression  log(M/B) ~ quality + log(size)  with market fixed
    effects. Reproduces the paper's headline: higher quality commands a valuation
    premium. Returns the quality coefficient, its t-stat, and the implied premium
    for a +1 SD move in quality."""
    from factor_research import ols
    d = df.dropna(subset=["pb", "quality", "mktcap"]).copy()
    d = d[(d["pb"] > 0) & (d["mktcap"] > 0)]
    if len(d) < 30:
        return {"n": len(d), "quality_coef": None}
    y = np.log(d["pb"].values)                         # pb = price/book = M/B
    q = d["quality"].values
    logsz = np.log(d["mktcap"].values)
    # market fixed effects as dummies (drop first to avoid collinearity)
    dummies = pd.get_dummies(d["market"], drop_first=True).astype(float).values
    X = np.column_stack([q, logsz, dummies]) if dummies.size else np.column_stack([q, logsz])
    names = ["quality", "log_size"] + [f"mkt_{i}" for i in range(dummies.shape[1])] if dummies.size \
        else ["quality", "log_size"]
    res = ols(y, X, names)
    coef, t = res["quality"]
    # quality is a z-score (SD≈1), so the coef is ~ the log-M/B change per +1 SD:
    premium = float(np.exp(coef) - 1)
    return {"n": len(d), "quality_coef": coef, "quality_t": t,
            "mb_premium_per_sd%": round(premium * 100, 1), "R2": res["_R2"]}


def driver_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    """Correlation of each quality dimension with log(M/B) — which dimensions the
    market pays up for (paper §3.3 finds profitability & payout dominate)."""
    d = df.dropna(subset=["pb"]).copy()
    d = d[d["pb"] > 0]
    logmb = np.log(d["pb"])
    rows = []
    for dim in DIMENSIONS:
        if dim in d:
            corr = d[dim].corr(logmb)
            rows.append({"dimension": dim, "corr_with_logMB": round(float(corr), 3)})
    return pd.DataFrame(rows).sort_values("corr_with_logMB", ascending=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default=None, help="market code present in the cache, e.g. US, JP, KR, CN, DE")
    ap.add_argument("--all", action="store_true", help="score every market")
    ap.add_argument("--pooled", action="store_true", help="one global cross-section (default: per-market)")
    ap.add_argument("--premium", action="store_true", help="run the price-premium regression (Table 8)")
    ap.add_argument("--portfolios", action="store_true", help="show LQ / QMJ portfolio legs")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--out", default=None, help="write ticker,quality_score CSV (for meta_screen)")
    args = ap.parse_args()

    if not os.path.exists(FUND):
        raise SystemExit("no fundamentals_cache.db — run fundamentals_global.py first")

    markets = [args.market] if args.market else None
    f = load_fundamentals(markets)
    if f.empty:
        raise SystemExit(f"no fundamentals for {args.market or 'any market'}")
    f = attach_price_risk(f)
    scored = score_universe(f, by_market=not args.pooled)

    tag = args.market or ("all markets" if (args.all or True) else "")
    ranked = scored.dropna(subset=["quality"]).sort_values("quality", ascending=False)
    print(f"\n=== AFP/QMJ QUALITY RANKING — {tag} "
          f"({len(ranked)} scored) ===", file=sys.stderr)
    print(f"  {'mkt':4}{'ticker':14}{'prof':>6}{'grow':>6}{'safe':>6}{'pay':>6}"
          f"{'QUAL':>7}{'decile':>8}")
    ranked["decile"] = assign_deciles(ranked["quality"])
    for _, r in ranked.head(args.top).iterrows():
        print(f"  {str(r['market']):4}{str(r['ticker']):14}"
              f"{r['profitability']:>6.2f}{r['growth']:>6.2f}{r['safety']:>6.2f}"
              f"{r['payout']:>6.2f}{r['quality']:>7.2f}{r['decile']:>8}")

    if args.portfolios:
        legs = build_portfolios(scored)
        print("\n=== QMJ portfolio legs (value-weighted, 2×3 size×quality) ===")
        for nm, leg in legs.items():
            print(f"  {nm:14} {len(leg):>3} names, top: "
                  f"{', '.join(leg.sort_values('weight', ascending=False)['ticker'].head(5))}")
        print("  LQ = ½(small_quality + big_quality); "
              "QMJ = ½(small_q+big_q) − ½(small_j+big_j)")

    if args.premium:
        pp = price_premium(scored)
        print("\n=== QUALITY PRICE PREMIUM (paper Table 8: log(M/B) ~ quality + size + mkt-FE) ===")
        if pp.get("quality_coef") is None:
            print(f"  too few firms ({pp['n']})")
        else:
            print(f"  quality coef = {pp['quality_coef']}  (t = {pp['quality_t']}),  R² = {pp['R2']}")
            print(f"  => a +1 SD increase in quality is associated with a "
                  f"{pp['mb_premium_per_sd%']}% higher M/B  (paper: +23.6%)")
            sig = "SIGNIFICANT" if abs(pp["quality_t"]) > 2 else "not significant"
            print(f"  quality premium is {sig} (|t|>2)")
        print("\n  driver breakdown — corr of each dimension with log(M/B):")
        print(driver_breakdown(scored).to_string(index=False))

    if args.out:
        scored[["ticker", "quality_score"]].dropna().to_csv(args.out, index=False)
        print(f"\n  wrote {args.out} (ticker,quality_score for meta_screen --quality)",
              file=sys.stderr)


if __name__ == "__main__":
    main()
