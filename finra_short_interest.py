#!/usr/bin/env python3
"""
finra_short_interest.py
-----------------------
Fills the one gap `crowding.py` and FRONTIER_GAPS.md state as "needs a vendor feed":
**true short interest / days-to-cover** for US equities. FINRA publishes the
twice-monthly consolidated short-interest file keyless at cdn.finra.org (no key, no
login); this collector downloads one settlement date, normalises it and writes a
small gzipped CSV under data/.

  settlement files: https://cdn.finra.org/equity/otcmarket/biweekly/shrtYYYYMMDD.csv

Honest limits: short interest is only reported at settlement dates (~mid and end of
month) and published with a lag; it is not point-in-time-stamped by FINRA at the
ticker level beyond `settlementDate`, so backtest use must add a publication lag.

Usage:
  python finra_short_interest.py --date 2026-09-30        # -> data/finra_short_interest_20260930.csv.gz
  python finra_short_interest.py --date 2026-09-30 --top 15
"""

from __future__ import annotations

import argparse
import io
import os
import urllib.request

import pandas as pd

URL = "https://cdn.finra.org/equity/otcmarket/biweekly/shrt{ymd}.csv"
UA = "global-market-scanners research (open data collector)"
KEEP = {
    "symbolCode": "symbol", "issueName": "name", "marketClassCode": "market_class",
    "currentShortPositionQuantity": "short_shares",
    "previousShortPositionQuantity": "prev_short_shares",
    "averageDailyVolumeQuantity": "adv_shares", "daysToCoverQuantity": "days_to_cover",
    "changePercent": "chg_pct", "settlementDate": "settlement_date",
}


def parse(text: str) -> pd.DataFrame:
    """Pure parser: raw FINRA pipe-delimited text -> tidy frame (offline-testable)."""
    df = pd.read_csv(io.StringIO(text), sep="|", dtype={"symbolCode": str})
    return df[list(KEEP)].rename(columns=KEEP)


def fetch(date: str) -> pd.DataFrame:
    ymd = date.replace("-", "")
    req = urllib.request.Request(URL.format(ymd=ymd), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return parse(r.read().decode("utf-8", "replace"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--date", required=True, help="settlement date YYYY-MM-DD")
    ap.add_argument("--out-dir", default=os.path.join(os.path.dirname(__file__), "data"))
    ap.add_argument("--top", type=int, default=0, help="print top-N days-to-cover (ADV>=1M)")
    a = ap.parse_args()
    df = fetch(a.date)
    os.makedirs(a.out_dir, exist_ok=True)
    path = os.path.join(a.out_dir, f"finra_short_interest_{a.date.replace('-', '')}.csv.gz")
    df.to_csv(path, index=False, compression="gzip")
    print(f"{len(df)} rows -> {path} ({os.path.getsize(path)/1e3:.0f} KB)")
    if a.top:
        liq = df[df.adv_shares >= 1_000_000].sort_values("days_to_cover", ascending=False)
        print(liq.head(a.top)[["symbol", "market_class", "short_shares", "days_to_cover"]].to_string(index=False))


if __name__ == "__main__":
    main()
