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


# ── risk.py (risk metrics) ────────────────────────────────────────────────────
def test_risk_metrics_signs_and_drawdown():
    import risk
    r = np.array([0.02, -0.03, 0.01, -0.05, 0.04, -0.01, 0.03])
    assert risk.max_drawdown(r) < 0                              # a loss occurred
    assert risk.hist_var(r, 0.05) > 0                            # VaR is a positive loss
    assert risk.cvar(r, 0.05) >= risk.hist_var(r, 0.05)          # tail mean >= quantile
    assert risk.ann_vol(r) > 0
    up = np.full(10, 0.01)
    assert risk.max_drawdown(up) == 0.0                          # never a drawdown


def test_risk_regime_flags_high_vol_drawdown():
    import risk
    rng = np.random.default_rng(1)
    calm = rng.normal(0.0005, 0.005, 200)
    stormy = np.concatenate([calm, rng.normal(-0.01, 0.03, 80)])  # vol spike + losses
    assert risk.regime_flag(stormy)["regime"] in ("caution", "risk_off")


# ── portfolio.py (constrained weights) ────────────────────────────────────────
def test_position_cap_and_normalisation():
    from portfolio import cap_weights
    w = cap_weights(np.array([0.7, 0.2, 0.1]), 0.4)
    assert w.max() <= 0.4 + 1e-9
    assert w.sum() == pytest.approx(1.0)


def test_sector_cap_limits_group_exposure():
    from portfolio import apply_sector_cap
    w = apply_sector_cap(np.array([0.5, 0.3, 0.2]), ["A", "A", "B"], 0.5)
    assert w[:2].sum() <= 0.5 + 1e-6                             # sector A capped
    assert w.sum() == pytest.approx(1.0)


def test_long_only_and_turnover_budget():
    from portfolio import long_only, turnover, blend_to_turnover
    assert (long_only(np.array([0.6, -0.2, 0.6])) >= 0).all()
    a, b = np.array([0.5, 0.5]), np.array([0.0, 1.0])
    assert turnover(a, b) == pytest.approx(0.5)
    blended = blend_to_turnover(b, a, 0.25)                      # move only halfway
    assert turnover(blended, a) <= 0.25 + 1e-9


def test_min_variance_prefers_low_vol_asset():
    from portfolio import min_variance_weights
    cov = np.array([[0.04, 0.0], [0.0, 0.16]])                   # asset0 lower variance
    w = min_variance_weights(cov)
    assert w[0] > w[1] and w.sum() == pytest.approx(1.0)


# ── meta_screen.py (ensemble fusion) ──────────────────────────────────────────
def test_fuse_renormalises_missing_and_adds_gate_bonus():
    from meta_screen import fuse
    base = fuse({"durability": 80, "valuation": 60, "momentum": 40, "ml_signal": None})
    gated = fuse({"durability": 80, "valuation": 60, "momentum": 40, "ml_signal": None},
                 gates={"triple_hit": True})
    assert gated == pytest.approx(min(100, base + 10))
    # weight renormalises over present components (no ml_signal) -> not diluted to 0
    assert 40 < base < 80


def test_fuse_clamped_to_100():
    from meta_screen import fuse
    v = fuse({"durability": 100, "valuation": 100, "momentum": 100},
             gates={"a": True, "b": True})
    assert v == 100.0


# ── fx.py (currency normalisation) ────────────────────────────────────────────
def test_fx_currency_map_and_return_composition():
    import fx
    assert fx.market_currency("KR") == "KRW"
    assert fx.market_currency("JP") == "JPY"
    assert fx.combine_return(0.10, 0.05) == pytest.approx(0.155)   # 1.1*1.05-1
    assert fx.convert_level(100, 0.0064) == pytest.approx(0.64)    # JPY->USD


def test_fx_normalize_cross_market_levels():
    import fx
    df = pd.DataFrame({"market": ["US", "JP"], "mktcap": [100.0, 100.0]})
    rates = {"USD": 1.0, "JPY": 0.0064}
    out = fx.normalize_cross_market(df, "mktcap", "market", rates)
    assert out["mktcap_usd"].iloc[0] == pytest.approx(100.0)
    assert out["mktcap_usd"].iloc[1] == pytest.approx(0.64)        # now comparable


# ── incremental.py (partition-incremental refresh, F9.1) ──────────────────────
def test_partition_diff_add_remove_change():
    from incremental import partition_diff
    prev = pd.DataFrame({"k": [1, 2, 3], "v": [10, 20, 30]})
    curr = pd.DataFrame({"k": [2, 3, 4], "v": [20, 99, 40]})
    d = partition_diff(prev, curr, "k")
    assert d["added"] == [4] and d["removed"] == [1] and d["changed"] == [3]


def test_incremental_merge_upserts():
    from incremental import incremental_merge
    base = pd.DataFrame({"k": [1, 2, 3], "v": [10, 20, 30]})
    new = pd.DataFrame({"k": [3, 4], "v": [99, 40]})
    m = incremental_merge(base, new, "k").set_index("k")
    assert m.loc[3, "v"] == 99 and m.loc[4, "v"] == 40 and m.loc[1, "v"] == 10


def test_append_new_dates_only_newer():
    from incremental import append_new_dates
    base = pd.DataFrame({"Symbol": ["A", "A"],
                         "Date": pd.to_datetime(["2024-01-01", "2024-01-02"]), "Close": [1, 2]})
    new = pd.DataFrame({"Symbol": ["A", "A"],
                        "Date": pd.to_datetime(["2024-01-02", "2024-01-03"]), "Close": [2, 3]})
    merged = append_new_dates(base, new, "Date", "Symbol")
    assert len(merged) == 3                                       # only 01-03 appended


# ── feature_cache.py (ML feature cache, F9.2) ─────────────────────────────────
def test_feature_cache_key_stable_and_sensitive():
    from feature_cache import cache_key
    df = pd.DataFrame({"Close": [1, 2, 3], "High": [1, 2, 3], "Low": [1, 2, 3], "Volume": [9, 8, 7]})
    assert cache_key("AAPL", df) == cache_key("AAPL", df.copy())  # deterministic
    df2 = df.copy(); df2.loc[2, "Close"] = 99
    assert cache_key("AAPL", df2) != cache_key("AAPL", df)        # data change -> new key


# ── serve.py (serving layer, F8.1) ────────────────────────────────────────────
def test_serve_build_query_and_injection_guard():
    import serve
    assert "LIMIT 10" in serve.build_query("ggg", 10)
    with pytest.raises(KeyError):
        serve.build_query("nonexistent")
    assert serve.validate_predicate("roe>15 and de<1")
    for bad in ["roe>15; DROP TABLE x", "1=1 -- x", "evil>0"]:
        with pytest.raises(ValueError):
            serve.validate_predicate(bad)


# ── sector_rotation.py (industry momentum ranking) ────────────────────────────
def test_sector_rank_orders_by_momentum():
    from sector_rotation import rank_groups
    mem = pd.DataFrame({"industry": ["X"] * 3 + ["Y"] * 3,
                        "momentum": [0.2, 0.1, 0.15, -0.1, -0.05, -0.2]})
    rk = rank_groups(mem, "industry", min_members=3)
    assert list(rk["industry"]) == ["X", "Y"]                    # strong industry first
    assert rk.iloc[0]["rank"] == 1


def test_member_momentum_skips_recent_month():
    from sector_rotation import member_momentum
    c = pd.Series(np.linspace(100, 200, 300))                    # steady uptrend
    assert member_momentum(c) > 0
    assert np.isnan(member_momentum(pd.Series([1, 2, 3])))       # too short


# ── unlisted_valuation.py (comps) ─────────────────────────────────────────────
def test_peer_band_drops_bad_multiples_and_values():
    from unlisted_valuation import peer_multiple_band, value_range, implied_value
    band = peer_multiple_band([10, 20, 30, 40, -5, float("inf"), None])
    assert band["n"] == 4 and band["median"] == 25.0             # negatives/inf/None dropped
    vr = value_range(1_000_000, band)
    assert vr["mid"] == implied_value(1_000_000, 25.0)
    assert vr["low"] < vr["mid"] < vr["high"]


# ── data_quality.py (observability rules, B9) ─────────────────────────────────
def test_data_quality_rules():
    import data_quality as dq
    assert dq.null_rate([1, None, 3]) == pytest.approx(1 / 3)
    assert dq.staleness_days(["2024-01-01", "2024-01-10"], asof="2024-01-20") == 10
    assert dq.is_monotonic_dates(["2024-01-01", "2024-01-02"]) is True
    assert dq.is_monotonic_dates(["2024-01-02", "2024-01-01"]) is False
    normal = list(range(1, 21))
    assert dq.outlier_rate(normal + [100000]) > 0                # one absurd value flagged
    assert dq.outlier_rate(normal) == 0.0


def test_data_quality_evaluate_pass_fail():
    import data_quality as dq
    rows = dq.evaluate("src", {"stale": 5, "nulls": 0.5},
                       {"stale": ("<=", 30), "nulls": ("<=", 0.2)})
    status = {r["rule"]: r["status"] for r in rows}
    assert status["stale"] == "PASS" and status["nulls"] == "FAIL"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
