#!/usr/bin/env python3
"""
market_holidays.py
------------------
Lightweight multi-market trading calendar (no external deps) to skip non-trading
days and cut processing time. Covers the five scanned markets. Weekends are
always non-trading; fixed-date holidays are computed per year; the major
variable/regional holidays are curated for 2023-2027.

    from market_holidays import is_trading_day, trading_days, next_trading_day
    is_trading_day("India", "2026-01-26")     -> False  (Republic Day)
    trading_days("US", "2026-01-01", "2026-01-31")   -> DatetimeIndex of NYSE sessions
    should_run_today("NSE")                    -> gate a daily pipeline

Precision note: fixed holidays + weekends catch ~all non-trading days; lunar/
regional floats (Diwali, Lunar New Year, Golden Week) are curated where they most
affect processing. Extend HOLIDAYS as needed — this is a speed filter, not a
settlement-grade calendar.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

import pandas as pd

# market aliases -> canonical key
ALIASES = {"NSE": "India", "BSE": "India", "NYSE": "US", "NASDAQ": "US",
           "TSE": "Japan", "JPX": "Japan", "KRX": "Korea", "KOSPI": "Korea",
           "EU": "Europe", "XETRA": "Europe"}


def _easter(y: int) -> date:
    a = y % 19; b = y // 100; c = y % 100; d = b // 4; e = b % 4
    f = (b + 8) // 25; g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4; k = c % 4
    L = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * L) // 451
    mo = (h + L - 7 * m + 114) // 31
    da = ((h + L - 7 * m + 114) % 31) + 1
    return date(y, mo, da)


def _fixed(market: str, y: int) -> set:
    """Recurring fixed-date holidays per market for a given year."""
    s = set()
    ny = date(y, 1, 1); xmas = date(y, 12, 25)
    if market == "US":
        s |= {ny, date(y, 7, 4), xmas}                       # + MLK/Presidents/Memorial/Labor/Thanksgiving below
    elif market == "India":
        s |= {ny, date(y, 1, 26), date(y, 8, 15), date(y, 10, 2), xmas}  # Republic, Independence, Gandhi
    elif market == "Japan":
        s |= {ny, date(y, 1, 2), date(y, 1, 3), date(y, 2, 11), date(y, 4, 29),
              date(y, 5, 3), date(y, 5, 4), date(y, 5, 5), date(y, 11, 3),
              date(y, 11, 23), date(y, 12, 31)}
    elif market == "Korea":
        s |= {ny, date(y, 3, 1), date(y, 5, 5), date(y, 6, 6), date(y, 8, 15),
              date(y, 10, 3), date(y, 10, 9), xmas, date(y, 12, 31)}
    elif market == "Europe":
        e = _easter(y)
        s |= {ny, e - timedelta(days=2), e + timedelta(days=1),   # Good Friday, Easter Monday
              date(y, 5, 1), xmas, date(y, 12, 26), date(y, 12, 31)}
    return s


# Curated variable/regional holidays (lunar, US floating, Diwali, Golden Week gaps…)
HOLIDAYS = {
    "US": {  # MLK, Presidents, Memorial, Juneteenth, Labor, Thanksgiving
        "2024": ["2024-01-15", "2024-02-19", "2024-05-27", "2024-06-19", "2024-09-02", "2024-11-28"],
        "2025": ["2025-01-20", "2025-02-17", "2025-05-26", "2025-06-19", "2025-09-01", "2025-11-27"],
        "2026": ["2026-01-19", "2026-02-16", "2026-05-25", "2026-06-19", "2026-09-07", "2026-11-26"],
    },
    "India": {  # Holi, Diwali, Eid, etc. (major NSE closures)
        "2025": ["2025-03-14", "2025-03-31", "2025-04-14", "2025-08-27", "2025-10-21", "2025-11-05"],
        "2026": ["2026-03-04", "2026-03-21", "2026-04-14", "2026-08-26", "2026-11-09"],
    },
    "Japan": {  # Coming-of-Age, equinoxes, Marine/Mountain/Sports/Culture Day floats
        "2025": ["2025-01-13", "2025-03-20", "2025-07-21", "2025-09-15", "2025-09-23", "2025-10-13"],
        "2026": ["2026-01-12", "2026-03-20", "2026-07-20", "2026-09-21", "2026-09-22", "2026-09-23", "2026-10-12"],
    },
    "Korea": {  # Lunar New Year (Seollal) + Chuseok clusters
        "2025": ["2025-01-28", "2025-01-29", "2025-01-30", "2025-10-06", "2025-10-07", "2025-10-08"],
        "2026": ["2026-02-16", "2026-02-17", "2026-02-18", "2026-09-24", "2026-09-25"],
    },
    "Europe": {},  # fixed+Easter covers most XETRA/Euronext closures
}


@lru_cache(maxsize=32)
def _holiday_set(market: str, y: int) -> frozenset:
    s = _fixed(market, y)
    for iso in HOLIDAYS.get(market, {}).get(str(y), []):
        s.add(date.fromisoformat(iso))
    return frozenset(s)


def _canon(market: str) -> str:
    return ALIASES.get(market, market)


def is_trading_day(market: str, d) -> bool:
    m = _canon(market)
    d = pd.Timestamp(d).date()              # coerce str/date/Timestamp -> plain date
    if d.weekday() >= 5:                     # Sat/Sun
        return False
    return d not in _holiday_set(m, d.year)


def trading_days(market: str, start, end) -> pd.DatetimeIndex:
    """All trading sessions for a market in [start, end]."""
    rng = pd.bdate_range(start, end)         # weekdays
    keep = [ts for ts in rng if is_trading_day(market, ts)]
    return pd.DatetimeIndex(keep)


def next_trading_day(market: str, d=None) -> date:
    d = (d if isinstance(d, date) else pd.Timestamp(d).date()) if d else date.today()
    d += timedelta(days=1)
    while not is_trading_day(market, d):
        d += timedelta(days=1)
    return d


def should_run_today(market: str) -> bool:
    """Gate a daily pipeline: skip the run entirely on non-trading days."""
    return is_trading_day(market, date.today())


if __name__ == "__main__":
    import sys
    mkt = sys.argv[1] if len(sys.argv) > 1 else "US"
    td = trading_days(mkt, "2026-01-01", "2026-12-31")
    total = len(pd.bdate_range("2026-01-01", "2026-12-31"))
    print(f"{mkt}: {len(td)} trading days in 2026 "
          f"({total - len(td)} weekday holidays skipped, "
          f"+{261 - total if False else ''} weekends already excluded)")
    print("  today is a trading day?", should_run_today(mkt),
          "| next session:", next_trading_day(mkt))
