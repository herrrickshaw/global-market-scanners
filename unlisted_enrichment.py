#!/usr/bin/env python3
"""
unlisted_enrichment.py
----------------------
Adds UNLISTED (private) firms to the sector dataset, for the "profitable
segments" that pass the scan filters (Triple-Hits). Listed companies come from
the market scanners; this fills in their private peers in the same industry.

Sources (pick with --source):
  wikidata   FREE, no key — queries Wikidata for Indian firms in a segment and
             keeps those with NO stock-exchange listing (i.e. unlisted). Works now.
  dnb        Dun & Bradstreet Direct+ Company Search.  Needs DNB_KEY + DNB_SECRET.
  lusha      Lusha Prospecting company search.          Needs LUSHA_API_KEY.
  apollo     Apollo organization search.                Needs APOLLO_API_KEY
             (paid plan — the free plan blocks /mixed_companies/search).

No secrets live in this file — keys are read from environment variables only.

Output: unlisted_firms.parquet  (company_name, country, segment, source,
        website, employees, revenue, city, is_listed=False, fetched_at)
        plus an optional merge into companies_industry.parquet.

Usage:
  python unlisted_enrichment.py --source wikidata --geo India
  python unlisted_enrichment.py --source dnb --geo India       # needs keys
  python unlisted_enrichment.py --source wikidata --merge      # also append to main parquet
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN_PARQUET = os.path.join(HERE, "companies_industry.parquet")
OUT_PARQUET = os.path.join(HERE, "unlisted_firms.parquet")

# Profitable segments (industries of the Triple-Hit winners) -> source-specific
# search keywords. Edit/extend as the winners change.
SEGMENT_KEYWORDS = {
    "Specialty Chemicals":                       ["specialty chemical", "chemical"],
    "Medical Care Facilities":                   ["hospital", "healthcare"],
    "Auto Parts":                                ["auto parts", "automotive component"],
    "Packaged Foods":                            ["food processing", "packaged food"],
    "Beverages - Wineries & Distilleries":       ["distillery", "ethanol", "liquor", "spirits"],
    "Engineering & Construction":                ["construction", "engineering"],
    "Aerospace & Defense":                       ["aerospace", "defence"],
    "Business Equipment & Supplies":             ["stationery", "office supplies"],
    "Information Technology Services":           ["information technology", "it services"],
    "Drug Manufacturers - Specialty & Generic":  ["pharmaceutical"],
    "Software - Application":                    ["software"],
}

UA = "global-market-scanners/1.0 (umashankartd1991@gmail.com)"
WDQS = "https://query.wikidata.org/sparql"
INDIA_QID = "Q668"

# GLEIF matches on the company NAME (no industry codes), so per segment we use
# the distinctive words that appear in firm names for that industry.
GLEIF_KEYWORDS = {
    "Specialty Chemicals":                      ["SPECIALITY CHEMICAL", "SPECIALTY CHEMICAL"],
    "Medical Care Facilities":                  ["HOSPITAL", "HEALTHCARE"],
    "Auto Parts":                               ["AUTO PARTS", "AUTOMOTIVE", "AUTO COMPONENT"],
    "Packaged Foods":                           ["FOOD PRODUCTS", "AGRO FOODS", "FOODS PRIVATE"],
    "Beverages - Wineries & Distilleries":      ["DISTILLERY", "DISTILLERIES", "BREWERIES", "ETHANOL"],
    "Engineering & Construction":               ["INFRASTRUCTURE", "CONSTRUCTION"],
    "Aerospace & Defense":                      ["AEROSPACE", "DEFENCE"],
    "Business Equipment & Supplies":            ["STATIONERY", "OFFICE SUPPLIES"],
    "Information Technology Services":          ["INFOTECH", "IT SERVICES", "SOFTWARE SERVICES"],
    "Drug Manufacturers - Specialty & Generic": ["PHARMACEUTICAL", "PHARMA PRIVATE"],
    "Software - Application":                    ["SOFTWARE", "TECHNOLOGIES"],
}


# ---------------------------------------------------------------- Wikidata (free)
def wikidata_unlisted(segment, keywords, geo_qid=INDIA_QID, limit=60):
    """Indian orgs in a segment that have NO stock-exchange listing (P414)."""
    # match industry (P452) OR the item's own label on any keyword
    kw_filter = " || ".join(
        f'CONTAINS(LCASE(?indLabel), "{k.lower()}") || CONTAINS(LCASE(?cLabel), "{k.lower()}")'
        for k in keywords
    )
    query = f"""
    SELECT DISTINCT ?company ?cLabel ?website ?employees ?cityLabel WHERE {{
      ?company wdt:P17 wd:{geo_qid} .
      ?company rdfs:label ?cLabel . FILTER(LANG(?cLabel)="en")
      OPTIONAL {{ ?company wdt:P452 ?ind . ?ind rdfs:label ?indLabel . FILTER(LANG(?indLabel)="en") }}
      OPTIONAL {{ ?company wdt:P856 ?website }}
      OPTIONAL {{ ?company wdt:P1128 ?employees }}
      OPTIONAL {{ ?company wdt:P159 ?hq . ?hq wdt:P131* ?city . ?city rdfs:label ?cityLabel . FILTER(LANG(?cityLabel)="en") }}
      FILTER NOT EXISTS {{ ?company wdt:P414 ?exchange }}   # unlisted: no stock exchange
      FILTER({kw_filter})
    }} LIMIT {limit}
    """
    url = WDQS + "?" + urllib.parse.urlencode({"format": "json", "query": query})
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    rows = []
    try:
        data = None
        for attempt in range(4):  # WDQS throttles to ~1 req/min during outages
            try:
                with urllib.request.urlopen(req, timeout=90) as r:
                    data = json.loads(r.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as he:
                if he.code == 429 and attempt < 3:
                    print(f"    [wikidata] {segment}: 429, waiting 65s...", file=sys.stderr)
                    time.sleep(65)
                else:
                    raise
        if data is None:
            return rows
        seen = set()
        for b in data["results"]["bindings"]:
            name = b["cLabel"]["value"]
            if name.lower() in seen:
                continue
            seen.add(name.lower())
            rows.append({
                "company_name": name,
                "country": "India",
                "segment": segment,
                "source": "wikidata",
                "website": b.get("website", {}).get("value"),
                "employees": b.get("employees", {}).get("value"),
                "revenue": None,
                "city": b.get("cityLabel", {}).get("value"),
                "is_listed": False,
            })
    except Exception as e:
        print(f"    [wikidata] {segment}: {e}", file=sys.stderr)
    return rows


# ------------------------------------------------------ D&B Direct+ (needs keys)
def dnb_unlisted(segment, keywords, geo="IN", limit=50):
    key, secret = os.environ.get("DNB_KEY"), os.environ.get("DNB_SECRET")
    if not (key and secret):
        raise RuntimeError("Set DNB_KEY and DNB_SECRET env vars (D&B Direct+ subscription).")
    import base64
    # 1) OAuth token
    auth = base64.b64encode(f"{key}:{secret}".encode()).decode()
    tok_req = urllib.request.Request(
        "https://plus.dnb.com/v3/token",
        data=b'{"grant_type":"client_credentials"}',
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
        method="POST")
    token = json.loads(urllib.request.urlopen(tok_req, timeout=30).read())["access_token"]
    # 2) Company List search by industry keyword + country
    rows = []
    for kw in keywords[:1]:
        body = json.dumps({
            "countryISOAlpha2Code": geo,
            "searchTerm": kw,
            "isMarketable": True,
            "pageSize": limit,
        }).encode()
        req = urllib.request.Request(
            "https://plus.dnb.com/v1/search/companyList", data=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST")
        data = json.loads(urllib.request.urlopen(req, timeout=60).read())
        for c in data.get("searchCandidates", []):
            org = c.get("organization", {})
            rows.append({
                "company_name": org.get("primaryName"),
                "country": "India", "segment": segment, "source": "dnb",
                "website": (org.get("websiteAddress") or [{}])[0].get("url"),
                "employees": (org.get("numberOfEmployees") or [{}])[0].get("value"),
                "revenue": None,
                "city": (org.get("primaryAddress", {}) or {}).get("addressLocality", {}).get("name"),
                "is_listed": org.get("isStandalone", True) is False,
            })
    return rows


# ---------------------------------------------------- Lusha Prospecting (needs key)
def lusha_unlisted(segment, keywords, geo="India", limit=40):
    api_key = os.environ.get("LUSHA_API_KEY")
    if not api_key:
        raise RuntimeError("Set LUSHA_API_KEY env var (Lusha API subscription).")
    body = json.dumps({
        "pages": {"page": 0, "size": limit},
        "filters": {"companies": {"include": {
            "mainIndustriesIds": [], "names": keywords, "locations": [{"country": geo}],
        }}},
    }).encode()
    req = urllib.request.Request(
        "https://api.lusha.com/prospecting/company/search", data=body,
        headers={"api_key": api_key, "Content-Type": "application/json"}, method="POST")
    data = json.loads(urllib.request.urlopen(req, timeout=60).read())
    rows = []
    for c in data.get("data", []):
        rows.append({
            "company_name": c.get("name"), "country": "India", "segment": segment,
            "source": "lusha", "website": c.get("domain"),
            "employees": c.get("companySize"), "revenue": c.get("revenue"),
            "city": (c.get("location") or {}).get("city"), "is_listed": False,
        })
    return rows


# ----------------------------------------------------- Apollo REST (needs paid key)
def apollo_unlisted(segment, keywords, geo="India", limit=50):
    api_key = os.environ.get("APOLLO_API_KEY")
    if not api_key:
        raise RuntimeError("Set APOLLO_API_KEY (paid plan; free plan blocks org search).")
    body = json.dumps({
        "q_organization_keyword_tags": keywords,
        "organization_locations": [geo],
        "per_page": min(limit, 100), "page": 1,
    }).encode()
    req = urllib.request.Request(
        "https://api.apollo.io/v1/mixed_companies/search", data=body,
        headers={"X-Api-Key": api_key, "Content-Type": "application/json"}, method="POST")
    data = json.loads(urllib.request.urlopen(req, timeout=60).read())
    rows = []
    for o in data.get("organizations", []):
        rows.append({
            "company_name": o.get("name"), "country": "India", "segment": segment,
            "source": "apollo", "website": o.get("website_url"),
            "employees": o.get("estimated_num_employees"),
            "revenue": o.get("annual_revenue"),
            "city": o.get("city"), "is_listed": bool(o.get("publicly_traded_symbol")),
        })
    return rows


# ------------------------------------------------ OpenCorporates (needs token)
def opencorporates_unlisted(segment, keywords, geo="in", limit=100):
    """Registry-of-record company search. Needs OPENCORPORATES_API_TOKEN.
    Anonymous access is disabled (HTTP 401), so a token is mandatory. Most
    registered companies are private/unlisted; we keep them and dedupe against
    our listed names downstream."""
    token = os.environ.get("OPENCORPORATES_API_TOKEN")
    if not token:
        raise RuntimeError("Set OPENCORPORATES_API_TOKEN env var "
                           "(opencorporates.com account — anonymous API is 401).")
    rows, seen = [], set()
    for kw in keywords:
        params = urllib.parse.urlencode({
            "q": kw, "country_code": geo, "per_page": min(limit, 100),
            "order": "score", "api_token": token,
        })
        url = f"https://api.opencorporates.com/v0.4/companies/search?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            print(f"    [opencorporates] {segment}/{kw}: HTTP {e.code}", file=sys.stderr)
            continue
        for c in data.get("results", {}).get("companies", []):
            co = c.get("company", {})
            name = co.get("name")
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            addr = co.get("registered_address_in_full") or ""
            rows.append({
                "company_name": name, "country": "India", "segment": segment,
                "source": "opencorporates",
                "website": co.get("opencorporates_url"),
                "employees": None, "revenue": None,
                "city": addr.split(",")[-2].strip() if addr.count(",") >= 2 else addr or None,
                "is_listed": False,
                "company_number": co.get("company_number"),
                "status": co.get("current_status"),
                "incorporated": co.get("incorporation_date"),
            })
        time.sleep(0.6)
    return rows


# ----------------------------------------------------------- GLEIF (free, keyless)
def gleif_unlisted(segment, keywords, geo="IN", cap=80):
    """Open, no-key registry of legal entities (Global LEI Foundation).
    Matches India entities whose legal name contains a segment keyword. Most are
    private/unlisted; listed ones are removed by the downstream name dedupe."""
    gkws = GLEIF_KEYWORDS.get(segment, [k.upper() for k in keywords])
    rows, seen = [], set()
    for kw in gkws:
        params = urllib.parse.urlencode({
            "filter[entity.legalAddress.country]": geo,
            "filter[entity.legalName]": kw,
            "page[size]": 100, "page[number]": 1,
        })
        url = f"https://api.gleif.org/api/v1/lei-records?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                   "Accept": "application/vnd.api+json"})
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            print(f"    [gleif] {segment}/{kw}: HTTP {e.code}", file=sys.stderr)
            continue
        for rec in data.get("data", []):
            ent = rec["attributes"]["entity"]
            name = ent["legalName"]["name"]
            if name.lower() in seen:
                continue
            seen.add(name.lower())
            rows.append({
                "company_name": name, "country": "India", "segment": segment,
                "source": "gleif",
                "website": None, "employees": None, "revenue": None,
                "city": (ent.get("legalAddress") or {}).get("city"),
                "is_listed": False,
                "lei": rec.get("id"),
                "legal_form": (ent.get("legalForm") or {}).get("id"),
                "status": ent.get("status"),
            })
            if len(rows) >= cap:
                break
        time.sleep(0.4)
        if len(rows) >= cap:
            break
    return rows


PROVIDERS = {"wikidata": wikidata_unlisted, "dnb": dnb_unlisted,
             "lusha": lusha_unlisted, "apollo": apollo_unlisted,
             "opencorporates": opencorporates_unlisted, "gleif": gleif_unlisted}


def listed_names():
    if os.path.exists(MAIN_PARQUET):
        return set(pd.read_parquet(MAIN_PARQUET)["company_name"].str.lower())
    return set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=PROVIDERS, default="wikidata")
    ap.add_argument("--geo", default="India")
    ap.add_argument("--segments", nargs="*", default=list(SEGMENT_KEYWORDS),
                    help="subset of segment names (default: all profitable segments)")
    ap.add_argument("--merge", action="store_true",
                    help="also append the unlisted firms into companies_industry.parquet")
    ap.add_argument("--pace", type=float, default=None,
                    help="seconds between segment queries (default 62 for wikidata, 1 otherwise)")
    args = ap.parse_args()
    pace = args.pace if args.pace is not None else (62.0 if args.source == "wikidata" else 1.0)

    fn = PROVIDERS[args.source]
    known = listed_names()
    all_rows = []
    for seg in args.segments:
        kws = SEGMENT_KEYWORDS.get(seg)
        if not kws:
            print(f"  [skip] unknown segment: {seg}", file=sys.stderr); continue
        try:
            if args.source == "wikidata":
                rows = fn(seg, kws)
            else:
                geo = {"dnb": "IN", "opencorporates": "in", "gleif": "IN"}.get(args.source, args.geo)
                rows = fn(seg, kws, geo)
        except RuntimeError as e:  # missing credential — fail fast & clean
            print(f"\n{args.source}: {e}", file=sys.stderr)
            sys.exit(2)
        # drop any that are actually our listed names
        rows = [r for r in rows if (r.get("company_name") or "").lower() not in known]
        print(f"  {seg:42} {args.source}: {len(rows)} unlisted firms", file=sys.stderr)
        all_rows.extend(rows)
        time.sleep(pace)  # respect endpoint throttle (WDQS ~1 req/min during outage)

    if not all_rows:
        print("\nNo unlisted firms returned (all sources blocked/empty). "
              "Nothing written.", file=sys.stderr)
        return
    df = pd.DataFrame(all_rows).drop_duplicates(subset=["company_name", "segment"])
    df["fetched_at"] = datetime.now(timezone.utc).isoformat()
    df.to_parquet(OUT_PARQUET, index=False, compression="snappy")
    print(f"\nDONE: {len(df)} unlisted firms across {df['segment'].nunique()} segments "
          f"-> {OUT_PARQUET} ({os.path.getsize(OUT_PARQUET)//1024} KB)", file=sys.stderr)

    if args.merge and len(df):
        main_df = pd.read_parquet(MAIN_PARQUET)
        add = df.rename(columns={})[["company_name", "country", "segment"]].copy()
        add["ticker"] = None; add["code"] = None
        add["exchange"] = "UNLISTED"; add["sector"] = None
        add["industry"] = add["segment"]; add["peer_count"] = 0
        add["global_peers"] = [[] for _ in range(len(add))]
        merged = pd.concat([main_df, add[main_df.columns.intersection(add.columns)]],
                           ignore_index=True)
        merged.to_parquet(MAIN_PARQUET, index=False, compression="snappy")
        print(f"  merged into {MAIN_PARQUET}: now {len(merged)} rows", file=sys.stderr)


if __name__ == "__main__":
    main()
