"""portfolio.py + meta_screen.py: optimisers, caps, turnover and conviction fusion (offline)."""
import json
import os
import sqlite3
import sys
import types

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import meta_screen as ms  # noqa: E402
import portfolio as pt  # noqa: E402


# ───────────────────────────── portfolio ─────────────────────────────
def test_min_variance_weights():
    cov = np.diag([1.0, 4.0])
    w = pt.min_variance_weights(cov)
    assert w == pytest.approx([0.8, 0.2]) and w.sum() == pytest.approx(1.0)
    assert pt.min_variance_weights(np.eye(3)) == pytest.approx([1 / 3] * 3)


def test_max_sharpe_weights():
    cov = np.eye(2)
    assert pt.max_sharpe_weights(np.array([0.3, 0.1]), cov) == pytest.approx([0.75, 0.25])
    assert pt.max_sharpe_weights(np.array([0.1, -0.1]), cov) == pytest.approx([0.5, 0.5])   # zero-sum -> equal weight


def test_long_only():
    assert pt.long_only(np.array([0.5, -0.2, 0.7])) == pytest.approx([0.5 / 1.2, 0.0, 0.7 / 1.2])
    assert pt.long_only(np.array([-1.0, 0.0, -2.0])) == pytest.approx([1 / 3] * 3)


def test_cap_weights():
    w = pt.cap_weights(np.array([0.7, 0.2, 0.1]), 0.4)
    assert w.max() <= 0.4 + 1e-9 and w.sum() == pytest.approx(1.0) and w[0] == pytest.approx(0.4)
    assert w[1] > 0.2 and w[2] > 0.1                                       # excess spilled to the uncapped names
    assert pt.cap_weights(np.array([0.6, 0.4]), 0.3) == pytest.approx([0.5, 0.5])     # infeasible (n*cap<1) -> equal
    ok = np.array([0.3, 0.3, 0.4])
    assert pt.cap_weights(ok, 0.5) == pytest.approx(ok)
    allcap = pt.cap_weights(np.array([0.5, 0.5, 0.0]), 0.5)
    assert allcap.sum() == pytest.approx(1.0) and allcap.max() <= 0.5 + 1e-9


def test_cap_weights_no_free_names():
    w = pt.cap_weights(np.array([1.0, 0.0, 0.0]), 1 / 3)
    assert w == pytest.approx([1 / 3] * 3)


def test_apply_sector_cap():
    w = np.array([0.4, 0.3, 0.2, 0.1])
    out = pt.apply_sector_cap(w, ["A", "A", "B", "C"], 0.5)
    assert out.sum() == pytest.approx(1.0) and out[:2].sum() == pytest.approx(0.5, abs=1e-6)
    assert out[2] > 0.2 and out[3] > 0.1
    assert pt.apply_sector_cap(w, ["A", "A", "B", "B"], 0.4) == pytest.approx(w / w.sum())     # infeasible: 2*0.4<1
    same = pt.apply_sector_cap(w, ["A", "B", "C", "D"], 0.9)
    assert same == pytest.approx(w)
    single = pt.apply_sector_cap(np.array([0.5, 0.5]), ["A", "A"], 1.0)
    assert single == pytest.approx([0.5, 0.5])


def test_turnover_and_blend():
    a = np.array([0.5, 0.5, 0.0]); b = np.array([0.0, 0.5, 0.5])
    assert pt.turnover(a, b) == 0.5 and pt.turnover(a, a) == 0.0
    assert pt.blend_to_turnover(a, b, 0.5) is a                           # within budget -> unchanged (<=)
    w = pt.blend_to_turnover(a, b, 0.25)
    assert pt.turnover(w, b) == pytest.approx(0.25) and w.sum() == pytest.approx(1.0)
    assert pt.blend_to_turnover(a, a, 0.0) is a                           # zero turnover short-circuits


def _rets(n=300, k=4, seed=0):
    rng = np.random.default_rng(seed)
    cols = [f"T{i}" for i in range(k)]
    sd = np.array([0.01, 0.02, 0.015, 0.03][:k])
    return pd.DataFrame(rng.normal(0.0005, 1, (n, k)) * sd, columns=cols)


def test_build_weights_min_var_and_max_sharpe():
    r = _rets()
    w = pt.build_weights(r)
    assert w.sum() == pytest.approx(1.0) and (w >= 0).all() and w.is_monotonic_decreasing and w.idxmax() == "T0"
    ms_ = pt.build_weights(r, method="max_sharpe")
    assert ms_.sum() == pytest.approx(1.0) and not np.allclose(w.sort_index(), ms_.sort_index())


def test_build_weights_caps_and_sectors_and_turnover():
    r = _rets()
    capped = pt.build_weights(r, cap=0.3)
    assert capped.max() <= 0.3 + 1e-9
    sec = pt.build_weights(r, cap=0.4, sectors=["A", "A", "B", "B"], sector_cap=0.5)
    assert sec.max() <= 0.4 + 1e-6 and sec.sum() == pytest.approx(1.0)
    sec_nocap = pt.build_weights(r, sectors=["A", "A", "B", "B"], sector_cap=0.5)
    assert sec_nocap[["T0", "T1"]].sum() <= 0.5 + 1e-6
    prev = {"T0": 0.25, "T1": 0.25, "T2": 0.25, "T3": 0.25}
    base = pt.build_weights(r)
    blended = pt.build_weights(r, prev=prev, max_turnover=0.05)
    t = pt.turnover(blended.sort_index().values, np.full(4, 0.25))
    assert t == pytest.approx(0.05, abs=1e-6) and pt.turnover(base.sort_index().values, np.full(4, 0.25)) > 0.05
    no_prev_weight = pt.build_weights(r, prev={"X": 1.0}, max_turnover=0.01)               # prev has none of our names
    assert no_prev_weight.sum() == pytest.approx(1.0) and no_prev_weight.equals(base) or no_prev_weight.sum() == pytest.approx(1.0)


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="portfolio.py build_weights: the position cap is re-applied AFTER the sector-cap pass and its "
                          "spill-over can push a sector back above sector_cap (observed 51.3% vs a 50% cap)")
def test_build_weights_sector_cap_survives_position_cap():
    r = _rets()
    sec = pt.build_weights(r, cap=0.4, sectors=["A", "A", "B", "B"], sector_cap=0.5)
    assert sec[["T0", "T1"]].sum() <= 0.5 + 1e-6


def test_build_weights_drops_incomplete_and_requires_two():
    r = _rets()
    r.loc[0, "T3"] = np.nan
    assert "T3" not in pt.build_weights(r).index
    with pytest.raises(ValueError, match="need >=2 assets"):
        pt.build_weights(r[["T0"]])
    r2 = _rets(); r2.loc[0, ["T1", "T2", "T3"]] = np.nan
    with pytest.raises(ValueError):
        pt.build_weights(r2)


def _comp_db(path, rows):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE dvm_composite(market TEXT, ticker TEXT, sector TEXT, composite REAL, code TEXT, D REAL, V REAL, M REAL)")
    con.executemany("INSERT INTO dvm_composite VALUES (?,?,?,?,?,?,?,?)", rows)
    con.commit(); con.close()


def test_candidates_from_composite(tmp_path):
    db = str(tmp_path / "c.db")
    _comp_db(db, [("US", "A", "S1", 90, "GGG", 1, 1, 1), ("US", "B", "S2", 95, "GGG", 1, 1, 1), ("US", "C", "S1", 99, "GGB", 1, 1, 1),
                  ("JP", "D", "S1", 80, "GGG", 1, 1, 1), ("US", "E", "S3", 50, "GGG", 1, 1, 1)])
    c = pt.candidates_from_composite("US", 2, db)
    assert list(c["ticker"]) == ["B", "A"]                                  # GGG only, best composite first, limited


def test_returns_for(monkeypatch, tmp_path):
    monkeypatch.setattr(pt, "SEED", str(tmp_path))
    idx = pd.date_range("2023-01-01", periods=400, freq="B")
    px = pd.concat([pd.DataFrame({"Date": idx, "Symbol": s, "Close": 100.0 + np.arange(400) * k}) for s, k in [("A", 1), ("B", 2), ("Z", 3)]])
    monkeypatch.setattr(pt.pd, "read_parquet", lambda p: px)
    r = pt.returns_for("US", ["A", "B"])
    assert list(r.columns) == ["A", "B"] and len(r) == 252 and r.iloc[-1]["A"] == pytest.approx(1 / 498.0)


def test_portfolio_main(monkeypatch, tmp_path, capsys):
    db = str(tmp_path / "c.db")
    _comp_db(db, [("US", f"T{i}", "S1" if i < 2 else "S2", 90 - i, "GGG", 1, 1, 1) for i in range(4)])
    monkeypatch.setattr(pt, "HERE", str(tmp_path))
    monkeypatch.setattr(pt, "returns_for", lambda m, t: _rets().reindex(columns=t))
    fake_risk = types.ModuleType("risk")
    fake_risk.risk_report = lambda r: {"vol": 1.23}
    monkeypatch.setitem(sys.modules, "risk", fake_risk)
    prev = tmp_path / "prev.json"
    prev.write_text(json.dumps({"T0": 1.0}))
    monkeypatch.setattr(sys, "argv", ["pt", "--db", db, "--n", "4", "--cap", "0.5", "--sector-cap", "0.6",
                                      "--prev", str(prev), "--max-turnover", "0.2"])
    pt.main()
    out = capsys.readouterr().out
    saved = json.load(open(tmp_path / "portfolio_US_min_var.json"))
    assert set(saved) == {"T0", "T1", "T2", "T3"} and sum(saved.values()) == pytest.approx(1.0, abs=1e-4)
    assert "min_var portfolio — US, 4 names (cap 0.5)" in out and "turnover vs prev:" in out and "vol" in out
    monkeypatch.setattr(sys, "argv", ["pt", "--db", db, "--n", "4", "--method", "max_sharpe"])
    pt.main()
    assert "max_sharpe portfolio" in capsys.readouterr().out and (tmp_path / "portfolio_US_max_sharpe.json").exists()


def test_portfolio_main_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["pt", "--db", str(tmp_path / "none.db")])
    with pytest.raises(SystemExit, match="dvm_composite.py first"):
        pt.main()
    db = str(tmp_path / "c.db")
    _comp_db(db, [("US", "A", "S", 1, "BBB", 1, 1, 1)])
    monkeypatch.setattr(sys, "argv", ["pt", "--db", db, "--market", "US"])
    with pytest.raises(SystemExit, match="no GGG candidates for US"):
        pt.main()


# ───────────────────────────── meta_screen ─────────────────────────────
def test_fuse_weighted_mean_and_missing():
    w = {"a": 0.5, "b": 0.5}
    assert ms.fuse({"a": 80, "b": 60}, w) == 70.0
    assert ms.fuse({"a": 80, "b": None}, w) == 80.0                          # renormalised over present components
    assert ms.fuse({"a": 80, "b": float("nan")}, w) == 80.0
    assert ms.fuse({"a": None}, w) == 0.0 and ms.fuse({}, w) == 0.0
    assert ms.fuse({"a": 80, "zzz": 100}, w) == 80.0                         # unknown component ignored
    assert ms.fuse({"a": 80, "b": 20}, {"a": 0.0, "b": 0.5}) == 20.0         # zero-weight ignored
    assert ms.fuse({"a": 50}, w) == 50.0


def test_fuse_gates_and_clamp():
    w = {"a": 1.0}
    assert ms.fuse({"a": 50}, w, {"g1": True, "g2": False}) == 60.0
    assert ms.fuse({"a": 50}, w, {"g1": True, "g2": True}, gate_bonus=5) == 60.0
    assert ms.fuse({"a": 95}, w, {"g": True}) == 100.0                       # clamped at 100
    assert ms.fuse({"a": -20}, w) == 0.0                                     # clamped at 0
    assert ms.fuse({"durability": 50}) == 50.0                               # default weights
    assert ms.DEFAULT_WEIGHTS["durability"] == 0.20 and sum(ms.DEFAULT_WEIGHTS.values()) == pytest.approx(1.0)


def test_rank_sorts_and_counts_confirms():
    df = pd.DataFrame({"market": ["US"] * 3, "ticker": ["A", "B", "C"], "D": [80, 40, np.nan], "V": [70, 40, np.nan],
                       "M": [90, 40, np.nan], "ml_signal": [np.nan, 80.0, np.nan], "quality_score": [60.0, np.nan, np.nan],
                       "pead_score": [np.nan, np.nan, np.nan], "triple_hit": [True, False, False]})
    r = ms.rank(df)
    assert list(r["ticker"]) == ["A", "B", "C"] and r["conviction"].is_monotonic_decreasing
    a = r.set_index("ticker")
    assert a.loc["A", "n_confirms"] == 3 and a.loc["B", "n_confirms"] == 2 and a.loc["C", "n_confirms"] == 0
    # A: weights durability .2 valuation .12 momentum .16 quality .16 -> weighted mean + 10 gate bonus
    exp = (0.2 * 80 + 0.12 * 70 + 0.16 * 90 + 0.16 * 60) / (0.2 + 0.12 + 0.16 + 0.16) + 10
    assert a.loc["A", "conviction"] == round(exp, 1)
    assert a.loc["C", "conviction"] == 0.0


def test_rank_without_optional_columns():
    df = pd.DataFrame({"ticker": ["A"], "D": [50.0], "V": [50.0], "M": [50.0]})
    r = ms.rank(df)
    assert r["conviction"].iloc[0] == 50.0 and r["n_confirms"].iloc[0] == 1


def test_load_composite_and_merge_optional(tmp_path):
    db = str(tmp_path / "c.db")
    _comp_db(db, [("US", "A", "S", 1, "GGG", 80, 70, 90), ("JP", "B", "S", 2, "BBB", 10, 20, 30)])
    assert len(ms.load_composite(None, db)) == 2 and list(ms.load_composite("JP", db)["ticker"]) == ["B"]
    df = ms.load_composite(None, db)
    csv = tmp_path / "ml.csv"
    pd.DataFrame({"ticker": ["A"], "ml_signal": [77.0]}).to_csv(csv, index=False)
    merged = ms._merge_optional(df, str(csv), "ml_signal")
    assert merged.set_index("ticker").loc["A", "ml_signal"] == 77.0 and np.isnan(merged.set_index("ticker").loc["B", "ml_signal"])
    assert ms._merge_optional(df, None, "x") is df and ms._merge_optional(df, str(tmp_path / "nope.csv"), "x") is df


def test_meta_main(monkeypatch, tmp_path, capsys):
    db = str(tmp_path / "c.db")
    _comp_db(db, [("US", "A", "S", 1, "GGG", 80, 70, 90), ("US", "B", "S", 2, "BBB", 10, 20, 30)])
    pd.DataFrame({"ticker": ["B"], "ml_signal": [99.0]}).to_csv(tmp_path / "ml.csv", index=False)
    pd.DataFrame({"ticker": ["B"], "quality_score": [99.0]}).to_csv(tmp_path / "q.csv", index=False)
    pd.DataFrame({"ticker": ["B"], "pead_score": [99.0]}).to_csv(tmp_path / "p.csv", index=False)
    pd.DataFrame({"ticker": ["A"], "liquidity_score": [99.0]}).to_csv(tmp_path / "l.csv", index=False)
    pd.DataFrame({"ticker": ["A"]}).to_csv(tmp_path / "t.csv", index=False)
    monkeypatch.setattr(sys, "argv", ["ms", "--db", db, "--market", "US", "--top", "1", "--ml", str(tmp_path / "ml.csv"),
                                      "--quality", str(tmp_path / "q.csv"), "--pead", str(tmp_path / "p.csv"),
                                      "--liquidity", str(tmp_path / "l.csv"), "--triple-hits", str(tmp_path / "t.csv")])
    ms.main()
    cap = capsys.readouterr()
    rows = [l for l in cap.out.splitlines() if l.startswith("  US")]
    assert len(rows) == 1 and "META-SCREEN conviction — US (2 names)" in cap.err
    assert rows[0].split()[1] == "A"                                         # triple-hit bonus lifts A above B
    monkeypatch.setattr(sys, "argv", ["ms", "--db", db])
    ms.main()
    assert "all markets" in capsys.readouterr().err
    monkeypatch.setattr(sys, "argv", ["ms", "--db", str(tmp_path / "none.db")])
    with pytest.raises(SystemExit, match="dvm_composite.py first"):
        ms.main()
