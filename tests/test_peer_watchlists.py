"""peer_network.py + watchlists.py (parquet/scan inputs faked)."""
import os
import sys
import types

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import marketdata  # noqa: E402
import peer_network as pn  # noqa: E402
import watchlists as wl  # noqa: E402


# ───────────────────────────── peer_network ─────────────────────────────
def test_basket_return():
    px = pd.DataFrame({"a": [100.0, 110.0], "b": [50.0, 45.0]})
    assert pn.basket_return(px, 0, 1) == pytest.approx((0.10 - 0.10) / 2)
    assert np.isnan(pn.basket_return(px, 0, 2)) and np.isnan(pn.basket_return(px, -1, 1))
    assert np.isnan(pn.basket_return(px[[]], 0, 1))
    bad = pd.DataFrame({"a": [np.nan, 1.0], "b": [0.0, 2.0], "c": [10.0, np.nan], "d": [10.0, 12.0]})
    assert pn.basket_return(bad, 0, 1) == pytest.approx(0.2)                   # only 'd' is valid
    assert np.isnan(pn.basket_return(bad[["a", "b", "c"]], 0, 1))
    assert pn.basket_return(px, 0, 0) == 0.0                                    # end == start allowed


def test_peer_signal():
    assert pn.peer_signal(0.05) == 0.05 and np.isnan(pn.peer_signal(float("nan"))) and np.isnan(pn.peer_signal(float("inf")))


@pytest.fixture
def market(monkeypatch, tmp_path):
    import liquidity_factor as lf
    import pead_factor as pf
    n = 120
    rng = np.random.default_rng(0)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    cols = ["A", "B", "C", "D", "E", "F", "G"]
    close = pd.DataFrame({c: 50 * np.cumprod(1 + rng.normal(0.001, 0.01, n)) for c in cols}, index=idx)
    vol = pd.DataFrame(1e6, index=idx, columns=cols)
    comp = pd.DataFrame({"ticker": ["A.NS", "B", "C", "D", "E", "F", "G"],
                         "industry": ["Soft", "Soft", "Soft", "Soft", "Solo", "Solo", np.nan]})
    p = tmp_path / "companies.parquet"; p.write_bytes(b"x")
    monkeypatch.setattr(pn, "COMPANIES", str(p))
    monkeypatch.setattr(pn.pd, "read_parquet", lambda path: comp)
    monkeypatch.setattr(lf, "_market_wide", lambda m: None if m == "NONE" else (close, vol))
    monkeypatch.setattr(pf, "_liquid_symbols", lambda c, v: list(c.columns))
    return close


def test_build_panel(market):
    panel = pn.build_panel("US", lag=21, horizon=21)
    assert set(panel["ticker"]) == {"A", "B", "C", "D"} and set(panel.columns) == {"market", "ticker", "industry", "peer_signal", "own_lag", "fwd_ret"}
    close = market
    T = len(close) - 21 - 1
    r = panel.set_index("ticker").loc["B"]
    peers = ["A", "C", "D"]
    exp = np.mean([close[p].iloc[T] / close[p].iloc[T - 21] - 1 for p in peers])
    assert r["peer_signal"] == pytest.approx(exp) and r["fwd_ret"] == pytest.approx(close["B"].iloc[T + 21] / close["B"].iloc[T] - 1)
    assert r["own_lag"] == pytest.approx(close["B"].iloc[T] / close["B"].iloc[T - 21] - 1)
    assert pn.build_panel("NONE").empty and pn.build_panel("US", lag=200).empty


def test_build_panel_requires_peers_and_companies_file(market, monkeypatch, tmp_path):
    monkeypatch.setattr(pn, "COMPANIES", str(tmp_path / "missing.parquet"))
    assert pn.build_panel("US").empty
    close = market
    p = tmp_path / "c2.parquet"; p.write_bytes(b"x")
    monkeypatch.setattr(pn, "COMPANIES", str(p))
    monkeypatch.setattr(pn.pd, "read_parquet", lambda path: pd.DataFrame({"ticker": ["A", "B", "C"], "industry": ["Soft"] * 3}))
    assert pn.build_panel("US").empty                                           # only 3 members (< 4 needed)


def test_build_panel_skips_nan_prices(market):
    market.iloc[-1 - 21, 0] = np.nan                                             # A has no p0
    panel = pn.build_panel("US")
    assert "A" not in set(panel["ticker"]) and {"B", "C", "D"} <= set(panel["ticker"])


def test_peer_main(market, monkeypatch, capsys):
    monkeypatch.setattr(marketdata, "market_list", lambda: ["US"])
    monkeypatch.setattr(sys, "argv", ["pn", "--top", "2"])
    pn.main()
    out = capsys.readouterr().out
    assert "PEER-NETWORK LEAD-LAG — 4 stock-obs, peer 21d lag -> 21d forward" in out and "current top 2 by peer signal" in out
    assert "fwd return by peer-signal quantile" not in out                      # < 15 obs -> no curve
    monkeypatch.setattr(sys, "argv", ["pn", "--market", "NONE"])
    with pytest.raises(SystemExit, match="no peer-network panel"):
        pn.main()


def test_peer_main_with_curve(monkeypatch, capsys):
    rng = np.random.default_rng(1)
    s = rng.normal(0, 0.05, 80)
    panel = pd.DataFrame({"market": "US", "ticker": [f"T{i}" for i in range(80)], "industry": "Ind", "peer_signal": s,
                          "own_lag": 0.0, "fwd_ret": s * 0.5})
    panel.loc[0, "fwd_ret"] = 9.0                                               # outlier filtered (|ret| >= 3)
    monkeypatch.setattr(pn, "build_panel", lambda m, lag, horizon: panel)
    monkeypatch.setattr(marketdata, "market_list", lambda: ["US"])
    monkeypatch.setattr(sys, "argv", ["pn", "--lag", "10", "--horizon", "5"])
    pn.main()
    out = capsys.readouterr().out
    assert "79 stock-obs" in out and "peer 10d lag -> 5d forward" in out and "IC=+1.000" in out and "Q5-Q1 median" in out


# ───────────────────────────── watchlists ─────────────────────────────
def _scored(n=10):
    return pd.DataFrame({"ticker": [f"T{i}.NS" for i in range(n)], "market": "IN",
                         "quality_score": [10.0 * i for i in range(n)], "roe": [20.0] * n, "de": [0.5] * n,
                         "sector": "S"})


def test_strong_from_scores():
    s = wl.strong_from_scores(_scored())
    assert list(s["quality_score"]) == [90.0, 80.0, 70.0, 60.0] and s["strong_profile"].all() and s["key"].iloc[0] == "T9"
    assert len(wl.strong_from_scores(_scored(), min_quality=60.0)) == 4 and len(wl.strong_from_scores(_scored(), 60.1)) == 3
    d = _scored(); d.loc[9, "roe"] = 15.0; d.loc[8, "de"] = 1.0; d.loc[7, "roe"] = np.nan
    f = wl.strong_from_scores(d).set_index("ticker")["strong_profile"]
    assert not f["T9.NS"] and not f["T8.NS"] and not f["T7.NS"] and f["T6.NS"]    # roe>15 & de<1 strict
    d2 = _scored(); d2.loc[0, "quality_score"] = np.nan
    assert "T0.NS" not in set(wl.strong_from_scores(d2, 0)["ticker"])


@pytest.mark.xfail(strict=True, raises=(AttributeError, TypeError),
                   reason="watchlists.py strong_from_scores: a scored frame without roe/de columns makes "
                          "pd.to_numeric(None) return a scalar, so `(roe > 15) & (de < 1)` is a numpy bool and "
                          ".fillna() raises (AttributeError on pandas<3)")
def test_strong_from_scores_without_roe_de_columns():
    out = wl.strong_from_scores(_scored().drop(columns=["roe", "de"]))
    assert not out["strong_profile"].any()


def _scan():
    return pd.DataFrame({"market": "IN", "ticker": ["A.NS", "B.NS", "C.NS", "D.NS"], "state": ["in_box", "in_box", "breakout", "in_box"],
                         "accumulation": [2.0, 0.5, 5.0, 0.49], "position": [0.5] * 4, "cmf": [0.1] * 4, "close": 10.0,
                         "obv_trend": 0.3, "ud_vol_ratio": [1.5, np.inf, 1.0, 2.0]})


def test_accumulated_from_scan():
    a = wl.accumulated_from_scan(_scan())
    assert list(a["ticker"]) == ["A.NS", "B.NS"] and list(a["key"]) == ["A", "B"]          # in_box, accumulation >= 0.5
    assert len(wl.accumulated_from_scan(_scan(), min_accum=2.0)) == 1 and wl.accumulated_from_scan(_scan(), 9).empty


def test_intersect():
    strong = wl.strong_from_scores(_scored())
    accum = wl.accumulated_from_scan(_scan().assign(ticker=["T9.NS", "T8.BO", "ZZ.NS", "T1.NS"]))
    both = wl.intersect(strong, accum)
    assert list(both["key"]) == ["T9", "T8"] and both["quality_score"].is_monotonic_decreasing
    assert {"ticker_f", "ticker_a", "accumulation", "position", "cmf"} <= set(both.columns)
    assert wl.intersect(pd.DataFrame(), accum).empty and wl.intersect(strong, pd.DataFrame()).empty


def test_build_strong(monkeypatch):
    qf = types.ModuleType("quality_factor")
    qf.load_fundamentals = lambda m: pd.DataFrame() if m == ["NONE"] else pd.DataFrame({"x": [1]})
    qf.attach_price_risk = lambda f: f
    qf.score_universe = lambda f, by_market=True: _scored()
    monkeypatch.setitem(sys.modules, "quality_factor", qf)
    assert len(wl.build_strong(["IN"])) == 4 and wl.build_strong(["NONE"]).empty


def test_build_accumulated(monkeypatch):
    dv = types.ModuleType("darvas_volume")
    dv.scan_market = lambda m: pd.DataFrame() if m == "NONE" else _scan()
    monkeypatch.setitem(sys.modules, "darvas_volume", dv)
    assert len(wl.build_accumulated(["IN", "NONE", "JP"])) == 4                 # two scans concatenated, empty skipped
    assert wl.build_accumulated(["NONE"]).empty


def test_markets_selection(monkeypatch):
    monkeypatch.setattr(marketdata, "market_list", lambda: ["US", "JP"])
    ns = types.SimpleNamespace
    assert wl._markets(ns(all=True, market="US")) == ["US", "JP"] and wl._markets(ns(all=False, market=None)) == ["US", "JP"]
    assert wl._markets(ns(all=False, market="KR")) == ["KR"]


def test_watchlists_main(monkeypatch, tmp_path, capsys):
    strong = wl.strong_from_scores(_scored())
    accum = wl.accumulated_from_scan(_scan().assign(ticker=["T9.NS", "T8.NS", "ZZ.NS", "T1.NS"]))
    monkeypatch.setattr(wl, "build_strong", lambda m: strong)
    monkeypatch.setattr(wl, "build_accumulated", lambda m: accum)
    prefix = str(tmp_path / "wl")
    monkeypatch.setattr(sys, "argv", ["wl", "--market", "IN", "--both", "--top", "5", "--csv", prefix])
    wl.main()
    cap = capsys.readouterr()
    assert "FUNDAMENTALLY STRONG  (4 names)" in cap.out and "BEING ACCUMULATED  (2 names)" in cap.out
    assert "BOTH — strong AND accumulated  (2 names)" in cap.out and "★" in cap.out and "inf" in cap.out
    assert os.path.exists(prefix + "_strong.csv") and os.path.exists(prefix + "_accum.csv") and "wrote" in cap.err
    assert len(pd.read_csv(prefix + "_strong.csv")) == 4


def test_watchlists_main_empty(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(wl, "build_strong", lambda m: pd.DataFrame())
    monkeypatch.setattr(wl, "build_accumulated", lambda m: pd.DataFrame())
    monkeypatch.setattr(marketdata, "market_list", lambda: ["US"])
    monkeypatch.setattr(sys, "argv", ["wl", "--both", "--csv", str(tmp_path / "e")])
    wl.main()
    out = capsys.readouterr().out
    assert "(no fundamentals for the selected market)" in out and "(no accumulation coils found)" in out and "(no overlap" in out
    assert not os.path.exists(str(tmp_path / "e_strong.csv")) and not os.path.exists(str(tmp_path / "e_accum.csv"))
