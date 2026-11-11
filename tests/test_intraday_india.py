"""
Tests for intraday_india.py — planted panels, so the book and cost logic are
checked against a known answer. Skipped where duckdb is not installed (the
module imports the warehouse loader).
Run: pytest -q tests/test_intraday_india.py
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("duckdb")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import intraday_india as ii  # noqa: E402


def test_fees_on_one_lakh_ticket():
    # brokerage 2 x Rs 20 = 4.0 bps, STT 2.5, txn 0.594, SEBI 0.02, stamp 0.3,
    # GST 18% of (4.0 + 0.594 + 0.02) = 0.8305 -> 8.2445 bps
    assert ii.intraday_fees_bps(100_000) == pytest.approx(8.2445, abs=1e-3)


def test_fees_brokerage_is_capped_at_rs20():
    small = ii.intraday_fees_bps(20_000)            # 0.03% < Rs 20 -> percentage applies
    large = ii.intraday_fees_bps(10_000_000)        # Rs 20 cap -> 2 x Rs 20 = 0.04 bps
    assert small > large
    assert large == pytest.approx(0.04 + 2.5 + 0.594 + 0.02 + 0.3
                                  + 0.18 * (0.04 + 0.594 + 0.02), abs=1e-3)


def _planted(T=30, N=200, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2021-01-04", periods=T)
    s = pd.DataFrame(rng.standard_normal((T, N)), idx)
    r = pd.DataFrame(0.001 * rng.standard_normal((T, N)), idx)
    r = r + 0.01 * (s.rank(axis=1, pct=True) > 0.8) - 0.01 * (s.rank(axis=1, pct=True) <= 0.2)
    return s, r


def test_daily_book_recovers_planted_quintile_spread():
    s, r = _planted()
    b = ii.daily_book(s, r)
    ls = b["long"] - b["short"]
    assert ls.mean() == pytest.approx(0.02, abs=1e-3)
    assert (b["long"] - b["univ"]).mean() == pytest.approx(0.01, abs=1e-3)
    assert (b["n"] == 200).all()


def test_daily_book_skips_thin_days_and_missing_returns():
    s, r = _planted(T=5, N=200)
    r.iloc[0, :150] = np.nan                         # day 0 has only 50 tradeable names
    b = ii.daily_book(s, r, min_names=100)
    assert s.index[0] not in b.index
    assert len(b) == 4


def test_build_lags_signal_and_drops_locked_bars():
    idx = pd.bdate_range("2016-01-01", periods=600)
    cols = [f"S{i}" for i in range(3)]
    base = pd.DataFrame(np.linspace(100, 200, 600)[:, None] * [1, 1.1, 1.2], idx, cols)
    p = {"Close": base, "Open": base * 0.99, "High": base * 1.01, "Low": base * 0.98,
         "Volume": pd.DataFrame(1e6, idx, cols)}
    p["High"].iloc[-1, 0] = p["Low"].iloc[-1, 0]     # S0 locked on the last day
    S, R = ii.build(p)
    assert np.isnan(R.iloc[-1, 0]) and not np.isnan(R.iloc[-1, 1])
    # hi52 on day t equals close/252d-high computed on day t-1
    C, H = p["Close"], p["High"]
    want = (C / H.rolling(252, min_periods=200).max()).shift(1).loc[S["hi52"].index]
    pd.testing.assert_frame_equal(S["hi52"], want, check_freq=False)
