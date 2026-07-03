#!/usr/bin/env python3
"""
survivorship.py  —  Remediation L4: remove survivorship bias.

The paper's universes are *current* liquid names, so stocks that later died
(delisted, went bankrupt, were acquired) are silently dropped — which flatters
results. This module provides the point-in-time (PIT) machinery to fix that:

  1. point_in_time_universe(membership, asof)
        Reconstruct the set of names that were IN the universe on a past date,
        from a membership-history table (add_date .. remove_date).

  2. apply_delisting_returns(returns, delistings)
        Splice each dead name's final "delisting return" into a returns panel on
        its delisting date and blank it thereafter, so the loss is counted.

  3. survivorship_gap(full, survivors_only)
        Quantify how much survivorship flattered a result (full-universe mean
        minus survivors-only mean).

Everything is pure and works on plain dicts / pandas frames, so it can be unit
tested without any external data. The *data* (real membership + delisting lists)
comes from historical index constituents + delisted-security returns (CRSP
delisting codes; free proxies: exchange delisting notices, Stooq delisted
archives, dated Wikipedia constituent history) — see data_sources.filing_source.

Not investment advice.
"""
from __future__ import annotations

from datetime import date, datetime


def _d(x) -> date:
    if x is None:
        return None
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    return datetime.strptime(str(x)[:10], "%Y-%m-%d").date()


def point_in_time_universe(membership, asof) -> list:
    """Names that were in the universe on `asof`.

    membership: iterable of {"symbol", "add_date", "remove_date"} (remove_date
    None/"" = still a member). A name is included iff add_date <= asof and
    (remove_date is None or asof < remove_date).
    Returns a sorted list of symbols — including names that later delisted, and
    excluding names that had not yet been added. This is the anti-survivorship set.
    """
    t = _d(asof)
    out = set()
    for m in membership:
        add = _d(m.get("add_date"))
        rem = _d(m.get("remove_date")) if m.get("remove_date") else None
        if add is not None and add <= t and (rem is None or t < rem):
            out.add(m["symbol"])
    return sorted(out)


def apply_delisting_returns(returns, delistings):
    """Insert each dead name's final delisting return; blank it afterwards.

    returns: pandas DataFrame indexed by date (datetime-like), columns = symbols,
             values = periodic returns (may be NaN where the name doesn't trade).
    delistings: {symbol: {"date": <date>, "final_return": <float>}}.
        final_return is the delisting-day return (often strongly negative for
        bankruptcies; can be positive for buyouts).

    On the delisting date the name's return is set to final_return; on every later
    date it is set to NaN (the name no longer exists). Returns a NEW frame.
    """
    import numpy as np  # local import keeps module import cheap for tests that don't need it
    df = returns.copy()
    idx = [_d(i) for i in df.index]
    for sym, info in delistings.items():
        if sym not in df.columns:
            continue
        ddate = _d(info["date"])
        fr = float(info["final_return"])
        for pos, dt in enumerate(idx):
            if dt == ddate:
                df.iloc[pos, df.columns.get_loc(sym)] = fr
            elif dt is not None and dt > ddate:
                df.iloc[pos, df.columns.get_loc(sym)] = np.nan
    return df


def survivorship_gap(full_universe_mean: float, survivors_only_mean: float) -> float:
    """How much survivorship flattered the result (survivors minus full).

    Positive => the survivors-only view overstated returns by this much, i.e. the
    bias you remove by including dead names.
    """
    return survivors_only_mean - full_universe_mean


def coverage(membership, asof) -> dict:
    """Quick diagnostic: how many names are live vs delisted-by-now as of a date."""
    live = point_in_time_universe(membership, asof)
    ever = sorted({m["symbol"] for m in membership})
    return {"asof": str(_d(asof)), "in_universe": len(live), "ever_listed": len(ever)}


def main():
    print("survivorship.py — remediation L4 (point-in-time universe + delisting)\n")
    membership = [
        {"symbol": "ALIVE1", "add_date": "2015-01-01", "remove_date": None},
        {"symbol": "ALIVE2", "add_date": "2018-06-01", "remove_date": None},
        {"symbol": "DIED",   "add_date": "2015-01-01", "remove_date": "2020-03-15"},
        {"symbol": "FUTURE", "add_date": "2024-01-01", "remove_date": None},
    ]
    for asof in ("2016-01-01", "2019-01-01", "2021-01-01", "2025-01-01"):
        u = point_in_time_universe(membership, asof)
        print(f"  as of {asof}: {u}")
    print("\nNote DIED is correctly IN the 2019 universe (before it delisted) and")
    print("FUTURE is correctly OUT until 2024 — neither survivorship nor look-ahead.")


if __name__ == "__main__":
    main()
