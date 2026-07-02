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


# ── quality_factor.py (AFP/QMJ quality factor — IIMA 2022) ────────────────────
def test_z_rank_monotonic_and_centered():
    from quality_factor import z_rank
    z = z_rank(pd.Series([10, 20, 30, 40, 50]))
    assert z.is_monotonic_increasing                            # rank order preserved
    assert z.mean() == pytest.approx(0.0, abs=1e-9)             # standardised
    assert z.iloc[0] < 0 < z.iloc[-1]


def test_dimension_score_applies_sign():
    from quality_factor import dimension_score
    df = pd.DataFrame({"de": [1.0, 2.0, 3.0]})                  # lower leverage = better
    s = dimension_score(df, [("de", -1)])
    assert s.iloc[0] > s.iloc[2]                                # low-de firm scores higher


def test_quality_score_ranks_all_rounder_top():
    from quality_factor import quality_score, DIMENSIONS
    df = pd.DataFrame({
        "ticker": ["GOOD", "MID", "BAD"],
        "roe": [30, 15, -5], "roa": [20, 8, -2], "op_margin": [30, 12, -8],
        "rev_growth": [25, 8, -10], "earn_growth": [20, 5, -15],
        "de": [0.2, 1.0, 3.0], "beta": [0.7, 1.0, 1.8], "vol": [0.15, 0.3, 0.6],
        "div_yield": [3.0, 1.5, 0.0], "mktcap": [1e9, 5e8, 1e8], "pb": [8, 3, 1],
    })
    scored = quality_score(df)
    assert set(DIMENSIONS).issubset(scored.columns) and "quality" in scored.columns
    top = scored.sort_values("quality", ascending=False)["ticker"].iloc[0]
    assert top == "GOOD"                                        # best on every dimension


def test_qmj_and_lq_combination_formulas():
    from quality_factor import qmj_combo, lq_combo
    legs = {"small_quality": 2.0, "big_quality": 4.0, "small_junk": 1.0, "big_junk": 1.0}
    assert qmj_combo(legs) == pytest.approx(0.5 * 6 - 0.5 * 2)  # = 2.0
    assert lq_combo(legs) == pytest.approx(3.0)                 # ½(small_q+big_q)


def test_value_weight_proportional_and_normalised():
    from quality_factor import value_weight
    w = value_weight(pd.Series([1.0, 3.0]))
    assert w.sum() == pytest.approx(1.0)
    assert w.iloc[1] == pytest.approx(0.75)                     # weight ∝ market cap


def test_assign_deciles_labels_extremes():
    from quality_factor import assign_deciles
    lab = assign_deciles(pd.Series(np.arange(100.0)))
    assert lab.iloc[-1] == "quality" and lab.iloc[0] == "junk"


def test_price_premium_detects_positive_quality_premium():
    from quality_factor import price_premium
    rng = np.random.default_rng(3)
    n = 120
    q = rng.normal(0, 1, n)                                     # standardised quality
    logmb = 0.4 * q + rng.normal(0, 0.05, n)                    # M/B rises with quality
    df = pd.DataFrame({"quality": q, "pb": np.exp(logmb),
                       "mktcap": np.exp(rng.normal(20, 1, n)), "market": ["US"] * n})
    pp = price_premium(df)
    assert pp["quality_coef"] > 0 and pp["quality_t"] > 2       # significant premium


# ── literature_scout.py (global research scout) ───────────────────────────────
def test_reconstruct_abstract_from_inverted_index():
    from literature_scout import reconstruct_abstract
    inv = {"Quality": [0], "factor": [1], "earns": [2], "alpha": [3]}
    assert reconstruct_abstract(inv) == "Quality factor earns alpha"
    assert reconstruct_abstract(None) == ""


def test_score_paper_covered_vs_gap_classification():
    from literature_scout import score_paper
    covered = score_paper({"title": "Quality Minus Junk profitability factor",
                           "year": 2019, "citations": 2000})
    assert covered["coverage"] in ("covered", "extends")
    assert "quality_factor.py" in covered["modules"]
    # PEAD used to be a gap; the scout->implement loop closed it (now pead_factor.py)
    pead = score_paper({"title": "Post-earnings-announcement drift and analyst revisions",
                        "year": 2021, "citations": 100})
    assert pead["coverage"] in ("covered", "extends")
    assert "pead_factor.py" in pead["modules"]
    # ESG was a frontier gap; now implemented (esg_screen.py) -> covered
    esg = score_paper({"title": "ESG factor and climate risk premium in equities",
                       "year": 2022, "citations": 80})
    assert esg["coverage"] in ("covered", "extends") and "esg_screen.py" in esg["modules"]
    # a genuinely off-topic paper maps to nothing
    off = score_paper({"title": "A study of igneous rock crystallisation", "year": 2000})
    assert off["coverage"] == "unmapped"
    unmapped = score_paper({"title": "A study of igneous rock formation", "year": 2000})
    assert unmapped["coverage"] == "unmapped"


def test_score_paper_ranks_relevant_recent_cited_higher():
    from literature_scout import score_paper
    strong = score_paper({"title": "Momentum and value factor in the cross-section of returns",
                          "year": 2022, "citations": 5000})
    weak = score_paper({"title": "Momentum note", "year": 1970, "citations": 1})
    assert strong["score"] > weak["score"]


def test_dedup_by_title_and_doi():
    from literature_scout import dedup
    papers = [{"title": "Quality Minus Junk", "doi": "10.1/x"},
              {"title": "quality minus junk", "doi": ""},        # same title, dropped
              {"title": "Other", "doi": "10.1/x"}]              # same doi as #1, dropped
    assert len(dedup(papers)) == 2


def test_rank_orders_and_coverage_summary_counts():
    from literature_scout import rank, coverage_summary, SEED_PAPERS
    ranked = rank(SEED_PAPERS)
    scores = [p["score"] for p in ranked]
    assert scores == sorted(scores, reverse=True)              # descending
    summ = coverage_summary(ranked)
    assert summ["covered"]["quality"] >= 2                     # QMJ + Novy-Marx + IIMA etc.


def test_strip_jats_and_report_sections():
    from literature_scout import _strip_jats, render_report, rank, coverage_summary, SEED_PAPERS
    assert _strip_jats("<jats:p>Hello <b>world</b></jats:p>").strip() == "Hello  world".strip()
    ranked = rank(SEED_PAPERS)
    md = render_report(ranked, coverage_summary(ranked), query=None)
    assert "## Top relevant papers" in md and "## Research gaps" in md and "## Coverage summary" in md


# ── pead_factor.py (post-earnings-announcement drift) ─────────────────────────
def test_sue_standardised_unexpected_earnings():
    from pead_factor import sue
    assert sue(120, 100, 10) == pytest.approx(2.0)              # 2 std beat
    assert np.isnan(sue(120, 100, 0))                           # zero std -> undefined


def test_market_adjust_and_car():
    from pead_factor import market_adjust, car
    stock = pd.Series([0.02, -0.01, 0.03, 0.00])
    mkt = pd.Series([0.01, -0.01, 0.01, 0.01])
    abn = market_adjust(stock, mkt)
    assert abn.iloc[0] == pytest.approx(0.01)                   # 0.02 − 0.01
    assert car(abn, 0, 3) == pytest.approx(abn.sum())          # full-window CAR = sum


def test_detect_events_finds_volume_return_spike():
    from pead_factor import detect_events
    n = 120
    close = pd.Series(100 + np.zeros(n), dtype=float)
    close.iloc[80] = 112                                        # +12% jump on day 80
    close.iloc[81:] = 112
    vol = pd.Series(1e5, index=range(n), dtype=float)
    vol.iloc[80] = 1e6                                          # 10x volume spike
    ev = detect_events(close.reset_index(drop=True), vol, lookback=20)
    assert 80 in ev                                             # the earnings-proxy day is caught


def test_pead_score_direction_and_decay():
    from pead_factor import pead_score
    fresh_pos = pead_score(0.10, days_since=0)
    old_pos = pead_score(0.10, days_since=40)
    assert fresh_pos > 50 and old_pos > 50                      # positive surprise -> bullish
    assert fresh_pos > old_pos                                  # decays over the window
    assert pead_score(-0.10, 0) < 50                            # negative surprise -> bearish
    assert pead_score(0.10, days_since=999) == 50.0            # past the window -> neutral


def test_drift_by_surprise_monotone_when_pead_present():
    from pead_factor import drift_by_surprise, monotonicity
    rng = np.random.default_rng(5)
    n = 400
    surprise = rng.normal(0, 0.05, n)
    fwd = 0.4 * surprise + rng.normal(0, 0.01, n)               # drift follows surprise (PEAD)
    curve = drift_by_surprise(pd.DataFrame({"surprise": surprise, "fwd_car": fwd}), q=5)
    assert list(curve["mean%"]) == sorted(curve["mean%"])       # monotone increasing
    assert monotonicity(curve) == pytest.approx(1.0)           # perfect PEAD ordering


# ── liquidity_factor.py (Amihud illiquidity + liquidity premium) ──────────────
def test_amihud_illiq_higher_for_thin_volume():
    from liquidity_factor import amihud_illiq
    rets = [0.02, -0.02, 0.01, -0.01, 0.02, -0.02]
    liquid = amihud_illiq(rets, [1e9] * 6)                     # deep dollar-volume
    thin = amihud_illiq(rets, [1e6] * 6)                       # thin dollar-volume
    assert thin > liquid > 0                                   # same moves, less volume => more illiquid
    assert np.isnan(amihud_illiq([0.01] * 6, [0] * 6))         # zero volume => undefined


def test_capacity_score_inverts_illiquidity():
    from liquidity_factor import capacity_score, illiq_pctile
    illiq = pd.Series([0.1, 1.0, 10.0], index=["liq", "mid", "illiq"])
    cap = capacity_score(illiq)
    assert cap["liq"] > cap["mid"] > cap["illiq"]              # liquid name -> high capacity
    pct = illiq_pctile(illiq)
    assert pct["illiq"] > pct["liq"]                           # illiquid -> high ILLIQ percentile


def test_zero_return_frac():
    from liquidity_factor import zero_return_frac
    assert zero_return_frac([0.0, 0.0, 0.05, -0.03]) == pytest.approx(0.5)


def test_liquidity_premium_quantile_monotone():
    from liquidity_factor import premium_by_illiq, monotonicity
    rng = np.random.default_rng(7)
    n = 500
    illiq = np.abs(rng.normal(1, 0.5, n))
    fwd = 0.05 * illiq + rng.normal(0, 0.005, n)               # illiquid earn more (premium)
    curve = premium_by_illiq(pd.DataFrame({"illiq": illiq, "fwd_ret": fwd}), q=5)
    assert list(curve["mean_fwd%"]) == sorted(curve["mean_fwd%"])   # Q1<...<Q5
    assert monotonicity(curve) == pytest.approx(1.0)


# ── data_sources.py (per-market public factor-source registry) ────────────────
def test_paper_sources_match_the_iima_paper():
    from data_sources import paper_sources
    p = paper_sources()
    assert "CMIE Prowess" in p["returns_and_fundamentals"]["name"]
    assert p["returns_and_fundamentals"]["license"].startswith("COMMERCIAL")   # not public
    assert "IFFM" in p["factor_benchmark"]["name"] or "Fama-French-Momentum" in p["factor_benchmark"]["name"]
    assert p["factor_benchmark"]["license"] == "public/free"                    # the public one
    assert "iima.ac.in" in p["factor_benchmark"]["url"]


def test_for_market_mappings():
    from data_sources import for_market
    us = for_market("US")
    assert us["ken_french_region"] == "North America" and us["aqr_country_qmj"] is True
    assert "edgar" in us["raw_sources"]                       # US has point-in-time EDGAR
    ind = for_market("IN")
    assert ind["currency"] == "INR" and "iffm" in ind["public_factor_sources"]  # paper's library
    cn = for_market("CN")
    assert cn["ken_french_region"] == "Emerging" and cn["aqr_country_qmj"] is False


def test_every_platform_market_has_currency_and_public_benchmark():
    from data_sources import PLATFORM_MARKETS, for_market
    for m in PLATFORM_MARKETS:
        d = for_market(m)
        assert d["currency"], m
        assert len(d["public_factor_sources"]) >= 1           # always a public benchmark
        assert "ken_french" in d["public_factor_sources"]     # Ken French covers every region


# ── benchmark.py (real Ken French factors + alpha regression) ─────────────────
def test_parse_ff_csv_dates_scaling_and_missing():
    from benchmark import parse_ff_csv
    text = ("This file was created by ...\n"
            "\n"
            ",Mkt-RF,SMB,HML,RMW,CMA,RF\n"
            "20240102,    1.00,    0.50,   -0.20,    0.10,    0.05,    0.02\n"
            "20240103,   -0.50,   -0.25,    0.30,  -99.99,    0.00,    0.02\n"
            "\n"
            "  Annual Factors: January-December\n"
            "2024,   10.0,    5.0,   -2.0,    1.0,    0.5,    0.2\n")
    df = parse_ff_csv(text)
    assert list(df.columns) == ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"]
    assert len(df) == 2                                        # annual block excluded
    assert df["Mkt-RF"].iloc[0] == pytest.approx(0.01)        # percent -> decimal
    assert np.isnan(df["RMW"].iloc[1])                        # -99.99 -> NaN
    assert str(df.index[0].date()) == "2024-01-02"


def test_carhart_alpha_recovers_loadings_and_alpha():
    from benchmark import carhart_alpha
    rng = np.random.default_rng(11)
    n = 300
    idx = pd.date_range("2023-01-02", periods=n, freq="B")
    fac = pd.DataFrame({
        "Mkt-RF": rng.normal(0, 0.01, n), "SMB": rng.normal(0, 0.005, n),
        "HML": rng.normal(0, 0.005, n), "Mom": rng.normal(0, 0.006, n)}, index=idx)
    port = 0.0003 + 1.2 * fac["Mkt-RF"] - 0.5 * fac["HML"] + rng.normal(0, 0.001, n)
    res = carhart_alpha(port, fac)
    assert res["loadings"]["Mkt-RF"][0] == pytest.approx(1.2, abs=0.1)
    assert res["loadings"]["HML"][0] == pytest.approx(-0.5, abs=0.1)
    assert res["alpha_daily"] == pytest.approx(0.0003, abs=1e-4)
    assert res["alpha_reliable"] is False                     # n=300 < 400 -> flagged unreliable


def test_factor_premia_annualises():
    from benchmark import factor_premia
    idx = pd.date_range("2023-01-02", periods=252, freq="B")
    fac = pd.DataFrame({"Mkt-RF": np.full(252, 0.0004)}, index=idx)   # +0.04%/day
    prem = factor_premia(fac)
    assert prem.loc[0, "ann_mean%"] == pytest.approx(0.0004 * 252 * 100, abs=0.5)


# ── hft_selection.py (HFT-archetype picker from daily OHLC) ────────────────────
def test_efficiency_ratio_trend_vs_chop():
    from hft_selection import efficiency_ratio
    assert efficiency_ratio([1, 2, 3, 4, 5]) == pytest.approx(1.0)        # pure trend
    assert efficiency_ratio([1, 2, 1, 2, 1]) == pytest.approx(0.0)        # pure chop
    assert efficiency_ratio([1, 3, 2, 4, 3]) == pytest.approx(2 / 6)      # net 2 / travel 6


def test_daily_range_and_avg():
    from hft_selection import daily_range_pct, avg_range
    r = daily_range_pct([11, 12], [9, 10], [10, 11])
    assert r[0] == pytest.approx(0.2) and r[1] == pytest.approx(2 / 11)
    assert avg_range([11, 12], [9, 10], [10, 11]) == pytest.approx((0.2 + 2 / 11) / 2)


def test_corwin_schultz_wider_range_higher_spread():
    from hft_selection import corwin_schultz_spread
    tight = corwin_schultz_spread([10.05, 10.05], [10.00, 10.00])
    wide = corwin_schultz_spread([10.60, 10.55], [10.00, 10.05])
    assert wide >= tight >= 0                                             # more high-low => wider spread


def test_lag1_autocorr_sign():
    from hft_selection import lag1_autocorr
    assert lag1_autocorr([1, -1, 1, -1, 1, -1]) < 0                       # alternating => reversion
    assert lag1_autocorr([1, 2, 3, 4, 5, 6]) > 0                          # trending => persistence


def test_ou_half_life_trend_is_infinite():
    from hft_selection import ou_half_life
    assert ou_half_life([1, 2, 3, 4, 5, 6]) == np.inf                     # not mean-reverting
    # smooth AR(1) reversion toward 10 (phi=0.6, so b=-0.4) -> finite half-life
    smooth = [12.0, 11.2, 10.72, 10.432, 10.2592, 10.1555, 10.0933, 10.056, 10.0336]
    hl = ou_half_life(smooth)
    assert np.isfinite(hl) and hl > 0


def test_archetype_scores_route_names_correctly():
    from hft_selection import archetype_scores
    feat = pd.DataFrame({
        "ticker": ["MM", "SA", "LAT"],
        "avg_range%": [0.3, 3.0, 3.0], "cs_spread": [0.001, 0.01, 0.01],
        "range_stability": [0.001, 0.02, 0.02], "eff_ratio": [0.05, 0.1, 0.98],
        "ret_autocorr": [0.0, -0.7, 0.6], "vol_autocorr": [0.1, 0.1, 0.9],
        "half_life": [30.0, 0.5, np.inf],
    })
    s = archetype_scores(feat).set_index("ticker")
    assert s["market_making"].idxmax() == "MM"                           # tight/stable/low-tox
    assert s["stat_arb"].idxmax() == "SA"                                # strong mean reversion
    assert s["latency"].idxmax() == "LAT"                                # persistent/predictable


# ── darvas_volume.py (Darvas box × volume-acquisition monitor) ────────────────
def test_obv_accumulates_on_up_closes():
    from darvas_volume import obv
    o = obv([10, 11, 10, 11], [100, 100, 100, 100])
    assert o[-1] == pytest.approx(100)                         # +100 −100 +100
    assert obv([10, 11, 12, 13], [100] * 4)[-1] == pytest.approx(300)   # all up


def test_chaikin_money_flow_sign():
    from darvas_volume import chaikin_money_flow
    # closes at the high each day -> strong accumulation (CMF -> +1)
    hi = chaikin_money_flow([10, 11], [9, 10], [10, 11], [100, 100])
    lo = chaikin_money_flow([10, 11], [9, 10], [9, 10], [100, 100])   # closes at the low
    assert hi == pytest.approx(1.0) and lo == pytest.approx(-1.0)


def test_up_down_volume_ratio():
    from darvas_volume import up_down_volume_ratio
    r = up_down_volume_ratio([10, 11, 10, 12], [100, 300, 100, 200])  # up vol 500, down vol 100
    assert r == pytest.approx(5.0)


def test_trend_corr_direction():
    from darvas_volume import trend_corr
    assert trend_corr([1, 2, 3, 4, 5]) == pytest.approx(1.0)
    assert trend_corr([5, 4, 3, 2, 1]) == pytest.approx(-1.0)
    assert np.isnan(trend_corr([3, 3, 3]))


def test_darvas_box_excludes_current_bar():
    from darvas_volume import darvas_box, box_state
    highs = [5, 6, 7, 8, 10, 9, 9, 9, 12]      # peak 10 holds 3 bars; current bar (12) is a breakout
    lows = [4, 5, 6, 7, 9, 8, 8, 8, 11]
    box = darvas_box(highs, lows, lookback=40, confirm=3)      # current bar excluded (design rule)
    assert box["top"] == pytest.approx(10) and box["bottom"] == pytest.approx(8)
    # including the current bar would swallow the breakout -> top becomes 12
    assert darvas_box(highs, lows, exclude_current=False)["top"] == pytest.approx(12)
    st = box_state(close_last=12, high_last=12, low_last=11, box=box, vol_last=300, vol_avg=100)
    assert st["state"] == "breakout" and st["vol_confirmed"] is True    # 300 >= 1.5×100


def test_box_state_in_box_and_breakdown():
    from darvas_volume import box_state
    box = {"top": 10.0, "bottom": 8.0}
    assert box_state(9, 9.1, 8.9, box, 100, 100)["state"] == "in_box"
    assert box_state(7, 7.1, 6.9, box, 100, 100)["state"] == "breakdown"
    assert box_state(9, 9, 9, box, 100, 100)["position"] == pytest.approx(0.5)


def test_accumulation_score_ranks_stealth_accumulation_higher():
    from darvas_volume import accumulation_score
    df = pd.DataFrame({
        "obv_trend": [0.9, -0.5], "ad_trend": [0.9, -0.4], "cmf": [0.3, -0.2],
        "ud_vol_ratio": [2.5, 0.5], "vol_trend": [0.8, -0.3], "eff_ratio": [0.05, 0.9]},
        index=["accum", "distrib"])
    s = accumulation_score(df)
    assert s["accum"] > s["distrib"]                          # rising OBV/CMF/up-vol + pinned price


# ── watchlists.py (fundamentally-strong & being-accumulated lists) ────────────
def test_clean_key_normalises_symbols():
    from watchlists import clean_key
    assert clean_key("AAPL.NS") == "AAPL" and clean_key("brk-b") == "BRK-B"
    assert clean_key("7203.T") == "7203"


def test_strong_from_scores_filters_and_flags():
    from watchlists import strong_from_scores
    df = pd.DataFrame({"ticker": ["A", "B", "C"], "market": ["US"] * 3,
                       "quality_score": [90.0, 50.0, 72.0],
                       "roe": [25, 5, 18], "de": [0.5, 2.0, 0.8], "sector": ["x", "y", "z"]})
    out = strong_from_scores(df, min_quality=60)
    assert list(out["ticker"]) == ["A", "C"]                  # B (50) below floor, sorted desc
    assert bool(out.set_index("ticker").loc["A", "strong_profile"]) is True   # roe>15 & de<1
    assert bool(out.set_index("ticker").loc["C", "strong_profile"]) is True


def test_accumulated_from_scan_keeps_in_box_coils():
    from watchlists import accumulated_from_scan
    scan = pd.DataFrame({"ticker": ["P", "Q", "R"],
                         "state": ["in_box", "breakout", "in_box"],
                         "accumulation": [3.0, 5.0, 0.2]})
    out = accumulated_from_scan(scan, min_accum=0.5)
    assert list(out["ticker"]) == ["P"]                       # Q not in_box, R below floor


def test_intersect_strong_and_accumulated():
    from watchlists import strong_from_scores, accumulated_from_scan, intersect
    strong = strong_from_scores(pd.DataFrame({
        "ticker": ["AAA", "BBB"], "market": ["US", "US"], "quality_score": [88.0, 70.0],
        "roe": [20, 20], "de": [0.5, 0.5], "sector": ["t", "t"]}))
    accum = accumulated_from_scan(pd.DataFrame({
        "ticker": ["BBB", "CCC"], "state": ["in_box", "in_box"], "accumulation": [2.0, 3.0],
        "position": [0.9, 0.5], "cmf": [0.2, 0.1]}))
    both = intersect(strong, accum)
    assert list(both["key"]) == ["BBB"]                       # only BBB is in both lists


# ── accumulation_screener.py (CMF/accumulation screen + validation) ───────────
def test_accumulation_signal_sign():
    from accumulation_screener import accumulation_signal
    n = 12
    # accumulation: closes near the high, price + volume rising, up-days
    high = np.arange(10, 10 + n, dtype=float) + 0.5
    low = np.arange(10, 10 + n, dtype=float) - 0.5
    close = np.arange(10, 10 + n, dtype=float) + 0.4          # near the high each day
    volume = np.arange(100, 100 + n * 10, 10, dtype=float)
    acc = accumulation_signal(high, low, close, volume)
    assert acc["cmf"] > 0.3 and acc["accum"] > 0             # net accumulation
    # distribution: same valid band but closes near the LOW each day
    close_d = np.arange(10, 10 + n, dtype=float) - 0.4       # near the low (still within band)
    dist = accumulation_signal(high, low, close_d, volume)
    assert dist["cmf"] < -0.3                                 # net distribution


def test_information_coefficient():
    from accumulation_screener import information_coefficient
    sig = np.arange(1, 21, dtype=float)
    assert information_coefficient(sig, 2 * sig + 1) == pytest.approx(1.0)   # perfect +
    assert information_coefficient(sig, -sig) == pytest.approx(-1.0)         # perfect −


def test_quantile_returns_monotone():
    from accumulation_screener import quantile_returns, monotonicity
    n = 500
    sig = np.linspace(0, 1, n)
    panel = pd.DataFrame({"accum": sig, "fwd_ret": 0.1 * sig})   # return rises with signal
    curve = quantile_returns(panel, "accum", q=5)
    assert list(curve["median_fwd%"]) == sorted(curve["median_fwd%"])       # Q1<...<Q5
    assert monotonicity(curve) == pytest.approx(1.0)


# ── dashboard.py (accumulation section on the daily dashboard) ────────────────
def test_dashboard_render_includes_accumulation_section():
    import dashboard
    accum = pd.DataFrame({"market": ["US"], "ticker": ["MEC"], "close": [37.0],
                          "cmf": [0.39], "accum": [3.10]})
    html = dashboard.render_html({"Market coverage": pd.DataFrame({"market": ["US"], "n": [1]}),
                                  "Accumulation / CMF screen": accum})
    assert "<h2>Accumulation / CMF screen</h2>" in html          # section rendered
    assert "MEC" in html and "3.1" in html                       # its rows are in the table
    # empty section renders a placeholder, not a crash
    assert "no data" in dashboard.render_html({"Empty": pd.DataFrame()})


# ── corporate_actions.py (split & rights-issue screeners) ─────────────────────
def test_nearest_split_ratio_and_label():
    from corporate_actions import nearest_split_ratio, label_ratio
    assert nearest_split_ratio(0.5)[0] == "2:1"               # forward 2:1
    assert nearest_split_ratio(1 / 3)[0] == "3:1"
    assert nearest_split_ratio(2.0)[0] == "1:2"               # reverse
    assert nearest_split_ratio(0.91) is None                  # ordinary move, no split
    assert label_ratio(10.0) == "10:1" and label_ratio(0.5) == "1:2"


def test_detect_splits_flags_clean_split_not_glitch():
    from corporate_actions import detect_splits
    flat = [100.0] * 21
    split = detect_splits(flat + [50, 50, 50, 50], [100] * 21 + [300, 100, 100, 100])
    assert len(split) == 1 and split[0]["ratio"] == "2:1"     # clean 2:1 on volume, persists
    glitch = detect_splits(flat + [0.5, 0.5], [100] * 21 + [300, 100])   # drop to ~0
    assert glitch == []                                       # glitch guarded out
    normal = detect_splits(flat + [90, 90], [100] * 21 + [300, 100])     # −10%, not a ratio
    assert normal == []


def test_detect_rights_idiosyncratic_persistent_only():
    from corporate_actions import detect_rights
    flat = [100.0] * 21
    px = flat + [75, 75, 75, 75]                              # −25%, settles at diluted level
    vol = [100] * 21 + [300, 100, 100, 100]
    mkt_flat = [0.0] * 25
    assert len(detect_rights(px, vol, mkt_flat)) == 1         # idiosyncratic + stabilises -> flagged
    mkt_down = [0.0] * 21 + [-0.25, 0, 0, 0]                  # whole market fell too
    assert detect_rights(px, vol, mkt_down) == []             # not idiosyncratic
    crash = flat + [75, 55, 40, 30]                           # keeps cratering
    assert detect_rights(crash, vol, mkt_flat) == []          # fails stabilisation


def test_parse_edgar_hits_extracts_ticker_and_form():
    from corporate_actions import parse_edgar_hits
    payload = {"hits": {"hits": [
        {"_source": {"display_names": ["STURM RUGER & CO INC  (RGR)  (CIK 0000095029)"],
                     "file_date": "2026-03-04", "root_forms": ["8-K"]}},
        {"_source": {"display_names": ["Dole plc  (DOLE, DOLE2)  (CIK 0001857518)"],
                     "file_date": "2026-04-07", "form_type": "8-K"}}]}}
    rows = parse_edgar_hits(payload, "rights issue")
    assert rows[0]["ticker"] == "RGR" and rows[0]["company"] == "STURM RUGER & CO INC"
    assert rows[0]["date"] == "2026-03-04" and rows[0]["form"] == "8-K"
    assert rows[0]["event"] == "rights issue" and rows[1]["ticker"] == "DOLE"


# ── scout frontier gaps: seasonality / network / crowding / nlp / options / esg ──
def test_seasonality_day_of_week_and_turn_of_month():
    import seasonality as se
    idx = pd.bdate_range("2024-01-01", periods=260)
    r = pd.Series(0.001, index=idx)
    r[r.index.dayofweek == 0] = 0.01                          # Mondays strong
    dow = se.day_of_week(r)
    assert dow.loc[dow["weekday"] == "Mon", "mean%"].iloc[0] > dow.loc[dow["weekday"] == "Fri", "mean%"].iloc[0]
    tom = se.turn_of_month(r)
    assert "edge%" in tom and tom["tom_n"] > 0


def test_peer_network_basket_return_and_signal():
    from peer_network import basket_return, peer_signal
    mp = pd.DataFrame({"A": [10.0, 11.0, 12.0], "B": [20.0, 22.0, 24.0]})   # both +20% over [0,2]
    assert basket_return(mp, 0, 2) == pytest.approx(0.2)
    assert peer_signal(0.2) == pytest.approx(0.2) and np.isnan(peer_signal(np.nan))


def test_crowding_score_rewards_correlation_and_run():
    from crowding import crowding_score, corr_to_market
    df = pd.DataFrame({"corr_mkt": [0.9, 0.1, 0.5], "rel_strength": [0.5, -0.1, 0.2]},
                      index=["crowded", "lonely", "mid"])
    s = crowding_score(df)
    assert s["crowded"] == s.max() and s["lonely"] == s.min()
    a = pd.Series([0.01, -0.02, 0.03, 0.01, -0.01] * 5)
    assert corr_to_market(a, a) == pytest.approx(1.0)         # perfectly co-moving


def test_news_sentiment_lexicon_scoring():
    from news_sentiment import score_text, score_headlines
    assert score_text("Company beats profit, shares surge to record")["sentiment"] > 0
    assert score_text("Fraud probe, bankruptcy warning, shares plunge")["sentiment"] < 0
    assert score_text("the company issued a statement")["sentiment"] == 0.0
    agg = score_headlines(["strong growth and record profit", "lawsuit and loss"])
    assert agg["n_headlines"] == 2 and -1 <= agg["sentiment"] <= 1


def test_options_iv_aggregators():
    from options_iv import atm_iv, put_call_ratio, iv_skew
    calls = pd.DataFrame({"strike": [90, 100, 110], "impliedVolatility": [0.30, 0.25, 0.22],
                          "openInterest": [100, 200, 150]})
    puts = pd.DataFrame({"strike": [90, 100, 110], "impliedVolatility": [0.35, 0.26, 0.24],
                         "openInterest": [300, 200, 100]})
    assert atm_iv(calls, puts, 100) == pytest.approx((0.25 + 0.26) / 2)
    assert put_call_ratio(calls, puts) == pytest.approx(600 / 450)   # defensive
    assert iv_skew(calls, puts, 100, moneyness=0.10) == pytest.approx(0.35 - 0.22)  # put fear


def test_esg_normalisation():
    from esg_screen import esg_grade, esg_score_0_100, rank_esg
    assert esg_grade(8) == "negligible" and esg_grade(35) == "high" and esg_grade(45) == "severe"
    assert esg_score_0_100(10) == pytest.approx(80.0)         # 100 − 2×10
    df = rank_esg(pd.DataFrame({"ticker": ["LOW", "HIGH"], "total_esg": [12.0, 38.0]}))
    assert list(df["ticker"]) == ["LOW", "HIGH"]              # lowest risk first


# ── earnings_liquidity.py (liquidity/volume/price × PEAD study) ────────────────
def test_directional_drift_sign():
    from earnings_liquidity import directional_drift
    assert directional_drift(0.05, 0.03) == pytest.approx(0.03)   # positive surprise, up drift
    assert directional_drift(-0.05, -0.02) == pytest.approx(0.02)  # negative surprise, down drift = positive PEAD
    assert directional_drift(-0.05, 0.02) == pytest.approx(-0.02)  # reversal against surprise
    assert np.isnan(directional_drift(0.0, 0.03))                  # no surprise -> undefined


def test_bucket_stats_and_spread():
    from earnings_liquidity import bucket_stats, spread_qhigh_qlow
    rng = np.random.default_rng(9)
    n = 400
    by = np.linspace(0, 1, n)
    val = 0.05 * by + rng.normal(0, 0.005, n)                      # value rises with the sort key
    t = bucket_stats(pd.DataFrame({"illiq": by, "dir_drift": val}), "illiq", "dir_drift", q=5)
    assert list(t["dir_drift_med%"]) == sorted(t["dir_drift_med%"])   # monotone increasing
    assert spread_qhigh_qlow(t, "dir_drift_med%") > 0                # Q5 > Q1


def test_per_market_summary_ranks_by_illiq_ic():
    from earnings_liquidity import per_market_summary
    rng = np.random.default_rng(4)
    n = 80
    # market A: drift rises with illiquidity (positive IC); market B: no relation
    illiq = np.linspace(0, 1, n)
    A = pd.DataFrame({"illiq": illiq, "dollar_vol": rng.random(n), "price": rng.random(n),
                      "vol_surge": rng.uniform(2, 5, n), "dir_drift": 0.1 * illiq + rng.normal(0, 0.01, n)})
    B = pd.DataFrame({"illiq": illiq, "dollar_vol": rng.random(n), "price": rng.random(n),
                      "vol_surge": rng.uniform(2, 5, n), "dir_drift": rng.normal(0, 0.05, n)})
    summ = per_market_summary({"A": A, "B": B}, min_events=40)
    assert list(summ["market"]) == ["A", "B"]                        # A (positive IC) ranks first
    assert summ.iloc[0]["illiq_IC"] > summ.iloc[1]["illiq_IC"]


def test_parse_submissions_filters_earnings_forms():
    from earnings_liquidity import parse_submissions
    payload = {"filings": {"recent": {
        "form": ["10-Q", "8-K", "10-K", "4", "10-Q"],
        "filingDate": ["2026-05-01", "2026-04-15", "2025-10-31", "2025-10-20", "2025-08-01"]}}}
    dates = parse_submissions(payload)                               # 10-Q/10-K only
    assert dates == ["2025-08-01", "2025-10-31", "2026-05-01"]       # sorted, 8-K & Form-4 dropped


# ── marketdata.py (shared reusable blocks) ────────────────────────────────────
def test_marketdata_clean_key_and_zscore():
    import marketdata as md
    assert md.clean_key("AAPL.NS") == "AAPL" and md.clean_key("brk-b") == "BRK-B"
    z = md.zscore(pd.Series([1.0, 2.0, 3.0]))
    assert z.mean() == pytest.approx(0.0, abs=1e-9) and z.iloc[0] < 0 < z.iloc[-1]
    zi = md.zscore(pd.Series([np.inf, 1.0, 2.0, 3.0]))
    assert pd.isna(zi.iloc[0]) and (zi.dropna().abs() < 10).all()             # inf -> NaN, no blow-up


def test_marketdata_liquid_symbols_filter():
    import marketdata as md
    idx = pd.date_range("2024-01-01", periods=300, freq="B")
    close = pd.DataFrame({"BIG": 100.0, "SMALL": 5.0}, index=idx)
    vol = pd.DataFrame({"BIG": 1e6, "SMALL": 1e2}, index=idx)                  # BIG far more $-volume
    liq = md.liquid_symbols(close, vol, quantile=0.5)
    assert "BIG" in liq and "SMALL" not in liq


def test_marketdata_ic_and_monotonicity():
    import marketdata as md
    x = np.arange(1, 21, dtype=float)
    assert md.information_coefficient(x, 2 * x) == pytest.approx(1.0)
    curve = pd.DataFrame({"v": [1.0, 2.0, 3.0, 4.0, 5.0]})
    assert md.monotonicity(curve, "v") == pytest.approx(1.0)
    assert md.trend_corr([1, 2, 3, 4, 5]) == pytest.approx(1.0)


def test_marketdata_delegation_preserves_module_helpers():
    # the refactored modules delegate to marketdata but keep their public names/behaviour
    import liquidity_factor as lf, pead_factor as pf, watchlists as wl, marketdata as md
    assert wl.clean_key("XYZ.BO") == md.clean_key("XYZ.BO") == "XYZ"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
