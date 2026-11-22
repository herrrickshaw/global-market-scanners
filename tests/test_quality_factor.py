"""quality_factor.py: AFP/QMJ scoring, portfolios, price risk, regression and CLI (offline)."""
import os
import sqlite3
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import quality_factor as qf  # noqa: E402


def test_z_rank_properties():
    z = qf.z_rank(pd.Series([10.0, 20.0, 30.0, 40.0]))
    assert z.mean() == pytest.approx(0) and z.std(ddof=0) == pytest.approx(1)
    assert z.is_monotonic_increasing
    assert qf.z_rank(pd.Series([5.0, 5.0, 5.0])).isna().all()              # zero spread -> NaN
    z2 = qf.z_rank(pd.Series([1.0, np.nan, 3.0, "x"], dtype=object))
    assert z2.iloc[1] != z2.iloc[1]                                         # NaN stays NaN
    assert qf.z_rank(pd.Series([1000.0, 2.0, 3.0])).iloc[0] == qf.z_rank(pd.Series([4.0, 2.0, 3.0])).iloc[0]   # rank-robust


def test_dimension_score_signs_and_missing():
    df = pd.DataFrame({"roe": [1.0, 2.0, 3.0], "roa": [3.0, 2.0, 1.0]})
    s = qf.dimension_score(df, [("roe", +1), ("roa", +1)])
    assert list(s.round(6)) == [0.0, 0.0, 0.0]                              # exactly offsetting
    neg = qf.dimension_score(df, [("roe", -1)])
    assert neg.iloc[0] > neg.iloc[2]
    partial = qf.dimension_score(df, [("roe", +1), ("missing", +1)])        # absent columns ignored
    assert partial.iloc[2] > partial.iloc[0]
    assert qf.dimension_score(df, [("nope", +1)]).isna().all()


def _fund(n=40, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"ticker": [f"T{i}" for i in range(n)], "market": ["US"] * (n // 2) + ["JP"] * (n - n // 2),
                         "roe": rng.normal(10, 5, n), "roa": rng.normal(5, 2, n), "op_margin": rng.normal(15, 5, n),
                         "rev_growth": rng.normal(8, 4, n), "earn_growth": rng.normal(8, 6, n),
                         "de": rng.uniform(0, 3, n), "beta": rng.uniform(0.5, 1.5, n), "vol": rng.uniform(10, 40, n),
                         "div_yield": rng.uniform(0, 4, n), "pb": rng.uniform(0.5, 6, n),
                         "mktcap": rng.uniform(1e8, 1e11, n)})


def test_quality_score_columns_and_direction():
    f = _fund()
    f.loc[0, ["roe", "roa", "op_margin", "rev_growth", "earn_growth", "div_yield"]] = 100.0
    f.loc[0, ["de", "beta", "vol"]] = 0.0
    out = qf.quality_score(f)
    assert {"profitability", "growth", "safety", "payout", "quality_raw", "quality", "quality_score"} <= set(out.columns)
    assert out["quality"].idxmax() == 0 and out["quality_score"].max() == 100.0
    assert out["quality_score"].min() > 0 and "quality" not in f.columns       # input not mutated


def test_assign_deciles_cuts():
    s = pd.Series(np.arange(1.0, 101.0))
    d = qf.assign_deciles(s)
    assert (d == "quality").sum() == 11 and (d == "junk").sum() == 10 and (d == "mid").sum() == 79
    assert d.iloc[89] == "quality" and d.iloc[88] == "mid" and d.iloc[9] == "junk" and d.iloc[10] == "mid"


def test_value_weight():
    w = qf.value_weight(pd.Series([1.0, 3.0]))
    assert list(w) == [0.25, 0.75]
    z = qf.value_weight(pd.Series([0.0, -5.0, 0.0]))
    assert list(z) == [1 / 3] * 3                                            # all non-positive -> equal weight
    coerced = qf.value_weight(pd.Series(["2", "bad", "2"]))
    assert coerced.iloc[0] == 0.5 and np.isnan(coerced.iloc[1])             # non-numeric -> NaN weight


def test_build_portfolios_legs():
    f = qf.quality_score(_fund(300, seed=1))
    legs = qf.build_portfolios(f)
    assert set(legs) <= {"small_quality", "small_junk", "big_quality", "big_junk"} and "small_quality" in legs
    for leg in legs.values():
        assert leg["weight"].sum() == pytest.approx(1.0) and list(leg.columns) == ["ticker", "weight"]
    d = f.dropna(subset=["quality", "mktcap"])
    big = d["mktcap"].rank(pct=True) >= 0.90
    assert set(legs["small_quality"]["ticker"]).isdisjoint(set(d[big]["ticker"]))
    assert qf.build_portfolios(f.assign(mktcap=np.nan)) == {}


def test_combos():
    r = {"small_quality": 0.10, "big_quality": 0.06, "small_junk": 0.02, "big_junk": 0.04}
    assert qf.qmj_combo(r) == pytest.approx(0.05) and qf.lq_combo(r) == pytest.approx(0.08)
    assert qf.qmj_combo({}) == 0.0 and qf.lq_combo({"big_quality": 0.2}) == pytest.approx(0.1)


def _px(symbols, n=300, seed=0, short=()):
    rng = np.random.default_rng(seed)
    rows = []
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    mk = rng.normal(0, 0.01, n)
    for s in symbols:
        k = 30 if s in short else n
        r = 1.5 * mk + rng.normal(0, 0.002, n) if s == "HIGH.NS" else 0.5 * mk + rng.normal(0, 0.002, n)
        c = 100 * np.cumprod(1 + r)
        rows.append(pd.DataFrame({"Date": idx[-k:], "Symbol": s, "Close": c[-k:]}))
    return pd.concat(rows, ignore_index=True)


def test_price_risk(monkeypatch, tmp_path):
    monkeypatch.setattr(qf, "SEED", str(tmp_path))
    assert list(qf.price_risk("US").columns) == ["key", "beta", "vol"] and qf.price_risk("US").empty
    (tmp_path / "cleaned_long_US.parquet").write_bytes(b"x")
    monkeypatch.setattr(qf.pd, "read_parquet", lambda p: _px(["HIGH.NS", "LOW.NS", "SHORT"], short=("SHORT",)))
    r = qf.price_risk("US").set_index("key")
    assert set(r.index) == {"HIGH", "LOW"}                                  # <60 obs dropped, suffix cleaned
    assert r.loc["HIGH", "beta"] > r.loc["LOW", "beta"] > 0
    assert (r["vol"] > 0).all()


def test_load_fundamentals_numeric_and_market_filter(monkeypatch, tmp_path):
    db = tmp_path / "f.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE fund(ticker TEXT, market TEXT, pe TEXT, pb TEXT, roe TEXT, roa TEXT, de TEXT, rev_growth TEXT,"
                " earn_growth TEXT, op_margin TEXT, div_yield TEXT, mktcap TEXT, sector TEXT)")
    con.executemany("INSERT INTO fund VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("A", "US", "10", "2", "15", "5", "0.5", "10", "5", "20", "1", "1e9", "S"),
        ("B", "JP", "Infinity", "x", "n/a", None, None, None, None, None, None, None, None)])
    con.commit(); con.close()
    monkeypatch.setattr(qf, "FUND", str(db))
    f = qf.load_fundamentals()
    assert len(f) == 2 and f.loc[0, "roe"] == 15.0 and np.isnan(f.loc[1, "roe"]) and np.isnan(f.loc[1, "pb"])
    assert f.loc[0, "mktcap"] == 1e9
    only = qf.load_fundamentals(["JP"])
    assert list(only["ticker"]) == ["B"]


def test_attach_price_risk(monkeypatch):
    f = pd.DataFrame({"ticker": ["HIGH.NS", "NOPE.NS"], "market": ["US", "US"]})
    monkeypatch.setattr(qf, "price_risk", lambda m: pd.DataFrame({"key": ["HIGH", "HIGH"], "beta": [1.5, 9.9], "vol": [20.0, 1.0]}))
    out = qf.attach_price_risk(f)
    assert out.loc[0, "beta"] == 1.5 and np.isnan(out.loc[1, "beta"]) and out.loc[0, "key"] == "HIGH"   # first dup kept
    monkeypatch.setattr(qf, "price_risk", lambda m: pd.DataFrame(columns=["key", "beta", "vol"]))
    out = qf.attach_price_risk(f)
    assert out["beta"].isna().all() and out["vol"].isna().all()
    empty = qf.attach_price_risk(pd.DataFrame({"ticker": [], "market": []}))
    assert empty["beta"].isna().all() if len(empty) else list(empty.columns) >= ["beta", "vol"]


def test_score_universe_modes():
    f = _fund(60)
    pooled = qf.score_universe(f, by_market=False)
    per = qf.score_universe(f, by_market=True)
    assert len(pooled) == len(per) == 60 and "quality" in per.columns
    assert per["quality"].notna().all()
    # per-market standardisation: each market's z-ranked quality has mean ~0
    for _, g in per.assign(market=f["market"].values).groupby("market"):
        assert g["quality"].mean() == pytest.approx(0, abs=1e-9)
    assert not np.allclose(pooled["quality"].values, per["quality"].values)


def test_price_premium_regression():
    f = qf.quality_score(_fund(200, seed=3))
    f["pb"] = np.exp(0.5 * f["quality"] + np.random.default_rng(1).normal(0, 0.05, len(f)))
    r = qf.price_premium(f)
    assert r["n"] == 200 and r["quality_coef"] == pytest.approx(0.5, abs=0.05) and abs(r["quality_t"]) > 20
    assert r["mb_premium_per_sd%"] == round((np.exp(r["quality_coef"]) - 1) * 100, 1)
    few = qf.price_premium(f.head(29))
    assert few == {"n": 29, "quality_coef": None}
    assert qf.price_premium(f.head(30))["quality_coef"] is not None
    bad = f.copy(); bad.loc[bad.index[:10], "pb"] = -1.0
    assert qf.price_premium(bad)["n"] == 190


def test_price_premium_single_market_no_dummies():
    f = qf.quality_score(_fund(60, seed=4)).assign(market="US")
    r = qf.price_premium(f)
    assert r["n"] == 60 and r["quality_coef"] is not None


def test_driver_breakdown_sorted():
    f = qf.quality_score(_fund(100, seed=5))
    f["pb"] = np.exp(2 * f["profitability"] - f["safety"] + 3)
    d = qf.driver_breakdown(f)
    assert list(d["dimension"])[0] == "profitability" and d["corr_with_logMB"].is_monotonic_decreasing
    assert set(d["dimension"]) == {"profitability", "growth", "safety", "payout"}
    f.loc[f.index[:5], "pb"] = np.nan
    assert len(qf.driver_breakdown(f)) == 4
    assert qf.driver_breakdown(f.drop(columns=["growth"]))["dimension"].tolist().count("growth") == 0


def _run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["qf", *argv])
    qf.main()


def test_main_missing_db_and_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(qf, "FUND", str(tmp_path / "none.db"))
    with pytest.raises(SystemExit, match="fundamentals_global.py first"):
        _run(monkeypatch)
    (tmp_path / "f.db").write_bytes(b"x")
    monkeypatch.setattr(qf, "FUND", str(tmp_path / "f.db"))
    monkeypatch.setattr(qf, "load_fundamentals", lambda m: pd.DataFrame())
    with pytest.raises(SystemExit, match="no fundamentals for ZZ"):
        _run(monkeypatch, "--market", "ZZ")
    with pytest.raises(SystemExit, match="no fundamentals for any market"):
        _run(monkeypatch)


def test_main_full_report(monkeypatch, tmp_path, capsys):
    (tmp_path / "f.db").write_bytes(b"x")
    monkeypatch.setattr(qf, "FUND", str(tmp_path / "f.db"))
    base = _fund(80, seed=6)
    seen = {}
    monkeypatch.setattr(qf, "load_fundamentals", lambda m: (seen.setdefault("m", m), base)[1])
    monkeypatch.setattr(qf, "attach_price_risk", lambda f: f)
    out_csv = str(tmp_path / "q.csv")
    _run(monkeypatch, "--market", "US", "--portfolios", "--premium", "--top", "3", "--out", out_csv)
    cap = capsys.readouterr()
    assert seen["m"] == ["US"]
    assert "AFP/QMJ QUALITY RANKING — US (80 scored)" in cap.err and "wrote" in cap.err
    rows = [l for l in cap.out.splitlines() if l.startswith("  ") and l.split()[0] in ("US", "JP")]
    assert len(rows) == 3
    assert "QMJ portfolio legs" in cap.out and "QUALITY PRICE PREMIUM" in cap.out and "driver breakdown" in cap.out
    csv = pd.read_csv(out_csv)
    assert list(csv.columns) == ["ticker", "quality_score"] and len(csv) == 80


def test_main_premium_too_few_and_pooled(monkeypatch, tmp_path, capsys):
    (tmp_path / "f.db").write_bytes(b"x")
    monkeypatch.setattr(qf, "FUND", str(tmp_path / "f.db"))
    monkeypatch.setattr(qf, "load_fundamentals", lambda m: _fund(20, seed=7))
    monkeypatch.setattr(qf, "attach_price_risk", lambda f: f)
    pooled = []
    real = qf.score_universe
    monkeypatch.setattr(qf, "score_universe", lambda f, by_market=True: (pooled.append(by_market), real(f, by_market))[1])
    _run(monkeypatch, "--premium", "--pooled")
    cap = capsys.readouterr()
    assert pooled == [False] and "too few firms (20)" in cap.out and "all markets" in cap.err
