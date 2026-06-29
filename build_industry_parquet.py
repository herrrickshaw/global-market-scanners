#!/usr/bin/env python3
"""
build_industry_parquet.py
-------------------------
Consolidates the latest per-country market-scan outputs into a single
parquet dataset of company names segmented by industry, with global
industry peers attached.

Outputs (written next to this script, parquet = already compressed/columnar):
  companies_industry.parquet   one row per monitored company:
      company_name, ticker, code, country, exchange, sector, industry,
      segment (industry or sector fallback), peer_count, global_peers (list, capped)
  industry_segments.parquet    one row per industry segment:
      segment, n_companies, countries, company_names (full list)

Industry source priority:
  US      -> scan_summary_*.xlsx (yfinance granular `industry` + `sector`)
  Japan   -> scan All_Stocks `Sector`
  Europe  -> scan All_Stocks `Sector`
  India   -> none on disk  (filled later by enrich_industries.py via yfinance)
  Korea   -> none on disk  (filled later by enrich_industries.py via yfinance)

An optional enrichment cache (industry_cache.json: {yf_ticker: {"sector":..,
"industry":..}}) is merged in when present, so re-running after the background
yfinance enrichment upgrades India/Korea (and fills any gaps) automatically.
"""

import glob
import json
import os
import sys

import pandas as pd

DL = os.path.expanduser("~/Downloads")
HERE = os.path.dirname(os.path.abspath(__file__))
PEER_CAP = 50  # cap peer-name list stored per company row to keep file small


def latest(pattern):
    hits = sorted(glob.glob(os.path.join(DL, pattern), recursive=True))
    return hits[-1] if hits else None


SCANS = {
    "USA":         "data/us_full_scan/**/us_full_scan_*.xlsx",
    "India":       "data/**/indian_full_scan_*.xlsx",
    "Japan":       "data/japan_scan/**/japan_market_scan_*.xlsx",
    "South Korea": "data/korea_scan/**/korea_market_scan_*.xlsx",
    "Europe":      "data/european_scan/**/european_market_scan_*.xlsx",
}
SCAN_SUMMARY = "data/**/scan_summary_*.xlsx"


def load_us_industry_map():
    """symbol -> (name, exchange, sector, industry) from scan_summary."""
    f = latest(SCAN_SUMMARY)
    if not f:
        return {}
    df = pd.read_excel(f, sheet_name=0)
    out = {}
    for _, r in df.iterrows():
        sym = str(r.get("symbol", "")).strip()
        if not sym:
            continue
        out[sym] = (
            r.get("name"), r.get("exchange"),
            r.get("sector"), r.get("industry"),
        )
    return out


def collect():
    rows = []
    us_map = load_us_industry_map()
    print(f"  scan_summary US rows w/ metadata: {len(us_map)}", file=sys.stderr)

    for country, pat in SCANS.items():
        path = latest(pat)
        if not path:
            print(f"  [skip] {country}: no scan found", file=sys.stderr)
            continue
        a = pd.ExcelFile(path).parse("All_Stocks")
        print(f"  {country}: {len(a)} rows from {os.path.basename(path)}", file=sys.stderr)

        for _, r in a.iterrows():
            name = ticker = code = exchange = sector = industry = None

            if country == "USA":
                sym = str(r.get("Symbol", "")).strip()
                ticker = code = sym
                meta = us_map.get(sym)
                if meta:
                    name, exchange, sector, industry = meta
                name = name or sym
                exchange = exchange or "US"

            elif country == "India":
                sym = str(r.get("Symbol", "")).strip()
                suf = str(r.get("Suffix", "")).strip()
                code = sym
                ticker = f"{sym}{suf}"
                name = sym  # India scan carries no name; enrichment may improve
                exchange = "NSE" if suf == ".NS" else ("BSE" if suf == ".BO" else "IN")

            elif country == "Japan":
                code = str(r.get("Code", "")).strip()
                ticker = str(r.get("YF_Ticker", "")).strip()
                name = r.get("Name")
                sector = r.get("Sector")
                exchange = "TSE"

            elif country == "South Korea":
                code = str(r.get("Code", "")).strip()
                ticker = str(r.get("YF_Ticker", "")).strip()
                name = r.get("Name")
                exchange = str(r.get("Market", "KRX")).strip() or "KRX"

            elif country == "Europe":
                code = ticker = str(r.get("Symbol", "")).strip()
                name = r.get("Name")
                sector = r.get("Sector")
                exchange = "EU"

            rows.append({
                "company_name": (str(name).strip() if pd.notna(name) else code),
                "ticker": ticker, "code": code, "country": country,
                "exchange": exchange,
                "sector": sector if pd.notna(sector) else None,
                "industry": industry if pd.notna(industry) else None,
            })
    return pd.DataFrame(rows)


def merge_cache(df):
    """Overlay yfinance enrichment cache if the background job has produced one."""
    cache_path = os.path.join(HERE, "industry_cache.json")
    if not os.path.exists(cache_path):
        print("  (no industry_cache.json yet — India/Korea industry will be blank)",
              file=sys.stderr)
        return df
    with open(cache_path) as fh:
        cache = json.load(fh)
    print(f"  merging enrichment cache: {len(cache)} tickers", file=sys.stderr)
    for i, r in df.iterrows():
        c = cache.get(r["ticker"])
        if c:
            if not r["sector"] and c.get("sector"):
                df.at[i, "sector"] = c["sector"]
            if not r["industry"] and c.get("industry"):
                df.at[i, "industry"] = c["industry"]
    return df


def main():
    print("Building company/industry parquet...", file=sys.stderr)
    df = collect()
    df = merge_cache(df)

    # dedup by ticker (a name can appear once per market)
    df = df.drop_duplicates(subset=["ticker", "country"]).reset_index(drop=True)

    # segment = granular industry if present, else broad sector, else 'Unclassified'
    df["segment"] = df["industry"].fillna(df["sector"]).fillna("Unclassified")

    # global peers within the same segment
    members = df.groupby("segment")["company_name"].apply(list).to_dict()
    counts = df.groupby("segment")["company_name"].size().to_dict()

    def peers_for(row):
        all_members = members.get(row["segment"], [])
        peers = [m for m in all_members if m != row["company_name"]]
        return peers[:PEER_CAP]

    df["peer_count"] = df["segment"].map(lambda s: max(counts.get(s, 1) - 1, 0))
    df["global_peers"] = df.apply(peers_for, axis=1)

    df = df.sort_values(["segment", "country", "company_name"]).reset_index(drop=True)

    out1 = os.path.join(HERE, "companies_industry.parquet")
    df.to_parquet(out1, index=False, compression="snappy")

    # segment-level table
    seg = (df.groupby("segment")
             .agg(n_companies=("company_name", "size"),
                  countries=("country", lambda s: sorted(set(s))),
                  company_names=("company_name", list))
             .reset_index()
             .sort_values("n_companies", ascending=False))
    out2 = os.path.join(HERE, "industry_segments.parquet")
    seg.to_parquet(out2, index=False, compression="snappy")

    # report
    classified = (df["segment"] != "Unclassified").sum()
    print(f"\nDONE", file=sys.stderr)
    print(f"  companies: {len(df)}  classified: {classified}  "
          f"unclassified: {len(df) - classified}", file=sys.stderr)
    print(f"  segments : {seg.shape[0]}", file=sys.stderr)
    print(f"  -> {out1} ({os.path.getsize(out1)//1024} KB)", file=sys.stderr)
    print(f"  -> {out2} ({os.path.getsize(out2)//1024} KB)", file=sys.stderr)
    print("\nTop 12 segments:", file=sys.stderr)
    print(seg.head(12)[["segment", "n_companies"]].to_string(index=False), file=sys.stderr)


if __name__ == "__main__":
    main()
