#!/usr/bin/env python3
"""
fx.py
-----
Cross-market rankings silently mixed currencies — a KRW price level and a USD
price level are not comparable, and a cross-market *return* comparison is only
fair in one common currency. This module fixes that: it maps each of the 19
markets to its trading currency and converts local values / returns into a base
currency (USD by default).

Two return conventions:
  * a *local* return in base terms picks up the FX move too:
        (1+r_base) = (1+r_local) * (1+r_fx)
  * a price *level* converts by the spot rate: value_base = value_local * rate.

Rates come from yfinance FX pairs via the governed apiclient, cached to
fx_rates.json. A static offline snapshot (editable, dated) keeps the module and
CI fully runnable without network.

Usage:
  python fx.py --rates                     # show current base-per-local rates
  python fx.py --refresh                   # fetch live rates -> fx_rates.json
  python fx.py --convert 1000 --from KR    # convert a KRW amount to USD
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "fx_rates.json")

# market (cleaned_long_*.parquet code) -> ISO currency
MARKET_CCY = {
    "AU": "AUD", "BR": "BRL", "CA": "CAD", "CH": "CHF", "CN": "CNY", "DE": "EUR",
    "DK": "DKK", "EU": "EUR", "FI": "EUR", "HK": "HKD", "JP": "JPY", "KR": "KRW",
    "SA": "SAR", "SE": "SEK", "SG": "SGD", "TW": "TWD", "UK": "GBP", "US": "USD",
    "ZA": "ZAR",
}

# Static USD-per-1-unit-of-currency snapshot (order-of-magnitude, dated 2026-07).
# Edit or `--refresh` to update. Used offline / when a live rate is unavailable.
SNAPSHOT_USD_PER = {
    "USD": 1.0, "EUR": 1.08, "GBP": 1.27, "JPY": 0.0064, "KRW": 0.00073,
    "AUD": 0.66, "BRL": 0.18, "CAD": 0.73, "CHF": 1.11, "CNY": 0.138,
    "DKK": 0.145, "HKD": 0.128, "SAR": 0.267, "SEK": 0.094, "SGD": 0.74,
    "TWD": 0.031, "ZAR": 0.054,
}


# ── pure conversion core ──────────────────────────────────────────────────────
def market_currency(market: str) -> str:
    return MARKET_CCY.get(market.upper(), "USD")


def convert_level(amount: float, rate_base_per_local: float) -> float:
    """Convert a price/market-cap level: base = local * rate."""
    return float(amount) * float(rate_base_per_local)


def combine_return(r_local: float, r_fx: float) -> float:
    """Local return expressed in base currency, compounding the FX move in."""
    return (1.0 + r_local) * (1.0 + r_fx) - 1.0


def normalize_cross_market(df: pd.DataFrame, value_col: str, market_col: str,
                           rates: dict, base: str = "USD") -> pd.DataFrame:
    """Add a `<value_col>_base` column converting a level to the base currency
    using {currency: base_per_unit} rates keyed via each row's market."""
    out = df.copy()
    def _rate(mkt):
        ccy = market_currency(str(mkt))
        return rates.get(ccy, np.nan)
    out[f"{value_col}_{base.lower()}"] = out[value_col].astype(float) * out[market_col].map(_rate)
    return out


# ── rate sourcing ─────────────────────────────────────────────────────────────
def load_rates(base: str = "USD") -> dict:
    """base-per-1-unit-of-currency for every currency (cache -> snapshot fallback)."""
    if os.path.exists(CACHE):
        try:
            data = json.load(open(CACHE))
            if data.get("base") == base and data.get("rates"):
                return data["rates"]
        except Exception:
            pass
    if base == "USD":
        return dict(SNAPSHOT_USD_PER)
    # rebase the USD snapshot to another base
    per_usd = SNAPSHOT_USD_PER
    b = per_usd.get(base)
    return {c: round(v / b, 6) for c, v in per_usd.items()} if b else dict(per_usd)


def refresh_rates(base: str = "USD") -> dict:
    """Fetch live spot rates via the governed apiclient; write fx_rates.json."""
    import apiclient
    rates = {base: 1.0}
    ccys = sorted(set(MARKET_CCY.values()) - {base})
    pairs = {c: f"{c}{base}=X" for c in ccys}          # e.g. EURUSD=X = USD per EUR
    data = apiclient.yf_download(list(pairs.values()), period="5d")
    for c, sym in pairs.items():
        df = data.get(sym)
        try:
            rates[c] = round(float(df["Close"].dropna().iloc[-1]), 6)
        except Exception:
            rates[c] = SNAPSHOT_USD_PER.get(c)          # fallback to snapshot
    json.dump({"base": base, "rates": rates}, open(CACHE, "w"), indent=2)
    return rates


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="USD")
    ap.add_argument("--rates", action="store_true", help="show current rates")
    ap.add_argument("--refresh", action="store_true", help="fetch live rates")
    ap.add_argument("--convert", type=float, default=None)
    ap.add_argument("--from", dest="frm", default=None, help="source MARKET code, e.g. KR")
    args = ap.parse_args()

    if args.refresh:
        rates = refresh_rates(args.base)
        print(f"refreshed {len(rates)} rates -> {CACHE}", file=sys.stderr)
    else:
        rates = load_rates(args.base)

    if args.convert is not None and args.frm:
        ccy = market_currency(args.frm)
        base_amt = convert_level(args.convert, rates.get(ccy, np.nan))
        print(f"{args.convert:,.2f} {ccy}  =  {base_amt:,.2f} {args.base}")
    else:
        print(f"=== {args.base}-per-unit rates ({len(rates)} currencies) ===")
        for c in sorted(rates):
            mkts = [m for m, x in MARKET_CCY.items() if x == c]
            print(f"  {c:4} {rates[c]:>12.6f}   markets: {','.join(mkts)}")


if __name__ == "__main__":
    main()
