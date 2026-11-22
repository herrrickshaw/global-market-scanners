"""factor_research.py: OLS/quintile helpers, PIT cross-section, Markowitz comparison and main() (offline)."""
import os
import sys
import types

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import factor_research as fr  # noqa: E402
from conftest_helpers import make_ohlcv  # noqa: E402


def test_ols_recovers_coefficients_and_r2():
    rng = np.random.default_rng(0)
    X = rng.standard_normal((500, 2))
    y = 1.5 + 2.0 * X[:, 0] - 0.5 * X[:, 1] + rng.standard_normal(500) * 0.1
    r = fr.ols(y, X, ["a", "b"])
    assert r["intercept"][0] == pytest.approx(1.5, abs=0.05)
    assert r["a"][0] == pytest.approx(2.0, abs=0.05) and r["b"][0] == pytest.approx(-0.5, abs=0.05)
    assert abs(r["a"][1]) > 50 and r["_R2"] > 0.99
    assert set(r) == {"intercept", "a", "b", "_R2"}


def test_ols_no_signal_low_r2_small_t():
    rng = np.random.default_rng(1)
    X = rng.standard_normal((400, 1)); y = rng.standard_normal(400)
    r = fr.ols(y, X, ["x"])
    assert r["_R2"] < 0.05 and abs(r["x"][1]) < 4


def test_quintile_table_counts_and_means():
    df = pd.DataFrame({"s": np.arange(100.0), "fwd_ret": np.arange(100.0)})
    t = fr.quintile_table(df, "s")
    assert list(t.index) == ["Q1", "Q2", "Q3", "Q4", "Q5"]
    assert list(t["count"]) == [20] * 5 and t["mean"].is_monotonic_increasing
    assert t.loc["Q1", "mean"] == 9.5 and t.loc["Q5", "mean"] == 89.5


def test_load_universe(monkeypatch):
    class XL:
        def __init__(self, p):
            XL.p = p

        def parse(self, s):
            return pd.DataFrame({"Symbol": ["A", "nan", "B", "C"]})
    monkeypatch.setattr(fr.pd, "ExcelFile", XL)
    monkeypatch.setattr(fr.glob, "glob", lambda p, recursive=False: ["/z.xlsx", "/y.xlsx"])
    assert fr.load_universe(0) == ["A", "B", "C"] and XL.p == "/z.xlsx"
    assert fr.load_universe(2) == ["A", "B"]


def _ohlc(n_stocks=8, n=700):
    return {f"S{i}": make_ohlcv(n, seed=i, drift=0.0005 * (i % 4), volume=2e6 * (i + 1) / 3) for i in range(n_stocks)}


def _install_pit(monkeypatch, facts):
    m = types.ModuleType("pit_fundamentals")
    m.as_of = lambda t, d: facts.get(t, {})
    m._g = lambda lst, i=0: lst[i] if lst and len(lst) > i else None
    monkeypatch.setitem(sys.modules, "pit_fundamentals", m)


def _facts(ohlc, shares=1e6, ni=5e7):
    return {t: {"shares": shares, "ni": [ni]} for t in ohlc}


def test_cross_section_builds_pit_rows(monkeypatch):
    ohlc = _ohlc()
    _install_pit(monkeypatch, _facts(ohlc))
    df, mkt = fr.cross_section(ohlc, min_dollar_vol=1.0)
    assert len(df) == 8 and set(df.columns) == {"ticker", "beta", "size", "log_size", "value_ey", "fwd_ret"}
    r = df.set_index("ticker").loc["S1"]
    c = ohlc["S1"]["Close"]
    T = c.index[-253]
    assert r["fwd_ret"] == pytest.approx((c.iloc[-1] / c.loc[T] - 1) * 100)
    assert r["size"] == pytest.approx(c.loc[T] * 1e6) and r["value_ey"] == pytest.approx(5e7 / (c.loc[T] * 1e6))
    assert r["log_size"] == pytest.approx(np.log10(r["size"]))
    assert isinstance(mkt, pd.Series)


def test_cross_section_filters(monkeypatch):
    ohlc = _ohlc(4)
    ohlc["SHORT"] = make_ohlcv(400)
    ohlc["ILLIQ"] = make_ohlcv(700, volume=1.0)
    facts = _facts(ohlc)
    facts["S0"] = {}                                   # no fundamentals -> dropped
    facts["S1"] = {"shares": 1e6, "ni": []}           # no earnings -> dropped
    facts["S2"] = {"shares": None, "ni": [1.0]}        # no shares -> dropped
    _install_pit(monkeypatch, facts)
    df, _ = fr.cross_section(ohlc, min_dollar_vol=1e6)
    assert set(df["ticker"]) == {"S3"}


def test_markowitz_portfolios(monkeypatch):
    ohlc = _ohlc(12)
    df = pd.DataFrame({"ticker": list(ohlc), "fwd_ret": np.arange(12.0)})
    res = fr.markowitz(ohlc, df)
    assert list(res) == ["Concentrated(1)", "EqualWeight", "MinVariance", "MaxSharpe"]
    # min-variance has the lowest ex-ante vol of the four
    vols = {k: v["ex_ante_vol%"] for k, v in res.items()}
    assert vols["MinVariance"] == min(vols.values())
    assert vols["Concentrated(1)"] > vols["EqualWeight"]
    assert all(set(v) == {"fwd_ret%", "ex_ante_vol%", "ret/vol"} for v in res.values())


def test_markowitz_too_few_assets():
    ohlc = _ohlc(4)
    assert fr.markowitz(ohlc, pd.DataFrame({"ticker": list(ohlc), "fwd_ret": [1.0, 2, 3, 4]})) is None


def _install_store(monkeypatch, ohlc):
    m = types.ModuleType("market_store")
    m.cached_download = lambda t, years: ohlc
    monkeypatch.setitem(sys.modules, "market_store", m)


def _panel(n, rng_seed=0):
    rng = np.random.default_rng(rng_seed)
    beta = rng.uniform(0.5, 1.5, n)
    return pd.DataFrame({"ticker": [f"S{i % 8}" for i in range(n)], "beta": beta, "size": 1e9 * beta,
                         "log_size": np.log10(1e9 * beta) + rng.normal(0, 0.1, n),
                         "value_ey": rng.uniform(0.01, 0.1, n),
                         "fwd_ret": 20 * beta + rng.normal(0, 3, n)})


def test_main_too_few_stocks(monkeypatch, capsys):
    _install_store(monkeypatch, {})
    monkeypatch.setattr(fr, "load_universe", lambda l: ["A"])
    monkeypatch.setattr(fr, "cross_section", lambda o, min_dollar_vol: (_panel(10), None))
    monkeypatch.setattr(sys, "argv", ["fr"])
    fr.main()
    cap = capsys.readouterr()
    assert "too few stocks" in cap.err and "P1" not in cap.out


def test_main_full_report(monkeypatch, capsys):
    ohlc = _ohlc(12)
    _install_store(monkeypatch, ohlc)
    monkeypatch.setattr(fr, "load_universe", lambda l: list(ohlc))
    panel = _panel(120)
    panel["ticker"] = [list(ohlc)[i % 12] for i in range(120)]
    monkeypatch.setattr(fr, "cross_section", lambda o, min_dollar_vol: (panel, None))
    monkeypatch.setattr(sys, "argv", ["fr", "--limit", "9", "--min-dollar-vol", "1e3"])
    fr.main()
    out = capsys.readouterr().out
    assert "P1 — MARKOWITZ" in out and "MinVariance" in out
    assert "P2 — SHARPE (1964) CAPM" in out and "OLS fwd_ret ~ beta" in out and "CAPM SUPPORTED" in out
    assert "P3 — FAMA-FRENCH" in out and "multivariate OLS" in out and "P4" in out
    assert "statistically significant factors:" in out and "beta significant? yes" in out


def test_main_markowitz_unavailable_and_beta_insignificant(monkeypatch, capsys):
    ohlc = _ohlc(12)
    _install_store(monkeypatch, ohlc)
    monkeypatch.setattr(fr, "load_universe", lambda l: list(ohlc))
    rng = np.random.default_rng(3)
    panel = _panel(120)
    panel["fwd_ret"] = rng.normal(0, 5, 120)                                # no beta effect
    monkeypatch.setattr(fr, "cross_section", lambda o, min_dollar_vol: (panel, None))
    monkeypatch.setattr(fr, "markowitz", lambda o, d: None)
    monkeypatch.setattr(sys, "argv", ["fr"])
    fr.main()
    out = capsys.readouterr().out
    assert "P1 — MARKOWITZ" in out and "Verdict" not in out
    assert "CAPM NOT supported" in out
