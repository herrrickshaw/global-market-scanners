#!/usr/bin/env python3
"""
warehouse.py
------------
A DuckDB analytical layer over all the platform's data — one SQL surface to
update, process, filter, and depict results across the whole system, without
copying data. DuckDB reads the parquets directly and attaches the SQLite result
DBs, so views always reflect the latest files (easy "update" = just re-run the
producers; the warehouse sees the new data).

Two-tier design (Modern Data Architecture Blueprint):
  Cassandra (market_store)  = operational store: OHLC cache, CDC source, streaming.
  DuckDB    (this)          = analytical / serving layer: fast filtering & rollups.

Unified views:
  ohlc          all 19 markets' daily OHLC (from cleaned_long_*.parquet, market column)
  companies     industry/peer dataset (companies_industry.parquet)
  fundamentals  yfinance fundamentals (fundamentals_cache.db)
  dvm_global    technical DVM/Trendlyne metrics per stock (dvm_global.db)
  dvm_composite global GGG/GGB/BBG classification (dvm_composite.db)
  viability     screen-viability summary (viability_summary.db)

Usage:
  python warehouse.py --build                       # (re)create the warehouse views
  python warehouse.py --show ggg_global             # pre-built result views
  python warehouse.py --show markets
  python warehouse.py --filter "roe>15 and de<1 and M>=70"   # ad-hoc DVM filter
  python warehouse.py --sql "SELECT market, count(*) FROM ohlc GROUP BY 1"
"""

from __future__ import annotations

import argparse
import os
import sys

import duckdb

import incremental   # F9.1 partition-incremental refresh (pure pandas helpers)

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = os.path.expanduser("~/Downloads/code/python_files/cache_seed")
DB = os.path.join(HERE, "market.duckdb")

# SQLite result DBs to attach (alias -> (file, {view: table}))
SQLITE_SOURCES = {
    "fund_db":   ("fundamentals_cache.db", {"fundamentals": "fund"}),
    "dvmg_db":   ("dvm_global.db", {"dvm_global": "dvm_global"}),
    "dvmc_db":   ("dvm_composite.db", {"dvm_composite": "dvm_composite"}),
    "viab_db":   ("viability_summary.db", {"viability": "market_screen_summary"}),
}


def build(con):
    con.execute("INSTALL sqlite; LOAD sqlite;")
    # OHLC across all markets, market derived from the parquet filename
    glob = os.path.join(SEED, "cleaned_long_*.parquet")
    con.execute(f"""
        CREATE OR REPLACE VIEW ohlc AS
        SELECT Symbol AS ticker, Date, Open, High, Low, Close, Volume,
               regexp_extract(filename, 'cleaned_long_([A-Za-z]+)\\.parquet', 1) AS market
        FROM read_parquet('{glob}', filename=true)
    """)
    ci = os.path.join(HERE, "companies_industry.parquet")
    if os.path.exists(ci):
        con.execute(f"CREATE OR REPLACE VIEW companies AS SELECT * FROM read_parquet('{ci}')")
    for alias, (fn, views) in SQLITE_SOURCES.items():
        path = os.path.join(HERE, fn)
        if not os.path.exists(path):
            print(f"  [skip] {fn} not present", file=sys.stderr); continue
        con.execute(f"ATTACH IF NOT EXISTS '{path}' AS {alias} (TYPE sqlite)")
        for view, table in views.items():
            try:
                if view == "fundamentals":   # yfinance can store 'Infinity' as text -> coerce
                    con.execute(f"""CREATE OR REPLACE VIEW fundamentals AS SELECT ticker, market,
                        TRY_CAST(pe AS DOUBLE) pe, TRY_CAST(pb AS DOUBLE) pb,
                        TRY_CAST(roe AS DOUBLE) roe, TRY_CAST(roa AS DOUBLE) roa,
                        TRY_CAST(de AS DOUBLE) de, TRY_CAST(rev_growth AS DOUBLE) rev_growth,
                        TRY_CAST(earn_growth AS DOUBLE) earn_growth, TRY_CAST(op_margin AS DOUBLE) op_margin,
                        TRY_CAST(div_yield AS DOUBLE) div_yield, TRY_CAST(mktcap AS DOUBLE) mktcap,
                        sector FROM {alias}.{table}""")
                else:
                    con.execute(f"CREATE OR REPLACE VIEW {view} AS SELECT * FROM {alias}.{table}")
            except Exception as e:
                print(f"  [skip] {view}: {e}", file=sys.stderr)
    print("  warehouse built:", [r[0] for r in con.execute("SHOW TABLES").fetchall()],
          file=sys.stderr)


# Pre-built "depict results" queries
SHOWS = {
    "markets": "SELECT market, count(DISTINCT ticker) AS tickers, count(*) AS bars, "
               "min(Date) AS from_, max(Date) AS to_ FROM ohlc GROUP BY 1 ORDER BY tickers DESC",
    "ggg_global": "SELECT market, ticker, D, V, M, composite, label FROM dvm_composite "
                  "WHERE code='GGG' ORDER BY composite DESC LIMIT 25",
    "dvm_dist": "SELECT code, label, count(*) n FROM dvm_composite GROUP BY 1,2 ORDER BY n DESC",
    "momentum_by_market": "SELECT market, count(*) scored, "
                          "round(avg(M),1) avg_M, sum(CASE WHEN M>=70 THEN 1 ELSE 0 END) hi_mom "
                          "FROM dvm_global GROUP BY 1 ORDER BY hi_mom DESC",
    "high_roe_low_de": "SELECT market, ticker, round(roe,1) roe, round(de,2) de, round(pe,1) pe, sector "
                       "FROM fundamentals WHERE roe>15 AND de<1 AND de IS NOT NULL "
                       "ORDER BY roe DESC LIMIT 25",
    "industry_segments": "SELECT segment, n_companies FROM (SELECT * FROM companies) "
                         "USING SAMPLE 0 ROWS",  # placeholder; companies has list cols
}


def refresh_ohlc_partition(market: str, new_parquet: str) -> dict:
    """F9.1: append only genuinely new dates from `new_parquet` into a market's
    cleaned_long parquet (per-symbol high-water mark), instead of rebuilding it.
    Returns a small summary of what was appended."""
    import pandas as pd
    base_path = os.path.join(SEED, f"cleaned_long_{market}.parquet")
    base = pd.read_parquet(base_path) if os.path.exists(base_path) else pd.DataFrame()
    new = pd.read_parquet(new_parquet)
    merged = incremental.append_new_dates(base, new, date_col="Date", key_col="Symbol")
    added = len(merged) - len(base)
    merged.to_parquet(base_path, index=False, compression="snappy")
    return {"market": market, "rows_before": len(base), "rows_added": added,
            "rows_after": len(merged)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--show", choices=[k for k in SHOWS if k != "industry_segments"])
    ap.add_argument("--filter", help="ad-hoc predicate over the dvm_composite⋈fundamentals join")
    ap.add_argument("--sql", help="run arbitrary SQL")
    ap.add_argument("--refresh-ohlc", nargs=2, metavar=("MARKET", "NEW_PARQUET"),
                    help="F9.1: incrementally append new dates into a market's parquet")
    args = ap.parse_args()

    if args.refresh_ohlc:
        print(refresh_ohlc_partition(*args.refresh_ohlc)); return

    con = duckdb.connect(DB)
    # DuckDB sqlite ATTACHments are per-session, so (re)build views every run — it's
    # just cheap view definitions over the live parquets/SQLite (that's the "update").
    build(con)

    con.execute("SET max_expression_depth=10000")
    if args.sql:
        print(con.execute(args.sql).df().to_string(index=False))
    elif args.show:
        print(con.execute(SHOWS[args.show]).df().to_string(index=False))
    elif args.filter:
        q = (f"SELECT c.market, c.ticker, c.D, c.V, c.M, c.composite, c.code, "
             f"f.roe, f.de, f.pe, f.sector FROM dvm_composite c "
             f"LEFT JOIN fundamentals f ON c.ticker=f.ticker "
             f"WHERE {args.filter} ORDER BY c.composite DESC LIMIT 30")
        print(con.execute(q).df().to_string(index=False))
    else:
        # default: quick health summary
        print("=== warehouse tables ===")
        for r in con.execute("SHOW TABLES").fetchall():
            try:
                n = con.execute(f"SELECT count(*) FROM {r[0]}").fetchone()[0]
                print(f"  {r[0]:16} {n:>10,} rows")
            except Exception:
                print(f"  {r[0]:16} (view)")
    con.close()


if __name__ == "__main__":
    main()
