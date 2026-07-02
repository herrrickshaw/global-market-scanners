#!/usr/bin/env python3
"""
data_quality.py
---------------
Governance (togaf.py) verifies the *code* and the integrity manifest verifies the
*files haven't been tampered with* — but nothing checked the *data itself* for
staleness, nulls, or absurd values. This is data observability: a set of pure
rules (freshness, completeness, outliers, monotonic dates) run over the platform's
data sources, with a CLI that prints a report and exits non-zero on failure, so it
can gate CI just like the tests do.

Pure rule functions are unit-testable; the CLI applies them to the cleaned_long
parquets and the fundamentals cache.

Usage:
  python data_quality.py                    # full report, exit 1 if any FAIL
  python data_quality.py --max-stale-days 20
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = os.path.expanduser("~/Downloads/code/python_files/cache_seed")


# ── pure rules ────────────────────────────────────────────────────────────────
def staleness_days(dates, asof: str | None = None) -> int:
    """Calendar days between the newest date in the series and `asof` (today)."""
    d = pd.to_datetime(pd.Series(list(dates))).max()
    ref = pd.Timestamp(asof) if asof else pd.Timestamp(dt.date.today())
    return int((ref - d).days)


def null_rate(series) -> float:
    s = pd.Series(list(series))
    return float(s.isna().mean()) if len(s) else 1.0


def outlier_rate(values, k: float = 8.0) -> float:
    """Fraction of finite values beyond k robust-MADs from the median (fat k=8
    so only truly absurd points, e.g. price feed glitches, count)."""
    v = pd.to_numeric(pd.Series(list(values)), errors="coerce").dropna().values
    if v.size < 5:
        return 0.0
    med = np.median(v)
    mad = np.median(np.abs(v - med)) or (np.std(v) or 1.0)
    z = np.abs(v - med) / (1.4826 * mad)
    return float((z > k).mean())


def is_monotonic_dates(dates) -> bool:
    d = pd.to_datetime(pd.Series(list(dates)))
    return bool(d.is_monotonic_increasing) if len(d) > 1 else True


def evaluate(name: str, checks: dict, thresholds: dict) -> dict:
    """checks: {rule: value}; thresholds: {rule: (op, limit)} where op in
    {'<=','>=','=='}. Returns {source, rule, value, limit, status}."""
    rows = []
    ops = {"<=": lambda a, b: a <= b, ">=": lambda a, b: a >= b,
           "==": lambda a, b: a == b}
    for rule, val in checks.items():
        op, limit = thresholds[rule]
        ok = ops[op](val, limit)
        rows.append({"source": name, "rule": rule, "value": val,
                     "limit": f"{op}{limit}", "status": "PASS" if ok else "FAIL"})
    return rows


# ── data-source scans ─────────────────────────────────────────────────────────
def scan_parquet(path: str, market: str, max_stale: int, max_null: float,
                 max_outlier: float) -> list:
    df = pd.read_parquet(path)
    checks = {
        "stale_days": staleness_days(df["Date"]),
        "close_null_rate": null_rate(df["Close"]),
        "close_outlier_rate": outlier_rate(df["Close"]),
        "dates_sorted_within_symbol": bool(
            df.sort_values(["Symbol", "Date"]).groupby("Symbol")["Date"]
              .apply(lambda s: s.is_monotonic_increasing).all()),
    }
    thr = {"stale_days": ("<=", max_stale), "close_null_rate": ("<=", max_null),
           "close_outlier_rate": ("<=", max_outlier),
           "dates_sorted_within_symbol": ("==", True)}
    return evaluate(f"ohlc:{market}", checks, thr)


def scan_fundamentals(db: str, max_null: float) -> list:
    con = sqlite3.connect(db)
    try:
        f = pd.read_sql("SELECT * FROM fund", con)
    finally:
        con.close()
    for c in ("roe", "de", "pe"):
        if c in f:
            f[c] = pd.to_numeric(f[c], errors="coerce")
    checks = {"roe_null_rate": null_rate(f.get("roe", [])),
              "pe_outlier_rate": outlier_rate(f.get("pe", []))}
    thr = {"roe_null_rate": ("<=", max_null), "pe_outlier_rate": ("<=", 0.05)}
    return evaluate("fundamentals", checks, thr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-stale-days", type=int, default=30)
    ap.add_argument("--max-null-rate", type=float, default=0.20)
    ap.add_argument("--max-outlier-rate", type=float, default=0.01)
    args = ap.parse_args()

    rows = []
    import glob
    for p in sorted(glob.glob(os.path.join(SEED, "cleaned_long_*.parquet"))):
        mkt = os.path.basename(p).split("cleaned_long_")[1].split(".")[0]
        try:
            rows += scan_parquet(p, mkt, args.max_stale_days, args.max_null_rate,
                                 args.max_outlier_rate)
        except Exception as e:
            rows.append({"source": f"ohlc:{mkt}", "rule": "readable", "value": str(e),
                         "limit": "==True", "status": "FAIL"})
    fdb = os.path.join(HERE, "fundamentals_cache.db")
    if os.path.exists(fdb):
        try:
            rows += scan_fundamentals(fdb, args.max_null_rate)
        except Exception as e:
            rows.append({"source": "fundamentals", "rule": "readable", "value": str(e),
                         "limit": "==True", "status": "FAIL"})

    rep = pd.DataFrame(rows)
    if rep.empty:
        print("no data sources found to check", file=sys.stderr); return
    fails = rep[rep["status"] == "FAIL"]
    print("=== DATA QUALITY REPORT ===")
    print(rep.to_string(index=False))
    print(f"\n  {len(rep)-len(fails)}/{len(rep)} checks PASS; {len(fails)} FAIL")
    sys.exit(1 if len(fails) else 0)


if __name__ == "__main__":
    main()
