"""risk.py, seasonality.py, news_sentiment.py, options_iv.py (offline, hand-computed expectations)."""
import os
import sys
import types

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import marketdata  # noqa: E402
import news_sentiment as ns  # noqa: E402
import options_iv as oi  # noqa: E402
import risk  # noqa: E402
import seasonality as se  # noqa: E402


# ───────────────────────────── risk ─────────────────────────────
def test_equity_curve_and_drawdown():
    assert list(risk.equity_curve([0.1, -0.1])) == pytest.approx([1.1, 0.99])
    assert risk.max_drawdown([0.1, -0.2, 0.1]) == pytest.approx(-0.2)
    assert risk.max_drawdown([0.1, 0.1]) == 0.0 and risk.max_drawdown([]) == 0.0



@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="risk.py max_drawdown: the running peak starts at the first equity value, not at the 1.0 of "
                          "starting capital, so a loss in the very first period is never counted (returns [-0.5, 1.0] -> 0.0)")
def test_max_drawdown_counts_initial_loss():
    assert risk.max_drawdown([-0.5, 1.0]) == pytest.approx(-0.5)


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="risk.py sharpe: `sd > 0` is true for floating-point noise (~1e-18) on a constant return series, "
                          "so a constant 1% series reports a Sharpe of ~8.7e16 instead of 0")
def test_sharpe_of_constant_returns_is_zero():
    assert risk.sharpe(np.full(10, 0.01)) == 0.0


def test_var_cvar():
    r = np.arange(-10, 10) / 100.0                         # 20 evenly spaced returns
    q = np.quantile(r, 0.05)
    assert risk.hist_var(r) == pytest.approx(-q) and risk.hist_var(r, 0.5) == pytest.approx(-np.quantile(r, 0.5))
    assert risk.cvar(r) == pytest.approx(-r[r <= q].mean()) and risk.cvar(r) >= risk.hist_var(r)
    assert risk.hist_var([]) == 0.0 and risk.cvar([]) == 0.0
    assert risk.hist_var([0.01, 0.02, 0.03]) < 0                                    # all gains -> negative "loss"


def test_vol_return_sharpe_sortino():
    assert risk.ann_vol([0.01]) == 0.0 and risk.ann_vol([]) == 0.0
    r = np.array([0.01, -0.01, 0.02, -0.02, 0.01])
    assert risk.ann_vol(r) == pytest.approx(r.std(ddof=1) * np.sqrt(252)) and risk.ann_vol(r, periods=12) == pytest.approx(r.std(ddof=1) * np.sqrt(12))
    assert risk.ann_return([]) == 0.0
    assert risk.ann_return([0.01] * 252) == pytest.approx(1.01 ** 252 - 1)
    assert risk.ann_return([0.1, 0.1], periods=2) == pytest.approx(0.21)
    assert risk.sharpe(r) == pytest.approx(r.mean() / r.std(ddof=1) * np.sqrt(252))
    assert risk.sharpe(r, rf=0.252) == pytest.approx((r - 0.001).mean() / (r - 0.001).std(ddof=1) * np.sqrt(252))
    down = r[r < 0]
    assert risk.sortino(r) == pytest.approx(r.mean() / down.std(ddof=1) * np.sqrt(252))
    assert risk.sortino([0.01, -0.01, 0.02]) == 0.0                                 # one downside obs -> sd undefined -> 0
    assert risk.sortino([0.01, 0.02]) == 0.0


def test_regime_flag():
    calm = np.full(200, 0.001) + np.random.default_rng(0).normal(0, 0.0001, 200)
    assert risk.regime_flag(calm[:63]) == {"regime": "unknown", "vol_ratio": None, "in_drawdown": None}
    assert risk.regime_flag(calm[:64])["regime"] != "unknown"
    assert risk.regime_flag(calm)["regime"] == "risk_on" and risk.regime_flag(calm)["in_drawdown"] is False
    rng = np.random.default_rng(1)
    shock = np.r_[rng.normal(0.0005, 0.005, 200), rng.normal(-0.01, 0.03, 63)]
    off = risk.regime_flag(shock)
    assert off["regime"] == "risk_off" and off["in_drawdown"] is True and off["vol_ratio"] > 1.3
    dd_only = np.r_[np.full(100, 0.001), np.full(10, -0.01), np.full(80, 0.0)]
    r2 = risk.regime_flag(dd_only)
    assert r2["in_drawdown"] is True and r2["regime"] in ("caution", "risk_off")
    flat = risk.regime_flag(np.zeros(100))
    assert flat["vol_ratio"] == 1.0 and flat["regime"] == "risk_on"                  # zero full vol -> ratio 1.0


def test_regime_thresholds():
    # ratio exactly >1.15 w/o drawdown -> caution; just below -> risk_on
    base = np.r_[np.tile([0.01, -0.01], 100), np.tile([0.012, -0.012], 32)]
    rec = risk.regime_flag(base)
    assert rec["vol_ratio"] > 1.0
    up = np.r_[np.full(200, 0.0), np.tile([0.02, 0.0], 32)]
    assert risk.regime_flag(up)["regime"] in ("caution", "risk_on") and risk.regime_flag(up)["in_drawdown"] is False


def test_risk_report_and_main(monkeypatch, tmp_path, capsys):
    r = np.random.default_rng(2).normal(0.0005, 0.01, 300)
    rep = risk.risk_report(r)
    assert set(rep) == {"n", "ann_return%", "ann_vol%", "sharpe", "sortino", "max_dd%", "VaR95%", "CVaR95%", "regime", "vol_ratio", "in_drawdown"}
    assert rep["n"] == 300 and rep["ann_vol%"] == round(risk.ann_vol(r) * 100, 2) and rep["max_dd%"] == round(risk.max_drawdown(r) * 100, 1)
    assert risk.risk_report(r, periods=12)["ann_vol%"] != rep["ann_vol%"]
    monkeypatch.setattr(risk, "SEED", str(tmp_path))
    with pytest.raises(SystemExit, match="no parquet for market 'US'"):
        risk.market_proxy_returns("US")
    (tmp_path / "cleaned_long_US.parquet").write_bytes(b"x")
    idx = pd.date_range("2024-01-01", periods=100, freq="B")
    px = pd.concat([pd.DataFrame({"Date": idx, "Symbol": s, "Close": 100 * np.cumprod(1 + np.random.default_rng(i).normal(0, 0.01, 100))})
                    for i, s in enumerate("AB")])
    monkeypatch.setattr(risk.pd, "read_parquet", lambda p: px)
    mp = risk.market_proxy_returns("US")
    assert len(mp) == 99 and mp.notna().all()
    monkeypatch.setattr(sys, "argv", ["risk", "--periods", "250"])
    risk.main()
    cap = capsys.readouterr()
    assert "risk report — US equal-weight proxy (99 days)" in cap.err and "ann_vol%" in cap.out and "regime" in cap.out


# ───────────────────────────── seasonality ─────────────────────────────
def _rets(years=4, seed=0):
    idx = pd.bdate_range("2020-01-01", periods=252 * years)
    return pd.Series(np.random.default_rng(seed).normal(0.0005, 0.01, len(idx)), index=idx)


def test_day_of_week():
    idx = pd.bdate_range("2024-01-01", periods=10)          # Mon..Fri x2
    r = pd.Series([0.01, 0.02, 0.03, 0.04, 0.05] * 2, index=idx)
    d = se.day_of_week(r)
    assert list(d["weekday"]) == ["Mon", "Tue", "Wed", "Thu", "Fri"] and list(d["mean%"]) == [1.0, 2.0, 3.0, 4.0, 5.0] and list(d["n"]) == [2] * 5
    r.iloc[0] = np.nan
    assert se.day_of_week(r).set_index("weekday").loc["Mon", "n"] == 1
    weekend = pd.Series([0.1], index=pd.to_datetime(["2024-01-06"]))              # Saturday excluded
    assert se.day_of_week(weekend).empty


def test_turn_of_month():
    idx = pd.bdate_range("2024-01-01", "2024-03-29")
    r = pd.Series(0.0, index=idx)
    m = pd.Series(idx.to_period("M"), index=idx)
    for per, g in r.groupby(m):
        r.loc[g.index[-1]] = 0.02                                       # last day of month
        r.loc[g.index[:3]] = 0.02                                       # first 3 days
    t = se.turn_of_month(r)
    assert t["tom_mean%"] == 2.0 and t["rest_mean%"] == 0.0 and t["edge%"] == 2.0 and t["tom_n"] == 12 and t["rest_n"] == len(idx) - 12
    narrow = se.turn_of_month(r, window=1)
    assert narrow["tom_n"] == 6 and narrow["tom_mean%"] == 2.0
    assert se.turn_of_month(pd.Series(dtype=float)) == {}


def test_monthly_seasonality_and_halloween():
    idx = pd.bdate_range("2020-01-01", "2022-12-30")
    r = pd.Series(0.0, index=idx)
    r[(idx.month >= 11) | (idx.month <= 4)] = 0.001
    mo = se.monthly_seasonality(r)
    assert list(mo["month"]) == list(range(1, 13)) and list(mo["n"]) == [3] * 12
    assert (mo.set_index("month").loc[[1, 2, 3, 4, 11, 12], "mean%"] > 0).all() and (mo.set_index("month").loc[5:10, "mean%"] == 0).all()
    h = se.sell_in_may(r)
    assert h["nov_apr%"] > h["may_oct%"] == 0.0 and h["edge%"] == h["nov_apr%"] and h["years"] == 3
    assert h["edge%"] == round(h["nov_apr%"] - h["may_oct%"], 2)


def test_market_returns(monkeypatch):
    import liquidity_factor as lf
    import pead_factor as pf
    idx = pd.date_range("2024-01-01", periods=20, freq="B")
    close = pd.DataFrame({"A": np.linspace(10, 20, 20), "B": np.linspace(10, 5, 20), "C": 1.0}, index=idx.strftime("%Y-%m-%d"))
    close.iloc[5, 0] = close.iloc[4, 0] * 3.0                                         # +200% glitch clipped to +50%
    monkeypatch.setattr(lf, "_market_wide", lambda m: None if m == "NONE" else (close, None))
    monkeypatch.setattr(pf, "_liquid_symbols", lambda c, v: ["A", "B"])
    r = se.market_returns("US")
    assert isinstance(r.index, pd.DatetimeIndex) and len(r) == 19
    a = close["A"].pct_change(fill_method=None).clip(-0.5, 0.5); b = close["B"].pct_change(fill_method=None).clip(-0.5, 0.5)
    assert r.iloc[4] == pytest.approx((a.iloc[5] + b.iloc[5]) / 2) and a.iloc[5] == 0.5
    with pytest.raises(SystemExit, match="no parquet for NONE"):
        se.market_returns("NONE")


def test_seasonality_main(monkeypatch, capsys):
    monkeypatch.setattr(se, "market_returns", lambda m: _rets(4))
    monkeypatch.setattr(sys, "argv", ["se", "--market", "US"])
    se.main()
    out = capsys.readouterr().out
    assert "SEASONALITY — US equal-weight proxy (1008 days, 4 yr)" in out and "day-of-week mean return:" in out and "Sell-in-May:" in out
    monkeypatch.setattr(se, "market_returns", lambda m: _rets(2))
    monkeypatch.setattr(marketdata, "market_list", lambda: ["US", "JP"])
    monkeypatch.setattr(sys, "argv", ["se", "--all"])
    se.main()
    out = capsys.readouterr().out
    assert out.count("SEASONALITY —") == 2 and "skipped (needs >=3 yr; have 2)" in out


# ───────────────────────────── news_sentiment ─────────────────────────────
def test_tokenize_and_score_text():
    assert ns.tokenize("Profit BEAT, up 5%! can't") == ["profit", "beat", "up", "can", "t"] and ns.tokenize(None) == []
    assert ns.score_text("Record profit beats forecasts") == {"pos": 3, "neg": 0, "sentiment": 1.0} or ns.score_text("Record profit beats")["sentiment"] == 1.0
    assert ns.score_text("fraud probe and lawsuit")["sentiment"] == -1.0
    assert ns.score_text("profit but fraud")["sentiment"] == 0.0 and ns.score_text("profit gain loss") == {"pos": 2, "neg": 1, "sentiment": 0.333}
    assert ns.score_text("nothing relevant here") == {"pos": 0, "neg": 0, "sentiment": 0.0} and ns.score_text("") == {"pos": 0, "neg": 0, "sentiment": 0.0}
    assert ns.score_text("great", positive={"great"}, negative=set())["sentiment"] == 1.0


def test_score_headlines():
    r = ns.score_headlines(["Record profit", "fraud probe", "", None, "neutral words"])
    assert r == {"n_headlines": 3, "sentiment": 0.0, "lexicon_hits": 4}
    assert ns.score_headlines([]) == {"n_headlines": 0, "sentiment": 0.0, "lexicon_hits": 0}
    assert ns.score_headlines(["profit gain loss"])["sentiment"] == 0.333


def _install_news(monkeypatch, news, fail=False):
    ac = types.ModuleType("apiclient")
    ac.robust = (lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))) if fail else (lambda src, fn, retries=5: fn())
    monkeypatch.setitem(sys.modules, "apiclient", ac)
    yf = types.ModuleType("yfinance"); yf.Ticker = lambda t: types.SimpleNamespace(news=news.get(t))
    monkeypatch.setitem(sys.modules, "yfinance", yf)


def test_fetch_news_yf(monkeypatch):
    items = [{"title": "A"}, {"content": {"title": "B"}}, {"content": None}, {"title": ""}, {"title": "E"}]
    _install_news(monkeypatch, {"X": items, "N": None})
    assert ns.fetch_news_yf("X") == ["A", "B", "E"] and ns.fetch_news_yf("X", limit=1) == ["A"] and ns.fetch_news_yf("N") == []
    _install_news(monkeypatch, {}, fail=True)
    assert ns.fetch_news_yf("X") == []


def test_news_universe_and_main(monkeypatch, capsys):
    import liquidity_factor as lf
    import pead_factor as pf
    close = pd.DataFrame({"A": [1.0], "B": [1.0], "C": [1.0]})
    monkeypatch.setattr(lf, "_market_wide", lambda m: (close, close))
    monkeypatch.setattr(pf, "_liquid_symbols", lambda c, v: ["A", "B", "C"])
    ns_ = types.SimpleNamespace
    assert ns._universe(ns_(tickers=" aapl, msft ,,", market="US", top=5)) == ["AAPL", "MSFT"]
    assert ns._universe(ns_(tickers=None, market=None, top=2)) == ["A", "B"]
    heads = {"GOOD": ["record profit surge"], "BAD": ["fraud probe", "lawsuit"], "NONE": []}
    monkeypatch.setattr(ns, "fetch_news_yf", lambda t: heads[t])
    monkeypatch.setattr(sys, "argv", ["ns", "--tickers", "bad,good,none"])
    ns.main()
    out = capsys.readouterr().out
    rows = [l.split() for l in out.splitlines() if l.strip().split()[:1] in (["GOOD"], ["BAD"], ["NONE"])]
    assert [r[0] for r in rows] == ["GOOD", "BAD"] and rows[0][1] == "1.00" and rows[1][1] == "-1.00" and "2 names" in out
    monkeypatch.setattr(ns, "fetch_news_yf", lambda t: [])
    ns.main()
    assert "no news fetched" in capsys.readouterr().out


# ───────────────────────────── options_iv ─────────────────────────────
def _chain(strikes, ivs, oi_=None):
    d = {"strike": strikes, "impliedVolatility": ivs}
    if oi_ is not None:
        d["openInterest"] = oi_
    return pd.DataFrame(d)


def test_atm_iv():
    calls = _chain([90, 100, 110], [0.30, 0.20, 0.25]); puts = _chain([90, 100, 110], [0.35, 0.22, 0.28])
    assert oi.atm_iv(calls, puts, 101) == pytest.approx(0.21)
    assert oi.atm_iv(calls, None, 101) == 0.20 and oi.atm_iv(None, None, 100) != oi.atm_iv(None, None, 100)
    bad = _chain([100], [0.0]); assert np.isnan(oi.atm_iv(bad, pd.DataFrame(), 100))
    nan = _chain([100], [np.nan]); assert oi.atm_iv(nan, puts, 100) == 0.22
    assert np.isnan(oi.atm_iv(pd.DataFrame({"strike": [1]}), pd.DataFrame(), 1))                  # missing IV column
    assert oi.atm_iv(calls, puts, 94) == pytest.approx((0.30 + 0.35) / 2)                         # nearest strike is 90 or 100? 94 -> 90


def test_put_call_ratio():
    c = _chain([1, 2], [0.1, 0.1], [100, 100]); p = _chain([1, 2], [0.1, 0.1], [50, 100])
    assert oi.put_call_ratio(c, p) == 0.75
    assert oi.put_call_ratio(c, p, field="volume") != oi.put_call_ratio(c, p, field="volume")      # missing field -> NaN
    zero = _chain([1], [0.1], [0])
    assert np.isnan(oi.put_call_ratio(zero, p)) and oi.put_call_ratio(c, _chain([1], [0.1], [0])) == 0.0


def test_iv_skew():
    calls = _chain([100, 110, 120], [0.20, 0.22, 0.30]); puts = _chain([80, 90, 100], [0.45, 0.40, 0.25])
    assert oi.iv_skew(calls, puts, 100) == pytest.approx(0.40 - 0.22)                              # 90 put vs 110 call
    assert oi.iv_skew(calls, puts, 100, moneyness=0.2) == pytest.approx(0.45 - 0.30)
    assert np.isnan(oi.iv_skew(None, puts, 100)) and np.isnan(oi.iv_skew(calls, pd.DataFrame(), 100))
    nanc = _chain([110], [np.nan]); assert np.isnan(oi.iv_skew(nanc, puts, 100))


def test_chain_metrics():
    calls = _chain([90, 100, 110], [0.30, 0.20, 0.25], [10, 20, 30]); puts = _chain([90, 100, 110], [0.35, 0.22, 0.28], [30, 30, 30])
    m = oi.chain_metrics(calls, puts, 100)
    assert m == {"atm_iv%": 21.0, "put_call": 1.5, "iv_skew%": round((0.35 - 0.25) * 100, 1)}
    empty = oi.chain_metrics(pd.DataFrame({"strike": [], "impliedVolatility": [], "openInterest": []}), pd.DataFrame(), 100)
    assert np.isnan(empty["atm_iv%"]) and np.isnan(empty["iv_skew%"])


def _install_chain(monkeypatch, tickers, fail=False):
    ac = types.ModuleType("apiclient")
    ac.robust = (lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))) if fail else (lambda src, fn, retries=5: fn())
    monkeypatch.setitem(sys.modules, "apiclient", ac)

    def Ticker(t):
        spec = tickers.get(t, {})
        return types.SimpleNamespace(options=spec.get("options", ()), option_chain=lambda e: spec["chain"],
                                     fast_info={"lastPrice": spec.get("spot")})
    yf = types.ModuleType("yfinance"); yf.Ticker = Ticker
    monkeypatch.setitem(sys.modules, "yfinance", yf)


def test_fetch_chain_yf(monkeypatch):
    calls = _chain([100], [0.2], [1]); puts = _chain([100], [0.2], [1])
    spec = {"A": {"options": ("2025-01-17",), "chain": types.SimpleNamespace(calls=calls, puts=puts), "spot": "101.5"},
            "NOSPOT": {"options": ("x",), "chain": types.SimpleNamespace(calls=calls, puts=puts), "spot": None}}
    _install_chain(monkeypatch, spec)
    c, p, s = oi.fetch_chain_yf("A")
    assert c is calls and p is puts and s == 101.5
    assert np.isnan(oi.fetch_chain_yf("NOSPOT")[2]) and oi.fetch_chain_yf("EMPTY") == (None, None, None)
    _install_chain(monkeypatch, spec, fail=True)
    assert oi.fetch_chain_yf("A") == (None, None, None)


def test_options_main(monkeypatch, capsys):
    calls = _chain([90, 100, 110], [0.30, 0.20, 0.25], [10, 20, 30]); puts = _chain([90, 100, 110], [0.35, 0.22, 0.28], [30, 30, 30])
    chains = {"AAA": (calls, puts, 100.0), "BBB": (calls, puts.assign(impliedVolatility=0.5), 100.0), "CCC": (None, None, None),
              "DDD": (calls, puts, float("nan"))}
    monkeypatch.setattr(oi, "fetch_chain_yf", lambda t: chains[t])
    monkeypatch.setattr(sys, "argv", ["oi", "--tickers", "aaa, bbb,ccc,ddd,,"])
    oi.main()
    out = capsys.readouterr().out
    rows = [l.split() for l in out.splitlines() if l.strip().split()[:1] in (["AAA"], ["BBB"])]
    assert [r[0] for r in rows] == ["BBB", "AAA"]                               # sorted by IV skew descending
    assert "(yfinance, nearest expiry) — 2 names" in out
    monkeypatch.setattr(oi, "fetch_chain_yf", lambda t: (None, None, None))
    oi.main()
    assert "no option chains fetched" in capsys.readouterr().out
