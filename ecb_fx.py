#!/usr/bin/env python3
"""
ecb_fx.py
---------
Official keyless FX reference rates from the European Central Bank, to complement the
yfinance-sourced `fx.py` (whose offline snapshot is hand-edited). ECB publishes daily
EUR-base reference rates (~30 currencies; covers USD, JPY, GBP, CHF, SEK, DKK, CNY,
KRW, INR, BRL, AUD, CAD, HKD, SGD, ZAR ...) as XML, 90-day history file included.

  https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml

Honest limits: rates are EUR-per-foreign-unit quoted as foreign per 1 EUR, fixed
~14:15 CET, reference-only (not tradable), and exist only on TARGET business days.
This writes a standalone long-format CSV; it does not touch fx_rates.json.

Usage:
  python ecb_fx.py                      # -> data/ecb_fx_hist_90d.csv
"""

from __future__ import annotations

import argparse
import os
import urllib.request
import xml.etree.ElementTree as ET

import pandas as pd

URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"
UA = "global-market-scanners research (open data collector)"


def parse(xml_text: str) -> pd.DataFrame:
    """Pure parser: ECB eurofxref XML -> long frame [date, currency, per_eur]."""
    rows = []
    for el in ET.fromstring(xml_text).iter():
        if el.tag.endswith("Cube") and "time" in el.attrib:
            for c in el:
                rows.append((el.attrib["time"], c.attrib["currency"], float(c.attrib["rate"])))
    return pd.DataFrame(rows, columns=["date", "currency", "per_eur"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out-dir", default=os.path.join(os.path.dirname(__file__), "data"))
    a = ap.parse_args()
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        df = parse(r.read().decode("utf-8"))
    os.makedirs(a.out_dir, exist_ok=True)
    path = os.path.join(a.out_dir, "ecb_fx_hist_90d.csv")
    df.to_csv(path, index=False)
    print(f"{len(df)} rows, {df.date.nunique()} dates, {df.currency.nunique()} currencies "
          f"({df.date.min()}..{df.date.max()}) -> {path}")


if __name__ == "__main__":
    main()
