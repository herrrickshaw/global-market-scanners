#!/usr/bin/env python3
"""
pit_fundamentals.py
-------------------
Point-in-time US fundamentals from SEC EDGAR — the values as they were KNOWN on a
given historical date (only filings with filed <= date), so a backtest never leaks
restated numbers from the future.

Builds on sec_fundamentals.py's concept mappings, adds:
  as_of(ticker, date)        -> strategy-ready fundamentals dict, PIT
  piotroski_asof(ticker, dt) -> (F_score 0-9, detail dict)
  coffeecan_asof(ticker, dt) -> (bool pass, detail dict)   [US-adapted]

companyfacts JSON is cached to ./edgar_cache/CIK*.json so a 5-year monthly
backtest over hundreds of tickers hits disk, not the SEC.
"""

from __future__ import annotations

import glob
import json
import os
import sqlite3
import time
import warnings
from datetime import date as _date
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

import requests


def _d(s: str) -> _date:
    return _date.fromisoformat(s)


warnings.filterwarnings("ignore")

_UA = {"User-Agent": "market-research umashankartd1991@gmail.com"}
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "edgar_cache")     # legacy JSON cache (migrated in)
EDGAR_DB = os.path.join(HERE, "edgar_facts.db")   # compact single-file cache

# One normalised SQLite table replaces thousands of JSON files: no per-file
# overhead, no repeated JSON keys -> far smaller and queryable.
_conn = sqlite3.connect(EDGAR_DB)
_conn.execute("PRAGMA journal_mode=DELETE;")
_conn.execute("PRAGMA synchronous=NORMAL;")
_conn.executescript("""
  CREATE TABLE IF NOT EXISTS facts(
    cik TEXT, concept TEXT, unit TEXT, end TEXT, start TEXT, filed TEXT, val REAL);
  CREATE INDEX IF NOT EXISTS ix_facts_cik ON facts(cik);
  CREATE TABLE IF NOT EXISTS fetched(cik TEXT PRIMARY KEY);
""")
_conn.commit()


def _store(cik: str, facts: dict):
    rows = []
    for concept, node in facts.get("us-gaap", {}).items():
        for unit, vals in node.get("units", {}).items():
            for r in vals:
                rows.append((cik, concept, unit, r.get("end"), r.get("start"),
                             r.get("filed"), r.get("val")))
    _conn.executemany("INSERT INTO facts VALUES (?,?,?,?,?,?,?)", rows)
    _conn.execute("INSERT OR REPLACE INTO fetched VALUES (?)", (cik,))
    _conn.commit()


def _read(cik: str) -> Optional[dict]:
    if not _conn.execute("SELECT 1 FROM fetched WHERE cik=?", (cik,)).fetchone():
        return None
    gaap: Dict[str, dict] = {}
    for concept, unit, end, start, filed, val in _conn.execute(
            "SELECT concept,unit,end,start,filed,val FROM facts WHERE cik=?", (cik,)):
        gaap.setdefault(concept, {"units": {}})["units"].setdefault(unit, []).append(
            {"form": "10-K", "fp": "FY", "end": end, "start": start,
             "filed": filed, "val": val})
    return {"us-gaap": gaap}

# Only these us-gaap concepts are ever read (see as_of). Pruning companyfacts to
# these — 10-K/FY entries only — shrinks each cached filing from ~4MB to ~30KB,
# so the full ~6k US universe caches in ~190MB instead of ~27GB.
NEEDED_CONCEPTS = {
    "NetIncomeLoss", "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues", "SalesRevenueNet", "Assets", "Liabilities", "StockholdersEquity",
    "AssetsCurrent", "LiabilitiesCurrent", "GrossProfit",
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    "CommonStockSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingBasic",
    "PaymentsToAcquirePropertyPlantAndEquipment",
}


def _prune(facts: dict) -> dict:
    """Keep only NEEDED_CONCEPTS, and within them only 10-K/FY entries."""
    gaap = facts.get("us-gaap", {})
    out = {}
    for c in NEEDED_CONCEPTS:
        node = gaap.get(c)
        if not node:
            continue
        units = {}
        for unit, vals in node.get("units", {}).items():
            keep = [r for r in vals if r.get("form") == "10-K" and r.get("fp") == "FY"]
            if keep:
                units[unit] = keep
        if units:
            out[c] = {"units": units}
    return {"us-gaap": out}


@lru_cache(maxsize=1)
def _ticker_cik() -> Dict[str, str]:
    r = requests.get("https://www.sec.gov/files/company_tickers.json",
                     headers=_UA, timeout=30)
    r.raise_for_status()
    return {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in r.json().values()}


_MIGRATED = False


def _migrate_json():
    """One-time import of any legacy edgar_cache/*.json into the SQLite cache."""
    global _MIGRATED
    _MIGRATED = True
    if not os.path.isdir(CACHE_DIR):
        return
    for p in glob.glob(os.path.join(CACHE_DIR, "CIK*.json")):
        cik = os.path.basename(p)[3:-5]
        if _conn.execute("SELECT 1 FROM fetched WHERE cik=?", (cik,)).fetchone():
            continue
        try:
            _store(cik, _prune(json.load(open(p))))
        except Exception:
            pass


@lru_cache(maxsize=8192)
def _load_facts(ticker: str) -> Optional[dict]:
    """Pruned companyfacts for a ticker, from the compact SQLite cache."""
    if not _MIGRATED:
        _migrate_json()
    cik = _ticker_cik().get(ticker.upper())
    if not cik:
        return None
    cached = _read(cik)
    if cached is not None:
        return cached
    try:
        r = requests.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
                         headers=_UA, timeout=30)
        time.sleep(0.12)  # SEC fair-access
        if r.status_code != 200:
            _conn.execute("INSERT OR REPLACE INTO fetched VALUES (?)", (cik,))  # negative-cache
            _conn.commit()
            return {"us-gaap": {}}
        facts = _prune(r.json().get("facts", {}))
        _store(cik, facts)
        return facts
    except Exception:
        return None


def _annual_asof(facts: dict, concept: str, asof: str, n: int = 5) -> List[float]:
    """Annual (10-K/FY) values for a concept KNOWN as of `asof` (filed <= asof).
    For each fiscal period-end keep the latest version filed on/before asof
    (i.e. most-recent restatement that was public by then). Newest period first."""
    node = facts.get("us-gaap", {}).get(concept)
    if not node:
        return []
    best: Dict[str, Tuple[str, float]] = {}  # end -> (filed, val)
    for unit_vals in node.get("units", {}).values():
        for r in unit_vals:
            if r.get("form") != "10-K" or r.get("fp") != "FY":
                continue
            filed, val, end, start = r.get("filed"), r.get("val"), r.get("end"), r.get("start")
            if val is None or not filed or not end or filed > asof:
                continue
            # Duration concepts (income/revenue/cashflow) carry 'start': keep only
            # ~annual periods (≈365d), dropping quarterly frames mis-tagged fp=FY.
            # Instantaneous balance-sheet concepts have no 'start' — accept as-is
            # (10-K snapshots are fiscal-year-end only).
            if start:
                days = (_d(end) - _d(start)).days
                if not (350 <= days <= 385):
                    continue
            if end not in best or filed > best[end][0]:
                best[end] = (filed, val)
    return [best[k][1] for k in sorted(best, reverse=True)][:n]


def _first(facts, asof, *concepts, n=5):
    for c in concepts:
        v = _annual_asof(facts, c, asof, n)
        if v:
            return v
    return []


def _r(a, b):
    return (a / b) if (a is not None and b not in (None, 0)) else None


def _g(lst, i=0):
    return lst[i] if lst and len(lst) > i else None


def as_of(ticker: str, date: str) -> Dict:
    """PIT fundamentals dict for a US ticker as known on `date` (YYYY-MM-DD)."""
    f = _load_facts(ticker)
    if not f:
        return {}
    ni     = _first(f, date, "NetIncomeLoss")
    rev    = _first(f, date, "RevenueFromContractWithCustomerExcludingAssessedTax",
                    "Revenues", "SalesRevenueNet")
    assets = _first(f, date, "Assets")
    liab   = _first(f, date, "Liabilities")
    equity = _first(f, date, "StockholdersEquity")
    cur_a  = _first(f, date, "AssetsCurrent")
    cur_l  = _first(f, date, "LiabilitiesCurrent")
    cfo    = _first(f, date, "NetCashProvidedByUsedInOperatingActivities",
                    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations")
    gp     = _first(f, date, "GrossProfit")
    shares = _first(f, date, "CommonStockSharesOutstanding",
                    "WeightedAverageNumberOfSharesOutstandingBasic")
    capex  = _first(f, date, "PaymentsToAcquirePropertyPlantAndEquipment")

    roe_hist = []
    for i in range(min(len(ni), len(equity))):
        if equity[i]:
            roe_hist.append(ni[i] / equity[i] * 100)

    return {
        "ni": ni, "rev": rev, "assets": assets, "equity": equity,
        "cfo": _g(cfo), "capex": _g(capex),
        "roa": _r(_g(ni), _g(assets)), "roa_prev": _r(_g(ni, 1), _g(assets, 1)),
        "cur": _r(_g(cur_a), _g(cur_l)), "cur_prev": _r(_g(cur_a, 1), _g(cur_l, 1)),
        "lev": _r(_g(liab), _g(assets)), "lev_prev": _r(_g(liab, 1), _g(assets, 1)),
        "gm": _r(_g(gp), _g(rev)), "gm_prev": _r(_g(gp, 1), _g(rev, 1)),
        "at": _r(_g(rev), _g(assets)), "at_prev": _r(_g(rev, 1), _g(assets, 1)),
        "shares": _g(shares), "shares_prev": _g(shares, 1),
        "de": _r(_g(liab), _g(equity)), "roe_hist": roe_hist,
    }


def piotroski_asof(ticker: str, date: str) -> Tuple[Optional[int], dict]:
    d = as_of(ticker, date)
    if not d or _g(d["ni"]) is None or _g(d["assets"]) is None:
        return None, {}
    s, det = 0, {}
    tests = {
        "roa_pos":   (d["roa"] is not None and d["roa"] > 0),
        "cfo_pos":   (d["cfo"] is not None and d["cfo"] > 0),
        "d_roa":     (d["roa"] is not None and d["roa_prev"] is not None and d["roa"] > d["roa_prev"]),
        "accrual":   (d["cfo"] is not None and _g(d["ni"]) is not None and d["cfo"] > _g(d["ni"])),
        "d_lev":     (d["lev"] is not None and d["lev_prev"] is not None and d["lev"] < d["lev_prev"]),
        "d_cur":     (d["cur"] is not None and d["cur_prev"] is not None and d["cur"] > d["cur_prev"]),
        "no_dilute": (d["shares"] is not None and d["shares_prev"] is not None and d["shares"] <= d["shares_prev"] * 1.01),
        "d_margin":  (d["gm"] is not None and d["gm_prev"] is not None and d["gm"] > d["gm_prev"]),
        "d_turn":    (d["at"] is not None and d["at_prev"] is not None and d["at"] > d["at_prev"]),
    }
    for k, v in tests.items():
        det[k] = bool(v); s += int(bool(v))
    return s, det


def coffeecan_asof(ticker: str, date: str, mktcap: Optional[float] = None) -> Tuple[bool, dict]:
    """US-adapted Coffee-Can: avg ROE>15%, positive earnings every year, positive
    FCF, revenue growth, leverage in check, and (if given) market cap > $1B."""
    d = as_of(ticker, date)
    if not d or len(d["ni"]) < 3:
        return False, {}
    roe = d["roe_hist"]
    fcf = (d["cfo"] - d["capex"]) if (d["cfo"] is not None and d["capex"] is not None) else None
    det = {
        "avg_roe>15":     bool(roe) and (sum(roe) / len(roe) > 15),
        "earnings_all+":  all(x is not None and x > 0 for x in d["ni"][:5]),
        "fcf_pos":        fcf is not None and fcf > 0,
        "rev_growth":     _g(d["rev"]) is not None and _g(d["rev"], -1) is not None and _g(d["rev"]) > _g(d["rev"], -1),
        "leverage_ok":    d["lev"] is not None and d["lev"] < 0.6,
        "cap>1B":         (mktcap is None) or (mktcap > 1e9),
    }
    return all(det.values()), det


if __name__ == "__main__":
    import sys
    tkr = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    for dt in ["2019-06-01", "2021-06-01", "2023-06-01", "2025-06-01"]:
        f, fd = piotroski_asof(tkr, dt)
        cc, cd = coffeecan_asof(tkr, dt, mktcap=2e12)
        print(f"{tkr} as of {dt}:  F-score={f}  coffee-can={cc}")
        print(f"    F detail: {fd}")
