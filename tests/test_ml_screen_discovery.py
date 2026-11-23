"""ml_screen_discovery.py: feature matrix, screen rules, deviation/reward, CEM refine, main() (offline)."""
import json
import os
import sqlite3
import sys
import types

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ml_screen_discovery as ms  # noqa: E402
from conftest_helpers import make_ohlcv  # noqa: E402


def test_features_require_260_rows_and_none():
    assert ms._features_asof(None) is None
    assert ms._features_asof(make_ohlcv(259)) is None
    f = ms._features_asof(make_ohlcv(260, seed=1))
    assert f is not None and set(ms.FEATURES) <= set(f) and "_fwd" in f


def test_features_values_match_definitions():
    df = make_ohlcv(400, seed=2)
    f = ms._features_asof(df)
    c = df["Close"]
    assert f["ret_20d"] == pytest.approx((c.iloc[-1] / c.iloc[-21] - 1) * 100)
    assert f["ret_60d"] == pytest.approx((c.iloc[-1] / c.iloc[-61] - 1) * 100)
    assert f["_fwd"] == pytest.approx((c.iloc[-1] / c.iloc[-1 - ms.FWD] - 1) * 100)
    assert f["dist_52w_high"] == pytest.approx((c.iloc[-1] / c.iloc[-252:].max() - 1) * 100)
    assert f["dist_200dma"] == pytest.approx((c.iloc[-1] / c.iloc[-200:].mean() - 1) * 100)
    assert f["dollar_vol_log"] == pytest.approx(np.log10(c.iloc[-1] * df["Volume"].iloc[-1]))
    assert 0 <= f["rsi_14"] <= 100 and f["dist_52w_high"] <= 0


def test_features_zero_volume_ratio_and_floor():
    df = make_ohlcv(300, seed=3, volume=0.0)
    f = ms._features_asof(df)
    assert f["vol_ratio"] == 1.0 and f["dollar_vol_log"] == 0.0     # log10(max(0,1)) = 0


def test_features_nan_returns_none():
    df = make_ohlcv(300, seed=3)
    df.iloc[-1, df.columns.get_loc("Close")] = np.nan
    assert ms._features_asof(df) is None


def test_build_matrix_drops_invalid():
    X = ms.build_matrix({"A": make_ohlcv(300, seed=1), "B": make_ohlcv(100), "C": None})
    assert list(X.index) == ["A"] and set(ms.FEATURES) <= set(X.columns)


def _X(n=60, seed=0):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(rng.standard_normal((n, len(ms.FEATURES))), columns=ms.FEATURES)
    X["_fwd"] = X["ret_20d"] * 2 + rng.standard_normal(n) * 0.1
    return X


def test_rule_from_cluster_uses_percentiles():
    X = pd.DataFrame({f: np.arange(101, dtype=float) for f in ms.FEATURES})
    X["_fwd"] = 0.0
    mask = np.ones(101, dtype=bool)
    r = ms.rule_from_cluster(X, mask)
    assert set(r) == set(ms.FEATURES) and r["ret_20d"] == (20.0, 80.0)
    r2 = ms.rule_from_cluster(X, mask, q=(10, 90))
    assert r2["rsi_14"] == (10.0, 90.0)
    half = np.arange(101) >= 50
    assert ms.rule_from_cluster(X, half)["ret_20d"] == (60.0, 90.0)


def test_apply_rule_inclusive_bounds_and_all_features():
    X = pd.DataFrame({f: [0.0, 1.0, 2.0, 3.0] for f in ms.FEATURES})
    rule = {f: (1.0, 2.0) for f in ms.FEATURES}
    assert list(ms.apply_rule(X, rule)) == [False, True, True, False]
    rule["rsi_14"] = (2.0, 3.0)
    assert list(ms.apply_rule(X, rule)) == [False, False, True, False]
    assert ms.apply_rule(X, {}).all()


def test_deviation_jaccard():
    a = np.array([True, True, False, False]); b = np.array([False, True, True, False])
    assert ms.deviation(a, a) == 0.0
    assert ms.deviation(a, b) == pytest.approx(1 - 1 / 3)
    assert ms.deviation(a, ~a) == 1.0
    z = np.zeros(4, dtype=bool)
    assert ms.deviation(z, z) == 0.0
    assert ms.deviation(a, z) == 1.0


def test_reward_penalises_small_picks_and_deviation():
    X = _X()
    known = np.zeros(len(X), dtype=bool)
    tiny = {f: (100.0, 101.0) for f in ms.FEATURES}                # selects nothing
    r, m = ms.reward(X, tiny, known)
    assert r == -9.9 and m.sum() == 0
    top = {f: (-99.0, 99.0) for f in ms.FEATURES}
    top["ret_20d"] = (X["ret_20d"].quantile(0.7), 99.0)
    known = ms.apply_rule(X, top)
    r_same, m = ms.reward(X, top, known)
    exp_edge = X.loc[m, "_fwd"].mean() - X["_fwd"].mean()
    assert r_same == pytest.approx(exp_edge)                       # zero deviation
    r_far, _ = ms.reward(X, top, ~known, lam=1.5)
    assert r_far == pytest.approx(exp_edge - 1.5 * 1.0)
    r_lam, _ = ms.reward(X, top, ~known, lam=0.0)
    assert r_lam == pytest.approx(exp_edge)
    # exactly 3 picks is allowed, 2 is not
    X3 = X.copy()
    sel = {f: (-99.0, 99.0) for f in ms.FEATURES}
    sel["ret_20d"] = (X3["ret_20d"].nlargest(3).min(), 99.0)
    assert ms.reward(X3, sel, known)[1].sum() == 3 and ms.reward(X3, sel, known)[0] != -9.9
    sel["ret_20d"] = (X3["ret_20d"].nlargest(2).min(), 99.0)
    assert ms.reward(X3, sel, known)[0] == -9.9


def test_rl_refine_never_worse_and_valid(monkeypatch):
    np.random.seed(0)
    X = _X(80, seed=1)
    base = {f: (-1.0, 1.0) for f in ms.FEATURES}
    known = ms.apply_rule(X, {**{f: (-99.0, 99.0) for f in ms.FEATURES}, "ret_20d": (0.5, 99.0)})
    r0, _ = ms.reward(X, base, known)
    best_rule, best_r = ms.rl_refine(X, base, known, iters=8, pop=30)
    assert best_r >= r0
    assert set(best_rule) == set(ms.FEATURES) and all(lo <= hi for lo, hi in best_rule.values())
    assert ms.reward(X, best_rule, known)[0] == pytest.approx(best_r)


def test_rl_refine_keeps_original_when_nothing_beats(monkeypatch):
    X = _X(40)
    rule = {f: (-0.1, 0.1) for f in ms.FEATURES}
    monkeypatch.setattr(ms, "reward", lambda X_, r, k, lam=1.5: (5.0 if r is rule else -1.0, None))
    best_rule, best_r = ms.rl_refine(X, rule, np.zeros(len(X), bool), iters=3, pop=10)
    assert best_rule is rule and best_r == 5.0


def test_load_universe(monkeypatch):
    class XL:
        def __init__(self, p):
            XL.path = p

        def parse(self, s):
            return pd.DataFrame({"Symbol": ["A", "B", "nan"], "Suffix": [".NS", ".NS", ""],
                                 "YF_Ticker": ["7203.T", "nan", "x"]})
    monkeypatch.setattr(ms.pd, "ExcelFile", XL)
    monkeypatch.setattr(ms.glob, "glob", lambda p, recursive=False: ["/z.xlsx", "/y.xlsx"])
    assert ms.load_universe("US", None) == ["A", "B"]
    assert XL.path == "/z.xlsx"
    assert ms.load_universe("US", 1) == ["A"]
    assert ms.load_universe("India", None) == ["A.NS", "B.NS"]
    assert ms.load_universe("Japan", None) == ["7203.T", "x"]


def _universe(n=70, seed=0):
    ohlc = {}
    for i in range(n):
        ohlc[f"T{i:02d}"] = make_ohlcv(300, seed=seed + i, drift=(i % 7 - 3) * 0.0008, vol=0.01 + (i % 5) * 0.004,
                                       volume=1e6)
    return ohlc


def _install(monkeypatch, ohlc, runs_today=True):
    store = types.ModuleType("market_store")
    store.cached_download = lambda tickers, years: ohlc
    hol = types.ModuleType("market_holidays")
    hol.should_run_today = lambda m: runs_today
    monkeypatch.setitem(sys.modules, "market_store", store)
    monkeypatch.setitem(sys.modules, "market_holidays", hol)
    monkeypatch.setattr(ms, "load_universe", lambda m, l: list(ohlc))


def test_main_too_few_stocks(monkeypatch, tmp_path, capsys):
    _install(monkeypatch, _universe(10))
    db = tmp_path / "r.db"
    monkeypatch.setattr(sys, "argv", ["ml_screen_discovery.py", "--db", str(db)])
    ms.main()
    assert "too few stocks after filtering" in capsys.readouterr().err
    assert not db.exists()


def test_main_filters_illiquid_before_matrix(monkeypatch, tmp_path, capsys):
    uni = _universe(10)
    uni["ILLIQ"] = make_ohlcv(300, volume=1.0)
    _install(monkeypatch, uni)
    seen = {}
    real = ms.build_matrix
    monkeypatch.setattr(ms, "build_matrix", lambda o: seen.setdefault("n", set(o)) and real(o))
    monkeypatch.setattr(sys, "argv", ["ml_screen_discovery.py", "--db", str(tmp_path / "r.db")])
    ms.main()
    assert "ILLIQ" not in seen["n"] and len(seen["n"]) == 10


def test_main_end_to_end_writes_recos(monkeypatch, tmp_path, capsys):
    np.random.seed(0)
    _install(monkeypatch, _universe(80))
    db = str(tmp_path / "r.db")
    monkeypatch.setattr(sys, "argv", ["ml_screen_discovery.py", "--market", "US", "--db", db, "--dev-threshold", "0.0"])
    monkeypatch.setattr(ms, "rl_refine", lambda X, rule, known, **k: (rule, 0.5))
    ms.main()
    con = sqlite3.connect(db)
    rows = con.execute("SELECT market, n_picks, rl_refined, rule, screen_edge FROM screen_reco").fetchall()
    con.close()
    assert rows, "expected at least one outperforming cluster"
    assert all(r[0] == "US" and r[2] == 1 for r in rows)          # threshold 0 -> every cluster RL-refined
    assert set(json.loads(rows[0][3])) == set(ms.FEATURES)
    edges = [r[4] for r in rows]
    assert edges == sorted(edges, reverse=True)
    cap = capsys.readouterr()
    assert "market open today? True" in cap.err and "known-good universe" in cap.err
    assert "NEW SCREEN RECOMMENDATIONS — US" in cap.out and "[RL-refined]" in cap.out
    assert f"{len(rows)} outperforming clusters" in cap.out


def test_main_no_rl_when_deviation_below_threshold(monkeypatch, tmp_path):
    np.random.seed(0)
    _install(monkeypatch, _universe(80))
    called = []
    monkeypatch.setattr(ms, "rl_refine", lambda *a, **k: called.append(1) or (a[1], 0.0))
    db = str(tmp_path / "r.db")
    monkeypatch.setattr(sys, "argv", ["ml_screen_discovery.py", "--db", db, "--dev-threshold", "1.5"])
    ms.main()
    assert called == []
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM screen_reco WHERE rl_refined=1").fetchone()[0] == 0
    con.close()
