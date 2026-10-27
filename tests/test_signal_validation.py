"""
Tests for signal_validation.py — synthetic panels with a planted answer, so
each statistic is checked against something known rather than re-derived.
Run: pytest -q tests/test_signal_validation.py
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import signal_validation as sv  # noqa: E402


def _panel(T=200, N=150, ic=0.1, seed=0):
    """Signal and forward return with a planted cross-sectional correlation."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-03", periods=T, freq="W-FRI")
    cols = [f"S{i}" for i in range(N)]
    s = rng.standard_normal((T, N))
    f = ic * s + np.sqrt(1 - ic ** 2) * rng.standard_normal((T, N))
    return pd.DataFrame(s, idx, cols), pd.DataFrame(f, idx, cols)


# ── IC ───────────────────────────────────────────────────────────────────────
def test_rank_ic_recovers_planted_correlation():
    s, f = _panel(ic=0.15)
    ic = sv.rank_ic_series(s, f)
    assert abs(ic.mean() - 0.15) < 0.02


def test_rank_ic_ignores_names_missing_return():
    s, f = _panel(T=5, N=100, ic=0.0)
    f2 = f.copy()
    f2.iloc[:, :50] = np.nan                     # half the names have no return
    ic_full = sv.rank_ic_series(s.iloc[:, 50:], f.iloc[:, 50:])
    ic_masked = sv.rank_ic_series(s, f2)
    np.testing.assert_allclose(ic_full.values, ic_masked.values)


def test_rank_ic_min_names_gives_nan():
    s, f = _panel(T=3, N=20)
    assert sv.rank_ic_series(s, f, min_names=30).isna().all()


# ── Newey-West ───────────────────────────────────────────────────────────────
def test_newey_west_matches_plain_t_without_autocorrelation():
    x = np.random.default_rng(1).standard_normal(2000) + 0.05
    mu, t_nw, _ = sv.newey_west_t(x, lags=0)
    t_plain = x.mean() / (x.std(ddof=0) / np.sqrt(len(x)))
    assert abs(t_nw - t_plain) < 1e-9


def test_newey_west_deflates_overlapping_series():
    # moving sum of iid noise = heavily autocorrelated; HAC t must shrink
    e = np.random.default_rng(2).standard_normal(3000) + 0.02
    x = pd.Series(e).rolling(10).sum().dropna().to_numpy()
    _, t0, _ = sv.newey_west_t(x, lags=0)
    _, t10, _ = sv.newey_west_t(x, lags=10)
    assert abs(t10) < abs(t0) / 2


# ── multiple testing ─────────────────────────────────────────────────────────
def test_bh_fdr_known_values():
    q = sv.bh_fdr([0.01, 0.04, 0.03, 0.5])
    np.testing.assert_allclose(q, [0.04, 0.0533333, 0.0533333, 0.5], rtol=1e-5)


def test_bh_fdr_preserves_nan():
    q = sv.bh_fdr([0.01, np.nan, 0.02])
    assert np.isnan(q[1]) and q[0] <= q[2]


def test_deflated_sharpe_penalises_many_trials():
    r = np.random.default_rng(3).standard_normal(500) * 0.01 + 0.002
    _, dsr_few = sv.deflated_sharpe(r, n_trials=2, sr_trials_var=0.01)
    _, dsr_many = sv.deflated_sharpe(r, n_trials=1000, sr_trials_var=0.01)
    assert dsr_many < dsr_few


def test_deflated_sharpe_zero_skill_is_not_credible():
    r = np.random.default_rng(4).standard_normal(500) * 0.01
    _, dsr = sv.deflated_sharpe(r, n_trials=200, sr_trials_var=0.005)
    assert dsr < 0.95


# ── neutralisation ───────────────────────────────────────────────────────────
def test_neutralize_removes_planted_exposure():
    rng = np.random.default_rng(5)
    idx = pd.date_range("2021-01-01", periods=20, freq="W-FRI")
    cols = [f"S{i}" for i in range(200)]
    size = pd.DataFrame(rng.standard_normal((20, 200)), idx, cols)
    sig = size * 2 + 0.1 * rng.standard_normal((20, 200))       # pure size bet
    res = sv.neutralize(sig, [size])
    corr = [np.corrcoef(res.loc[d], size.loc[d].rank())[0, 1] for d in idx]
    assert max(abs(c) for c in corr) < 0.05


# ── Fama-MacBeth ─────────────────────────────────────────────────────────────
def test_fama_macbeth_flags_redundant_copy():
    s, f = _panel(T=150, N=200, ic=0.2, seed=6)
    noise = pd.DataFrame(np.random.default_rng(7).standard_normal(s.shape), s.index, s.columns)
    out = sv.fama_macbeth({"real": s, "junk": noise}, f, lags=1).set_index("signal")
    assert out.loc["real", "fm_t"] > 5
    assert abs(out.loc["junk", "fm_t"]) < 3


# ── costs ────────────────────────────────────────────────────────────────────
def test_static_signal_has_zero_turnover():
    rng = np.random.default_rng(8)
    idx = pd.date_range("2022-01-07", periods=30, freq="W-FRI")
    cols = [f"S{i}" for i in range(100)]
    base = rng.standard_normal(100)
    sig = pd.DataFrame(np.tile(base, (30, 1)), idx, cols)
    fwd = pd.DataFrame(rng.standard_normal((30, 100)) * 0.01, idx, cols)
    q = sv.quantile_spread_turnover(sig, fwd)
    assert q["turnover_ls"] == 0.0


def test_random_signal_turnover_near_four_fifths():
    s, f = _panel(T=100, N=500, ic=0.0, seed=9)
    q = sv.quantile_spread_turnover(s, f, q=0.2)
    assert 0.75 < q["turnover_ls"] < 0.85      # P(stay in top 20%) = 0.2


def test_breakeven_is_spread_over_two_tau():
    s, f = _panel(T=120, N=300, ic=0.2, seed=10)
    q = sv.quantile_spread_turnover(s, f)
    assert q["breakeven_rt_cost_ls"] == pytest.approx(q["ls_spread"] / (2 * q["turnover_ls"]))


# ── walk-forward ─────────────────────────────────────────────────────────────
def test_walk_forward_picks_real_signal_out_of_sample():
    s, f = _panel(T=260, N=150, ic=0.15, seed=11)
    noise = pd.DataFrame(np.random.default_rng(12).standard_normal(s.shape), s.index, s.columns)
    wf = sv.purged_walk_forward({"real": s, "noise": noise}, f, embargo=3, n_folds=3)
    assert (wf["best_single_name"] == "real").all()
    assert (wf["oos_ic_ridge"] > 0.08).all()


def test_walk_forward_handles_inverted_signal():
    s, f = _panel(T=260, N=150, ic=0.15, seed=13)
    wf = sv.purged_walk_forward({"inv": -s}, f, embargo=3, n_folds=3)
    assert (wf["oos_ic_best_single"] > 0.08).all()   # sign learned in training


# ── health ───────────────────────────────────────────────────────────────────
def test_ic_health_statuses():
    rng = np.random.default_rng(14)
    good = pd.Series(0.05 + 0.03 * rng.standard_normal(300))
    assert sv.ic_health(good)["status"] == "HEALTHY"
    flipped = good.copy()
    flipped.iloc[-52:] = -0.05 + 0.03 * rng.standard_normal(52)
    assert sv.ic_health(flipped)["status"] == "FLIPPED"
    assert sv.ic_health(pd.Series(0.03 * rng.standard_normal(300)))["status"] == "NOISE"


# ── overnight / intraday ─────────────────────────────────────────────────────
def test_overnight_intraday_legs_add_to_close_to_close():
    rng = np.random.default_rng(15)
    idx = pd.bdate_range("2021-01-04", periods=120)
    C = pd.DataFrame(100 * np.exp(np.cumsum(0.01 * rng.standard_normal((120, 4)), 0)), idx)
    O = C.shift(1) * np.exp(0.005 * rng.standard_normal((120, 4)))
    on, intra = sv.overnight_intraday_fwd(O, C, h=5)
    total = np.log(C.shift(-6) / C.shift(-1))
    diff = (on + intra - total).abs().max().max()
    assert diff < 1e-10
    assert on.iloc[-6:].isna().all().all()        # window runs past the data


def test_overnight_intraday_isolates_planted_gap_return():
    idx = pd.bdate_range("2021-01-04", periods=30)
    C = pd.DataFrame({"a": 100.0}, idx)
    O = C * 1.01                                   # every bar gaps up 1%, gives it back
    on, intra = sv.overnight_intraday_fwd(O, C, h=3)
    assert on["a"].dropna().iloc[0] == pytest.approx(3 * np.log(1.01))
    assert intra["a"].dropna().iloc[0] == pytest.approx(-3 * np.log(1.01))


# ── tail risk ────────────────────────────────────────────────────────────────
def test_tail_risk_known_series():
    r = np.r_[np.full(19, 0.01), -0.10]           # 20 periods, one crash
    tr = sv.tail_risk(r, alpha=0.05)
    assert tr["cvar"] == pytest.approx(-0.10)      # worst 5% = the one crash
    assert tr["worst"] == pytest.approx(-0.10)
    assert tr["max_dd"] == pytest.approx(-0.10)
    assert tr["skew"] < 0
    assert np.isnan(sv.tail_risk([0.01] * 5)["cvar"])


# ── PBO ──────────────────────────────────────────────────────────────────────
def test_pbo_near_half_for_pure_noise():
    rng = np.random.default_rng(16)
    perf = pd.DataFrame(0.02 * rng.standard_normal((400, 40)))
    res = sv.pbo_cscv(perf, n_blocks=10)
    assert res["n_splits"] == 252                  # C(10, 5)
    assert 0.25 < res["pbo"] < 0.75
    assert res["degradation_slope"] < 0.5


def test_pbo_near_zero_with_one_real_edge():
    rng = np.random.default_rng(17)
    perf = pd.DataFrame(0.02 * rng.standard_normal((400, 40)))
    perf[0] += 0.01                                # Sharpe 0.5/period
    res = sv.pbo_cscv(perf, n_blocks=10)
    assert res["pbo"] < 0.05
    assert res["oos_sr_of_is_best"] > 0.3


def test_quantile_spread_returns_formation_dates():
    s, f = _panel(T=60, N=200, ic=0.1, seed=18)
    q = sv.quantile_spread_turnover(s, f)
    assert len(q["dates"]) == len(q["ls_series"]) == 60
