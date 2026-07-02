"""
Core unit tests — the SDLC 'Integration & Testing' phase.
Covers deterministic, pure-logic paths (no network / Cassandra / EDGAR required):
calendars, rate limiter, cost model, PIT filing-date filtering, features, factor
regression, DVM scoring. Run: pytest -q
"""
import os
import sys
import time

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── market_holidays ───────────────────────────────────────────────────────────
def test_holidays_known_dates():
    from market_holidays import is_trading_day
    assert is_trading_day("India", "2026-01-26") is False      # Republic Day
    assert is_trading_day("US", "2026-12-25") is False          # Christmas
    assert is_trading_day("US", "2026-06-13") is False          # Saturday
    assert is_trading_day("US", "2026-06-15") is True           # normal Monday
    assert is_trading_day("NSE", "2026-08-15") is False         # alias + Independence Day


def test_trading_days_excludes_holidays():
    from market_holidays import trading_days
    td = trading_days("US", "2026-01-01", "2026-12-31")
    allw = pd.bdate_range("2026-01-01", "2026-12-31")
    assert 240 < len(td) < len(allw)                            # holidays removed, weekdays only
    assert pd.Timestamp("2026-12-25") not in td


# ── apiclient (rate governance) ───────────────────────────────────────────────
def test_rate_error_classification():
    from apiclient import _is_rate_error
    assert _is_rate_error(Exception("HTTP 429 Too Many Requests"))
    assert _is_rate_error(Exception("Invalid Crumb"))
    assert not _is_rate_error(ValueError("bad ticker"))


def test_throttle_enforces_min_interval():
    from apiclient import _Source
    s = _Source(0.05, 2)
    t0 = time.monotonic()
    for _ in range(4):
        s.acquire(); s.release()
    assert time.monotonic() - t0 >= 0.05 * 2                    # spacing enforced


def test_penalty_grows_and_decays():
    from apiclient import _Source
    s = _Source(0.1, 1)
    s.penalize(); s.penalize()
    assert s.penalty >= 4.0
    for _ in range(20):
        s.relax()
    assert s.penalty == pytest.approx(1.0, abs=0.01)


# ── apply_costs (net-of-cost) ─────────────────────────────────────────────────
def test_net_edge_subtracts_cost():
    import apply_costs as ac
    df = pd.DataFrame({"market": ["US", "India"], "screen": ["x", "y"],
                       "avg_hit_pct": [60, 40], "avg_edge": [0.5, 0.2]})
    nt = ac.net_table(df)
    us = nt[nt.market == "US"].iloc[0]
    assert us["net_edge"] == pytest.approx(0.5 - ac.COSTS_PCT["US"], abs=1e-6)
    assert us["net_viable"] == "YES"                            # edge>0 & hit>50
    ind = nt[nt.market == "India"].iloc[0]
    assert ind["net_viable"] == "no"                            # hit 40 < 50


# ── pit_fundamentals (point-in-time) ──────────────────────────────────────────
def test_annual_asof_respects_filing_date_and_duration():
    from pit_fundamentals import _annual_asof
    facts = {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
        {"form": "10-K", "fp": "FY", "start": "2020-01-01", "end": "2020-12-31", "filed": "2021-02-01", "val": 100},
        {"form": "10-K", "fp": "FY", "start": "2021-01-01", "end": "2021-12-31", "filed": "2022-02-01", "val": 120},
        {"form": "10-K", "fp": "FY", "start": "2021-10-01", "end": "2021-12-31", "filed": "2022-02-01", "val": 30},  # quarterly, must be dropped
    ]}}}}
    # as of mid-2021 only the 2020 annual is known
    assert _annual_asof(facts, "NetIncomeLoss", "2021-06-01") == [100]
    # as of 2022-06 both annuals known, newest first; quarterly (30) excluded by duration filter
    assert _annual_asof(facts, "NetIncomeLoss", "2022-06-01") == [120, 100]


# ── ml_signal_engine (features) ───────────────────────────────────────────────
def test_compute_features_columns_and_finiteness():
    from ml_signal_engine import compute_features, FEATURE_NAMES
    idx = pd.date_range("2021-01-01", periods=400, freq="B")
    rng = np.random.default_rng(0)
    px = 100 + np.cumsum(rng.normal(0, 1, 400))
    df = pd.DataFrame({"Close": px, "High": px + 1, "Low": px - 1,
                       "Volume": rng.integers(1e5, 1e6, 400)}, index=idx)
    f = compute_features(df)
    assert list(f.columns) == FEATURE_NAMES
    assert len(f) > 100 and np.isfinite(f.values).all()


# ── factor_research (OLS with t-stats) ────────────────────────────────────────
def test_ols_recovers_known_coefficients():
    from factor_research import ols
    x = np.linspace(0, 10, 200)
    y = 2.0 * x + 1.0                                           # exact line
    out = ols(y, x.reshape(-1, 1), ["beta"])
    assert out["beta"][0] == pytest.approx(2.0, abs=1e-6)
    assert out["intercept"][0] == pytest.approx(1.0, abs=1e-6)
    assert out["_R2"] == pytest.approx(1.0, abs=1e-9)


# ── dvm_composite (durability scoring) ────────────────────────────────────────
def test_durability_scoring_bounds_and_direction():
    from dvm_composite import durability
    strong = durability({"roe": 25, "de": 0.3, "rev_growth": 20, "op_margin": 25, "earn_growth": 10})
    weak = durability({"roe": -5, "de": 3.0, "rev_growth": -10, "op_margin": -5, "earn_growth": -10})
    assert 0 <= weak < 50 < strong <= 100                      # ordered and in range


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
