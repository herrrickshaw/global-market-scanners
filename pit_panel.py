#!/usr/bin/env python3
"""
pit_panel.py  —  Remediations L1 (filed-date fundamentals, ALL markets) and
L5 (minimum-history gate).

`pit_fundamentals.py` already gives point-in-time fundamentals for the US via SEC
EDGAR. L1 is to extend that same discipline to markets WITHOUT an EDGAR-style API,
where fundamentals arrive as filings we must timestamp ourselves. This module is
the market-agnostic core:

L1: `as_of(panel, T)` takes a filed-date panel (each row carries the date the
    figure was actually published) and returns, per symbol, the latest figure
    filed ON OR BEFORE T — dropping anything filed later. That single rule is the
    anti-look-ahead guarantee that makes non-US quality tests point-in-time.
    Per-market primary sources are registered in data_sources.filing_source
    (India MCA/NSE, EU ESEF, Japan EDINET, Brazil CVM, US EDGAR).

L5: `min_history_ok` / `gate_markets` refuse to report a signal for a market with
    too little history — a blank beats a bad guess.

Pure functions, unit-testable without any network. Not investment advice.
"""
from __future__ import annotations

from datetime import date, datetime

TRADING_DAYS_YEAR = 252


def _d(x) -> date:
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    return datetime.strptime(str(x)[:10], "%Y-%m-%d").date()


# ── L1: point-in-time filed-date panel (any market) ───────────────────────────
def as_of(panel, asof, symbol_col="symbol", filed_col="filed_date"):
    """Latest row filed on/before `asof`, per symbol.

    panel: list of dicts (or a pandas DataFrame) each with a symbol, a filed_date,
    and any metric columns. Returns {symbol: row}. Rows filed AFTER asof are
    dropped — the point-in-time guarantee. Among rows filed on/before asof, the
    one with the greatest filed_date wins (the most recent restatement then public).
    """
    t = _d(asof)
    rows = panel.to_dict("records") if hasattr(panel, "to_dict") else list(panel)
    best = {}
    for r in rows:
        fd = _d(r[filed_col])
        if fd > t:
            continue
        sym = r[symbol_col]
        if sym not in best or fd > _d(best[sym][filed_col]):
            best[sym] = r
    return best


def is_point_in_time(panel, filed_col="filed_date") -> bool:
    """True iff every row carries a real filing date (i.e. not a today-snapshot)."""
    rows = panel.to_dict("records") if hasattr(panel, "to_dict") else list(panel)
    return bool(rows) and all(r.get(filed_col) not in (None, "", "NaT") for r in rows)


def leaks_lookahead(panel, asof, filed_col="filed_date") -> bool:
    """True iff the panel contains any figure that would NOT have been known at asof."""
    t = _d(asof)
    rows = panel.to_dict("records") if hasattr(panel, "to_dict") else list(panel)
    return any(_d(r[filed_col]) > t for r in rows)


# ── L5: minimum-history gate ──────────────────────────────────────────────────
def min_history_ok(n_days: int, min_years: float = 3.0,
                   trading_days_year: int = TRADING_DAYS_YEAR) -> bool:
    """True iff `n_days` of history meets the minimum-years bar for reporting."""
    return n_days >= min_years * trading_days_year


def gate_markets(history_days: dict, min_years: float = 3.0) -> dict:
    """Split markets into report/withhold by history length (blank beats a guess)."""
    report, withhold = [], []
    for mkt, n in sorted(history_days.items()):
        (report if min_history_ok(n, min_years) else withhold).append(mkt)
    return {"report": report, "withhold": withhold, "min_years": min_years,
            "min_days": int(min_years * TRADING_DAYS_YEAR)}


def main():
    print("pit_panel.py — remediations L1 (filed-date, any market) & L5 (history gate)\n")
    panel = [
        {"symbol": "AAA", "filed_date": "2023-02-10", "roe": 0.10},
        {"symbol": "AAA", "filed_date": "2024-02-12", "roe": 0.14},   # newer, still <= asof
        {"symbol": "AAA", "filed_date": "2025-02-11", "roe": 0.18},   # future vs asof
        {"symbol": "BBB", "filed_date": "2024-05-01", "roe": 0.22},
    ]
    picked = as_of(panel, "2024-06-30")
    print("  as_of 2024-06-30:")
    for sym, r in picked.items():
        print(f"    {sym}: roe={r['roe']} (filed {r['filed_date']})")
    print("  -> AAA uses the 2024 filing, NOT the 2025 one (no look-ahead).")
    print(f"  leaks_lookahead(full panel @2024-06-30) = {leaks_lookahead(panel, '2024-06-30')}\n")

    hist = {"US": 2520, "BR": 900, "JP": 240, "CN": 180}
    g = gate_markets(hist, min_years=3.0)
    print(f"  history gate (>= {g['min_years']}y = {g['min_days']}d):")
    print(f"    report:   {g['report']}")
    print(f"    withhold: {g['withhold']}")


if __name__ == "__main__":
    main()
