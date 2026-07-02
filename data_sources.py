#!/usr/bin/env python3
"""
data_sources.py
---------------
A registry of the **public data sources** for factor research, per market —
capturing what the IIMA quality-factor paper (Jacob-Pradeep-Varma, W.P. 2022-11-01)
names for India, and generalising it to the platform's 19 markets.

Why this exists: that paper benchmarks its quality factor against a 4-factor model
whose returns come from a *public* library — the IIMA Indian Fama-French-Momentum
(IFFM) data set (Agarwalla, Jacob & Varma 2014) — while its raw returns/fundamentals
come from the *commercial* CMIE Prowess database. Our platform spans far more markets,
so this maps every market to the public factor library you'd use as the benchmark
(the analogue of IFFM): AQR's Quality-Minus-Junk data set and Kenneth French's
regional library. It also records the raw price/fundamental source the platform
actually uses per market.

Everything here is a static, offline reference table with pure lookup functions.

Usage:
  python data_sources.py                 # full per-market table
  python data_sources.py --market US     # detail for one market
  python data_sources.py --paper         # exactly what the IIMA paper names
  python data_sources.py --public        # public factor libraries only
"""

from __future__ import annotations

import argparse

# ── the public factor libraries (free benchmark factor-return series) ─────────
LIBRARIES = {
    "iffm": {
        "name": "IIMA Indian Fama-French-Momentum (Agarwalla, Jacob & Varma 2014)",
        "provides": "market/size/value/momentum factor returns + risk-free rate",
        "license": "public/free",
        "url": "https://faculty.iima.ac.in/~iffm/Indian-Fama-French-Momentum/",
        "note": "the exact library the IIMA quality paper uses as its 4-factor benchmark",
    },
    "aqr": {
        "name": "AQR Data Sets — Quality Minus Junk (QMJ) & Betting Against Beta",
        "provides": "QMJ + BAB + value/momentum factor returns, ~24 developed countries + global",
        "license": "public/free (registration)",
        "url": "https://www.aqr.com/Insights/Datasets",
        "note": "AQR = Asness-Frazzini-Pedersen; the exact QMJ factor quality_factor.py implements",
    },
    "ken_french": {
        "name": "Kenneth R. French Data Library (Dartmouth)",
        "provides": "Mkt-RF/SMB/HML/RMW/CMA/Mom for US + Developed/Emerging/regional portfolios",
        "license": "public/free",
        "url": "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html",
        "note": "regional 3/4/5-factor series covering most of our markets",
    },
}

# ── the raw returns/fundamentals sources (what actually feeds the pipeline) ────
RAW_SOURCES = {
    "yfinance":  {"name": "Yahoo Finance (yfinance)", "license": "public/free",
                  "provides": "OHLC + snapshot fundamentals", "url": "https://finance.yahoo.com"},
    "edgar":     {"name": "SEC EDGAR XBRL", "license": "public/free",
                  "provides": "point-in-time (filed-date) US fundamentals", "url": "https://www.sec.gov/edgar"},
    "nsepython": {"name": "NSE/BSE India (nsepython, bseindia)", "license": "public/free",
                  "provides": "India OHLC + corporate data", "url": "https://www.nseindia.com"},
    "cmie_prowess": {"name": "CMIE Prowess", "license": "COMMERCIAL (subscription)",
                     "provides": "India returns + firm-level financials",
                     "url": "https://prowess.cmie.com",
                     "note": "the IIMA paper's raw data source — NOT public"},
}

# Kenneth French regional bucket each market falls in.
KEN_FRENCH_REGION = {
    "US": "North America", "CA": "North America",
    "UK": "Europe", "DE": "Europe", "EU": "Europe", "FI": "Europe",
    "DK": "Europe", "SE": "Europe", "CH": "Europe",
    "JP": "Japan",
    "AU": "Asia Pacific ex Japan", "HK": "Asia Pacific ex Japan", "SG": "Asia Pacific ex Japan",
    "BR": "Emerging", "CN": "Emerging", "TW": "Emerging", "KR": "Emerging",
    "SA": "Emerging", "ZA": "Emerging", "IN": "Emerging",
}

# AQR publishes country-level QMJ for ~24 developed markets (+ a global series).
_AQR_DEVELOPED = {"US", "CA", "UK", "DE", "FI", "DK", "SE", "CH", "JP", "AU", "HK", "SG"}
# India is included in several AQR emerging/global sets.
_AQR_COUNTRY = _AQR_DEVELOPED | {"IN"}

# currency per market (kept local so this reference module has no heavy deps).
CURRENCY = {
    "AU": "AUD", "BR": "BRL", "CA": "CAD", "CH": "CHF", "CN": "CNY", "DE": "EUR",
    "DK": "DKK", "EU": "EUR", "FI": "EUR", "HK": "HKD", "JP": "JPY", "KR": "KRW",
    "SA": "SAR", "SE": "SEK", "SG": "SGD", "TW": "TWD", "UK": "GBP", "US": "USD",
    "ZA": "ZAR", "IN": "INR",
}

# the 19 markets in the platform's cache, plus India (the paper's market; lives in the
# separate India repo, included here for completeness / benchmark reference).
PLATFORM_MARKETS = ["US", "CA", "UK", "DE", "EU", "FI", "DK", "SE", "CH", "JP",
                    "AU", "HK", "SG", "BR", "CN", "TW", "KR", "SA", "ZA"]


# ── pure lookup core ──────────────────────────────────────────────────────────
def public_factor_sources(market: str) -> list:
    """Public factor-return libraries usable as the alpha benchmark for a market,
    best-first: India→IFFM; AQR country QMJ where published; Ken French regional."""
    m = market.upper()
    libs = []
    if m == "IN":
        libs.append("iffm")
    if m in _AQR_COUNTRY:
        libs.append("aqr")
    else:
        libs.append("aqr")               # AQR global series still applies to emerging
    if m in KEN_FRENCH_REGION:
        libs.append("ken_french")
    # de-dup, keep order
    return list(dict.fromkeys(libs))


def raw_sources(market: str) -> list:
    """The raw price/fundamental sources the platform uses for a market."""
    m = market.upper()
    out = ["yfinance"]
    if m == "US":
        out.append("edgar")
    if m == "IN":
        out = ["nsepython", "yfinance"]
    return out


def for_market(market: str) -> dict:
    """Full registry entry for a market."""
    m = market.upper()
    return {
        "market": m,
        "currency": CURRENCY.get(m),
        "ken_french_region": KEN_FRENCH_REGION.get(m),
        "aqr_country_qmj": m in _AQR_COUNTRY,
        "public_factor_sources": public_factor_sources(m),
        "raw_sources": raw_sources(m),
    }


def paper_sources() -> dict:
    """Exactly the two data sources the IIMA paper names (§2.2 + footnote 2)."""
    return {
        "returns_and_fundamentals": RAW_SOURCES["cmie_prowess"],   # commercial
        "factor_benchmark": LIBRARIES["iffm"],                     # public
    }


def coverage() -> list:
    """Registry rows for all platform markets (+ India)."""
    return [for_market(m) for m in PLATFORM_MARKETS + ["IN"]]


def _fmt_lib(key: str) -> str:
    lib = LIBRARIES[key]
    return f"{key} ({lib['license']})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default=None)
    ap.add_argument("--paper", action="store_true", help="what the IIMA paper names")
    ap.add_argument("--public", action="store_true", help="the public factor libraries")
    args = ap.parse_args()

    if args.paper:
        p = paper_sources()
        print("=== data sources named in the IIMA quality-factor paper (India-only) ===")
        print(f"  raw returns + fundamentals: {p['returns_and_fundamentals']['name']}")
        print(f"      -> {p['returns_and_fundamentals']['license']}  "
              f"({p['returns_and_fundamentals']['url']})")
        print(f"  4-factor benchmark:         {p['factor_benchmark']['name']}")
        print(f"      -> {p['factor_benchmark']['license']}  ({p['factor_benchmark']['url']})")
        print("\n  Only the IFFM factor library is public; CMIE Prowess is commercial.")
        return

    if args.public:
        print("=== public factor-return libraries (free alpha benchmarks) ===")
        for k, lib in LIBRARIES.items():
            print(f"  [{k}] {lib['name']}")
            print(f"       provides: {lib['provides']}")
            print(f"       {lib['license']}  ·  {lib['url']}")
        return

    if args.market:
        d = for_market(args.market)
        print(f"=== {d['market']} ({d['currency']}) ===")
        print(f"  Ken French region : {d['ken_french_region']}")
        print(f"  AQR country QMJ    : {'yes' if d['aqr_country_qmj'] else 'no (use AQR global)'}")
        print(f"  public factor libs : {', '.join(_fmt_lib(k) for k in d['public_factor_sources'])}")
        for k in d["public_factor_sources"]:
            print(f"       - {LIBRARIES[k]['url']}")
        print(f"  raw price/fund     : {', '.join(RAW_SOURCES[s]['name'] for s in d['raw_sources'])}")
        return

    # default: the full table
    print(f"{'mkt':4}{'ccy':5}{'KenFrench region':24}{'AQR-QMJ':9}  public factor libraries")
    print("-" * 78)
    for d in coverage():
        aqr = "country" if d["aqr_country_qmj"] else "global"
        print(f"{d['market']:4}{d['currency'] or '':5}{d['ken_french_region'] or '':24}"
              f"{aqr:9}  {', '.join(d['public_factor_sources'])}")
    print("\nRaw pipeline sources: yfinance (all), SEC EDGAR (US, point-in-time), "
          "nsepython/bseindia (India). IIMA paper: CMIE Prowess (commercial) + IFFM (public).")


if __name__ == "__main__":
    main()
