#!/usr/bin/env python3
"""
enrich_industries.py
--------------------
Fills missing sector/industry for monitored companies by querying yfinance,
writing a resumable cache (industry_cache.json) that build_industry_parquet.py
merges in. Targets the markets with no industry data on disk (India, Korea by
default; pass --all to also enrich every still-unclassified ticker).

Resumable: already-cached tickers are skipped, so it can be re-run/interrupted.
Threaded with a modest worker count + jitter to stay under yfinance rate limits.

Usage:
  python enrich_industries.py                 # India + Korea
  python enrich_industries.py --all           # every unclassified ticker
  python enrich_industries.py --rebuild       # re-run the parquet build at the end
"""

import argparse
import json
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "industry_cache.json")
PARQUET = os.path.join(HERE, "companies_industry.parquet")


def load_cache():
    if os.path.exists(CACHE):
        try:
            with open(CACHE) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cache(cache):
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f)
    os.replace(tmp, CACHE)


def fetch_one(ticker, retries=4):
    """Fetch sector/industry via the governed client (throttle + adaptive backoff)."""
    from apiclient import yf_info
    try:
        info = yf_info(ticker)
        sec, ind = info.get("sector"), info.get("industry")
        if sec or ind:
            return ticker, {"sector": sec, "industry": ind}
    except Exception as e:
        return ticker, {"sector": None, "industry": None, "_err": str(e)[:80]}
    return ticker, {"sector": None, "industry": None, "_err": "empty"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true",
                    help="enrich every unclassified ticker, not just India/Korea")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--rebuild", action="store_true",
                    help="run build_industry_parquet.py after enriching")
    args = ap.parse_args()

    df = pd.read_parquet(PARQUET)
    if args.all:
        need = df[df["industry"].isna()]
    else:
        need = df[df["country"].isin(["India", "South Korea"])]
    tickers = [t for t in need["ticker"].dropna().unique() if t]

    cache = load_cache()
    todo = [t for t in tickers if t not in cache]
    print(f"targets={len(tickers)}  cached={len(tickers)-len(todo)}  to_fetch={len(todo)}",
          file=sys.stderr, flush=True)

    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {}
        for t in todo:
            futs[ex.submit(fetch_one, t)] = t
            time.sleep(random.uniform(0.01, 0.05))  # light stagger
        for fut in as_completed(futs):
            ticker, data = fut.result()
            cache[ticker] = data
            done += 1
            if done % 100 == 0:
                save_cache(cache)
                rate = done / max(time.time() - t0, 1)
                hits = sum(1 for t in todo if cache.get(t, {}).get("industry"))
                print(f"  {done}/{len(todo)}  hits={hits}  {rate:.1f}/s", file=sys.stderr, flush=True)

    save_cache(cache)
    hits = sum(1 for t in tickers if cache.get(t, {}).get("industry"))
    print(f"DONE enrich: fetched={done}  industry_hits={hits}/{len(tickers)}  "
          f"elapsed={int(time.time()-t0)}s  cache={CACHE}", file=sys.stderr, flush=True)

    if args.rebuild:
        print("rebuilding parquet with enrichment...", file=sys.stderr, flush=True)
        os.system(f"cd {HERE} && python3 build_industry_parquet.py")


if __name__ == "__main__":
    main()
