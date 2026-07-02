#!/usr/bin/env python3
"""
unlisted_valuation.py
---------------------
unlisted_enrichment.py collects private/unlisted firms and tags them to an
industry, but that enrichment was never *joined* to listed multiples — so it
couldn't answer the obvious question: "what might this private firm be worth?"
This does comparable-company (comps) valuation: for each unlisted firm, find its
listed peers in the same industry (companies_industry.parquet ⋈ fundamentals),
take the peer-median trading multiple (P/E, P/B), and apply it to a supplied
financial to imply a valuation range (median plus inter-quartile band).

Pure valuation core (apply a multiple to a metric), unit-testable; the CLI reads
unlisted_firms.parquet and fundamentals_cache.db.

Usage:
  python unlisted_valuation.py --industry "Advertising Agencies" --earnings 5e7
  python unlisted_valuation.py --list                      # industries with peer coverage
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
COMPANIES = os.path.join(HERE, "companies_industry.parquet")
FUND = os.path.join(HERE, "fundamentals_cache.db")
UNLISTED = os.path.join(HERE, "unlisted_firms.parquet")


# ── pure comps core ───────────────────────────────────────────────────────────
def implied_value(metric_value: float, multiple: float) -> float:
    """value = metric * multiple (e.g. earnings * P/E, book * P/B)."""
    return float(metric_value) * float(multiple)


def peer_multiple_band(multiples) -> dict:
    """Robust central multiple + IQR band from a peer set (drops non-positive
    and non-finite values, which are meaningless for a trading multiple)."""
    m = np.asarray([x for x in multiples if x is not None], dtype=float)
    m = m[np.isfinite(m) & (m > 0)]
    if m.size == 0:
        return {"n": 0, "median": None, "q1": None, "q3": None}
    return {"n": int(m.size), "median": float(np.median(m)),
            "q1": float(np.quantile(m, 0.25)), "q3": float(np.quantile(m, 0.75))}


def value_range(metric_value: float, band: dict) -> dict:
    """Apply a multiple band to a financial metric -> implied value range."""
    if not band or band.get("median") is None:
        return {"low": None, "mid": None, "high": None, "n_peers": 0}
    return {"low": implied_value(metric_value, band["q1"]),
            "mid": implied_value(metric_value, band["median"]),
            "high": implied_value(metric_value, band["q3"]),
            "n_peers": band["n"]}


# ── data assembly ─────────────────────────────────────────────────────────────
def load_peers() -> pd.DataFrame:
    comp = pd.read_parquet(COMPANIES)[["ticker", "industry", "sector"]]
    con = sqlite3.connect(FUND)
    try:
        fund = pd.read_sql("SELECT ticker, pe, pb, roe FROM fund", con)
    finally:
        con.close()
    for c in ("pe", "pb", "roe"):
        fund[c] = pd.to_numeric(fund[c], errors="coerce")
    return comp.merge(fund, on="ticker", how="inner")


def industry_band(peers: pd.DataFrame, industry: str, kind: str = "pe") -> dict:
    sub = peers[peers["industry"] == industry]
    return peer_multiple_band(sub[kind].tolist())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--industry", default=None)
    ap.add_argument("--earnings", type=float, default=None, help="net earnings (for P/E comps)")
    ap.add_argument("--book", type=float, default=None, help="book value (for P/B comps)")
    ap.add_argument("--list", action="store_true", help="list industries with peer coverage")
    args = ap.parse_args()

    if not (os.path.exists(COMPANIES) and os.path.exists(FUND)):
        raise SystemExit("need companies_industry.parquet + fundamentals_cache.db")
    peers = load_peers()

    if args.list or not args.industry:
        cov = (peers.dropna(subset=["pe"]).groupby("industry")["pe"]
               .agg(peers="count", median_pe="median").reset_index()
               .sort_values("peers", ascending=False))
        cov = cov[cov["peers"] >= 3]
        print(f"=== industries with >=3 listed peers ({len(cov)}) ===")
        for _, r in cov.head(30).iterrows():
            print(f"  {str(r['industry'])[:40]:40} peers={int(r['peers']):>4}  "
                  f"median P/E={r['median_pe']:.1f}")
        if not args.industry:
            return

    pe_band = industry_band(peers, args.industry, "pe")
    pb_band = industry_band(peers, args.industry, "pb")
    print(f"\n=== comps for industry: {args.industry} ===")
    print(f"  P/E band (n={pe_band['n']}): "
          f"q1={pe_band['q1']:.1f} med={pe_band['median']:.1f} q3={pe_band['q3']:.1f}"
          if pe_band["median"] else "  P/E band: no peer coverage")
    if args.earnings and pe_band["median"]:
        v = value_range(args.earnings, pe_band)
        print(f"  implied value @ earnings {args.earnings:,.0f}: "
              f"{v['low']:,.0f} — {v['mid']:,.0f} — {v['high']:,.0f} "
              f"(low—mid—high, {v['n_peers']} peers)")
    if args.book and pb_band["median"]:
        v = value_range(args.book, pb_band)
        print(f"  implied value @ book {args.book:,.0f}: "
              f"{v['low']:,.0f} — {v['mid']:,.0f} — {v['high']:,.0f}")

    # If we have unlisted firms tagged to this industry, list them as candidates.
    if os.path.exists(UNLISTED):
        u = pd.read_parquet(UNLISTED)
        seg_col = "segment" if "segment" in u.columns else None
        if seg_col:
            hits = u[u[seg_col].astype(str).str.contains(args.industry, case=False, na=False)]
            if len(hits):
                print(f"\n  unlisted firms tagged to this space ({len(hits)}):", file=sys.stderr)
                for nm in hits["company_name"].head(10):
                    print(f"    - {nm}")


if __name__ == "__main__":
    main()
