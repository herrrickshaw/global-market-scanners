"""pead_factor.py, liquidity_factor.py, earnings_liquidity.py: event studies and liquidity premia (offline)."""
import os
import sys
import types

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import earnings_liquidity as el  # noqa: E402
import liquidity_factor as lf  # noqa: E402
import marketdata  # noqa: E402
import pead_factor as pf  # noqa: E402
from conftest_helpers import event_market  # noqa: E402


# ───────────────────────────── pead_factor ─────────────────────────────
def test_sue_and_market_adjust_and_car():
    assert pf.sue(10, 6, 2) == 2.0 and np.isnan(pf.sue(1, 1, 0)) and np.isnan(pf.sue(1, 1, float("nan")))
    s = pd.Series([0.01, 0.02, 0.03]); m = pd.Series([0.005, 0.005, 0.005])
    assert list(pf.market_adjust(s, m, beta=2.0).round(4)) == [0.0, 0.01, 0.02]
    assert list(pf.market_adjust(s, m).round(4)) == [0.005, 0.015, 0.025]
    a = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    assert pf.car(a, 1, 3) == 9.0                         # inclusive of both ends
    assert pf.car(a, -5, 1) == 3.0 and pf.car(a, 3, 99) == 9.0 and pf.car(a, 10, 12) == 0.0


def test_detect_events_declusters():
    c, v = event_market(n=200, k=1, events=(60, 80, 130), jump=0.12)
    close, vol = c["S0"], v["S0"]
    ev = pf.detect_events(close, vol)
    assert ev == [60, 130]                                # 80 within min_gap(40) of 60 -> dropped
    assert pf.detect_events(close, vol, min_gap=10) == [60, 80, 130]
    assert pf.detect_events(close, vol, vol_mult=100.0) == []
    flat = pd.Series(np.full(100, 10.0)); assert pf.detect_events(flat, pd.Series(np.full(100, 5.0))) == []


def test_event_surprise_window():
    a = pd.Series(np.arange(10.0))
    assert pf.event_surprise(a, 5) == 4 + 5 + 6 and pf.event_surprise(a, 5, half=0) == 5.0
    assert pf.event_surprise(a, 0) == 0 + 1                 # left window clipped at 0


def test_pead_score_decay_and_bounds():
    assert pf.pead_score(0.0, 0) == 50.0
    assert pf.pead_score(0.1, 0) == pytest.approx(50 + 50 * np.tanh(0.8))
    assert pf.pead_score(0.1, 20) == pytest.approx(50 + 50 * np.tanh(0.8) * 0.5)      # one half-life
    assert pf.pead_score(-0.1, 0) < 50 < pf.pead_score(0.1, 0)
    assert pf.pead_score(0.1, -1) == 50.0 and pf.pead_score(0.1, 61) == 50.0 and pf.pead_score(float("nan"), 3) == 50.0
    assert pf.pead_score(0.1, 60) != 50.0 and pf.pead_score(0.1, 90, horizon=100) != 50.0


def test_drift_by_surprise_and_monotonicity():
    rng = np.random.default_rng(0)
    s = rng.normal(0, 0.05, 100)
    ev = pd.DataFrame({"surprise": s, "fwd_car": s * 0.5})
    c = pf.drift_by_surprise(ev)
    assert list(c["bucket"]) == ["Q1", "Q2", "Q3", "Q4", "Q5"] and c["mean%"].is_monotonic_increasing
    assert pf.monotonicity(c) == pytest.approx(1.0)
    assert pf.drift_by_surprise(ev.head(14)).empty and not pf.drift_by_surprise(ev.head(15)).empty
    assert len(pf.drift_by_surprise(ev, q=4)) == 4


def test_liquid_symbols_delegates():
    idx = pd.date_range("2024-01-01", periods=260, freq="B")
    close = pd.DataFrame({f"S{i}": 10.0 for i in range(10)}, index=idx)
    vol = pd.DataFrame({f"S{i}": 1000.0 * (i + 1) for i in range(10)}, index=idx)
    assert pf._liquid_symbols(close, vol) == ["S6", "S7", "S8", "S9"]       # top 40% by dollar volume


def test_market_wide(monkeypatch, tmp_path):
    monkeypatch.setattr(pf, "SEED", str(tmp_path))
    assert pf._market_wide("US") == (None, None)
    (tmp_path / "cleaned_long_US.parquet").write_bytes(b"x")
    px = pd.DataFrame({"Date": ["d1", "d1", "d2"], "Symbol": ["A", "B", "A"], "Close": [1.0, 2.0, 3.0], "Volume": [10, 20, 30]})
    monkeypatch.setattr(pf.pd, "read_parquet", lambda p: px)
    close, vol = pf._market_wide("US")
    assert close.loc["d2", "A"] == 3.0 and np.isnan(close.loc["d2", "B"]) and vol.loc["d1", "B"] == 20.0


@pytest.fixture
def evmkt(monkeypatch):
    close, vol = event_market(n=320, k=10, events=(120, 200), jump=0.10)
    monkeypatch.setattr(pf, "_market_wide", lambda m: (None, None) if m == "NONE" else (close, vol))
    monkeypatch.setattr(pf, "_liquid_symbols", lambda c, v: list(c.columns))
    monkeypatch.setattr(pf, "MIN_HISTORY", 250)
    return close, vol


def test_pead_scan_market(evmkt):
    ev, sig = pf.scan_market("US", horizon=60)
    assert set(ev.columns) == {"market", "ticker", "surprise", "fwd_car"} and len(ev) > 0
    assert (ev["surprise"].abs() <= pf.SURPRISE_CAP).all()
    assert set(sig.columns) == {"market", "ticker", "surprise", "days_since", "pead_score"}
    assert (sig["days_since"] == 320 - 1 - 200).all()                       # last event is at index 200
    assert pf.scan_market("NONE", 60)[0].empty
    ev_long, _ = pf.scan_market("US", horizon=130)
    assert len(ev_long) < len(ev)                                           # event 200 lacks a full 130d window


def test_pead_scan_drops_glitch_surprises(evmkt, monkeypatch):
    close, vol = evmkt
    close.iloc[200:, 0] *= 3.0                                              # +200% jump -> split/glitch filtered
    ev, sig = pf.scan_market("US", horizon=60)
    assert not (ev[ev.ticker == "S0"]["surprise"].abs() > pf.SURPRISE_CAP).any()


def test_pead_main(evmkt, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(marketdata, "market_list", lambda: ["US"])
    out = str(tmp_path / "pead.csv")
    monkeypatch.setattr(sys, "argv", ["pf", "--out", out, "--top", "3", "--horizon", "40"])
    pf.main()
    cap = capsys.readouterr()
    assert "PEAD EVENT STUDY — US" in cap.out and "horizon 40d" in cap.out and "monotonicity" in cap.out
    assert "current PEAD signals — top 3" in cap.out
    assert list(pd.read_csv(out).columns) == ["ticker", "pead_score"] and "wrote" in cap.err
    monkeypatch.setattr(sys, "argv", ["pf", "--market", "NONE"])
    with pytest.raises(SystemExit, match="no events detected"):
        pf.main()


def test_pead_main_verdict_variants(monkeypatch, capsys):
    ev = pd.DataFrame({"market": "US", "ticker": "A", "surprise": np.linspace(-0.1, 0.1, 50), "fwd_car": np.linspace(-0.05, 0.05, 50)})
    sig = pd.DataFrame({"market": ["US"], "ticker": ["A"], "surprise": [0.05], "days_since": [3], "pead_score": [70.0]})
    monkeypatch.setattr(marketdata, "market_list", lambda: ["US", "JP", "KR", "DE"])
    monkeypatch.setattr(pf, "scan_market", lambda m, h: (ev, sig))
    monkeypatch.setattr(sys, "argv", ["pf", "--all"])
    pf.main()
    out = capsys.readouterr().out
    assert "4 markets" in out and "PEAD PRESENT" in out
    monkeypatch.setattr(pf, "scan_market", lambda m, h: (ev.assign(fwd_car=-ev["fwd_car"]), sig))
    pf.main()
    assert "weak/absent" in capsys.readouterr().out


# ───────────────────────────── liquidity_factor ─────────────────────────────
def test_amihud_illiq():
    r = [0.01] * 5; dv = [1e6] * 5
    assert lf.amihud_illiq(r, dv) == pytest.approx(0.01 / 1e6 * 1e6)
    assert lf.amihud_illiq(r, dv, scale=1.0) == pytest.approx(1e-8)
    assert np.isnan(lf.amihud_illiq(r[:4], dv[:4]))                             # needs >= 5 valid days
    assert lf.amihud_illiq([0.01] * 6, [1e6] * 5 + [0.0]) == pytest.approx(0.01)   # zero $vol day dropped
    assert not np.isnan(lf.amihud_illiq([0.01, np.nan] + [0.01] * 5, [1e6] * 7))
    assert lf.amihud_illiq([-0.02] * 5, [1e6] * 5) == pytest.approx(0.02)       # |return|


def test_zero_return_turnover_scores():
    assert lf.zero_return_frac([0, 1e-5, 0.01, np.nan]) == pytest.approx(2 / 3)
    assert np.isnan(lf.zero_return_frac([np.nan])) and lf.zero_return_frac([1e-4]) == 0.0
    assert lf.turnover([10, 20, np.nan], 100) == 0.3 and np.isnan(lf.turnover([1], 0))
    s = pd.Series([1.0, 2.0, 3.0, 4.0])
    assert list(lf.capacity_score(s)) == [75.0, 50.0, 25.0, 0.0]
    assert list(lf.illiq_pctile(s)) == [25.0, 50.0, 75.0, 100.0]


def test_premium_by_illiq():
    rng = np.random.default_rng(0)
    ill = rng.uniform(0, 1, 100)
    panel = pd.DataFrame({"illiq": ill, "fwd_ret": ill * 0.1})
    c = lf.premium_by_illiq(panel)
    assert list(c.columns) == ["bucket", "mean_fwd%", "median_fwd%", "n"] and c["mean_fwd%"].is_monotonic_increasing
    assert lf.monotonicity(c) == pytest.approx(1.0)
    assert lf.premium_by_illiq(panel.head(14)).empty and not lf.premium_by_illiq(panel.head(15)).empty


@pytest.fixture
def liqmkt(monkeypatch):
    n = 300
    rng = np.random.default_rng(1)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    cols = [f"S{i}" for i in range(8)]
    close = pd.DataFrame({c: 50 * np.cumprod(1 + rng.normal(0.0005, 0.01 + 0.004 * i, n)) for i, c in enumerate(cols)}, index=idx)
    vol = pd.DataFrame({c: 1e6 / (1 + i) for i, c in enumerate(cols)}, index=idx)
    close.iloc[:200, 7] = np.nan                                         # S7 too short for lookback+horizon+5
    monkeypatch.setattr(lf, "_market_wide", lambda m: (None, None) if m == "NONE" else (close, vol))
    return close, vol


def test_liquidity_scan_market(liqmkt):
    panel, scores = lf.scan_market("US")
    assert set(panel["ticker"]) == {f"S{i}" for i in range(7)} and set(panel.columns) == {"market", "ticker", "illiq", "fwd_ret"}
    close = liqmkt[0]
    T = len(close) - 21 - 1
    r = panel.set_index("ticker").loc["S2"]
    assert r["fwd_ret"] == pytest.approx(close["S2"].iloc[T + 21] / close["S2"].iloc[T] - 1)
    assert set(scores.columns) == {"market", "ticker", "illiq", "capacity_score", "illiq_pctile"}
    s = scores.set_index("ticker")
    assert s.loc["S6", "illiq"] > s.loc["S0", "illiq"] and s.loc["S0", "capacity_score"] > s.loc["S6", "capacity_score"]
    p, sc = lf.scan_market("NONE")
    assert p.empty and sc.empty


def test_liquidity_scan_skips_short_lookback_or_horizon(liqmkt):
    p, s = lf.scan_market("US", lookback=290, horizon=21)
    assert p.empty and s.empty                                              # no name has 290+21+5 bars


def test_liquidity_main(liqmkt, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(marketdata, "market_list", lambda: ["US"])
    out = str(tmp_path / "liq.csv")
    monkeypatch.setattr(sys, "argv", ["lf", "--out", out, "--top", "2"])
    lf.main()
    o = capsys.readouterr()
    assert "LIQUIDITY PREMIUM — US" in o.out and "MOST liquid:" in o.out and "LEAST liquid:" in o.out
    assert list(pd.read_csv(out).columns) == ["ticker", "liquidity_score"]
    monkeypatch.setattr(sys, "argv", ["lf", "--market", "NONE"])
    with pytest.raises(SystemExit, match="no liquidity panel"):
        lf.main()


def test_liquidity_main_verdicts(monkeypatch, capsys):
    ill = np.linspace(0.1, 1, 60)
    pan = pd.DataFrame({"illiq": ill, "fwd_ret": ill * 0.1})
    monkeypatch.setattr(marketdata, "market_list", lambda: ["US"])
    monkeypatch.setattr(lf, "scan_market", lambda m, l, h: (pan, pd.DataFrame()))
    monkeypatch.setattr(sys, "argv", ["lf"])
    lf.main()
    assert "liquidity premium PRESENT" in capsys.readouterr().out
    monkeypatch.setattr(lf, "scan_market", lambda m, l, h: (pan.assign(fwd_ret=-pan["fwd_ret"]), pd.DataFrame()))
    lf.main()
    assert "weak/absent" in capsys.readouterr().out


# ───────────────────────────── earnings_liquidity ─────────────────────────────
def test_directional_drift():
    assert el.directional_drift(0.05, 0.1) == 0.1 and el.directional_drift(-0.05, 0.1) == -0.1
    assert np.isnan(el.directional_drift(0.0, 0.1)) and np.isnan(el.directional_drift(0.1, float("nan")))
    assert np.isnan(el.directional_drift(float("inf"), 1.0))


def test_bucket_stats_and_spread():
    rng = np.random.default_rng(0)
    x = rng.uniform(0, 1, 100)
    p = pd.DataFrame({"illiq": x, "dir_drift": x * 0.2})
    t = el.bucket_stats(p, "illiq", "dir_drift")
    assert list(t.columns) == ["bucket", "dir_drift_mean%", "dir_drift_med%", "n"] and t["n"].sum() == 100
    assert el.spread_qhigh_qlow(t, "dir_drift_med%") == round(t["dir_drift_med%"].iloc[-1] - t["dir_drift_med%"].iloc[0], 2) > 0
    assert el.bucket_stats(p.head(14), "illiq", "dir_drift").empty and not el.bucket_stats(p.head(15), "illiq", "dir_drift").empty
    assert np.isnan(el.spread_qhigh_qlow(pd.DataFrame(), "x"))
    t3 = el.bucket_stats(p, "illiq", "dir_drift", q=3, labels=["lo", "mid", "hi"])
    assert list(t3["bucket"]) == ["lo", "mid", "hi"]


def _panel(n=60, m="US", seed=0, corr=True):
    rng = np.random.default_rng(seed)
    ill = rng.uniform(0, 1, n)
    return pd.DataFrame({"market": m, "illiq": ill, "dollar_vol": rng.uniform(1e5, 1e7, n), "price": rng.uniform(5, 100, n),
                         "vol_surge": rng.uniform(2, 6, n), "dir_drift": (ill * 0.2 if corr else -ill * 0.2) + rng.normal(0, 0.01, n)})


def test_per_market_summary():
    s = el.per_market_summary({"US": _panel(60, "US"), "JP": _panel(60, "JP", seed=1, corr=False), "KR": _panel(39, "KR")})
    assert list(s["market"]) == ["US", "JP"] and s["illiq_IC"].iloc[0] > 0 > s["illiq_IC"].iloc[1]
    assert set(s.columns) == {"market", "events", "vol_surge×", "illiq_IC", "vol_IC", "price_IC"}
    assert el.per_market_summary({"US": _panel(60), "XX": pd.DataFrame()}).shape[0] == 1
    assert el.per_market_summary({"KR": _panel(39)}).empty
    big = _panel(60); big.loc[:19, "dir_drift"] = 5.0                              # |drift| >= 2 filtered before counting
    assert el.per_market_summary({"US": big})["events"].iloc[0] == 40          # exactly min_events kept
    big.loc[20, "dir_drift"] = 5.0
    assert el.per_market_summary({"US": big}, min_events=40).empty
    assert el.per_market_summary({"US": big}).empty                              # 39 left < 40
    assert el.per_market_summary({"US": _panel(40)}, min_events=40)["events"].iloc[0] == 40


def test_parse_submissions():
    p = {"filings": {"recent": {"form": ["10-Q", "8-K", "10-K", "10-Q", "10-Q"],
                                "filingDate": ["2024-05-01", "2024-05-02", "2024-02-01", "2024-05-01", "2023-11-01"]}}}
    assert el.parse_submissions(p) == ["2023-11-01", "2024-02-01", "2024-05-01"]
    assert el.parse_submissions(p, forms=("8-K",)) == ["2024-05-02"]
    assert el.parse_submissions({}) == [] and el.parse_submissions({"filings": {"recent": {"form": ["10-K"]}}}) == []


def test_cik_map_cached_and_error(monkeypatch):
    monkeypatch.setattr(el, "_CIK", None)
    m = types.ModuleType("pit_fundamentals"); calls = []
    m._ticker_cik = lambda: (calls.append(1), {"AAPL": 320193})[1]
    monkeypatch.setitem(sys.modules, "pit_fundamentals", m)
    assert el._cik_map() == {"AAPL": 320193} and el._cik_map() == {"AAPL": 320193} and calls == [1]
    monkeypatch.setattr(el, "_CIK", None)
    m._ticker_cik = lambda: (_ for _ in ()).throw(RuntimeError("offline"))
    assert el._cik_map() == {}


def _install_http(monkeypatch, fn):
    ac = types.ModuleType("apiclient"); ac.http_get = fn
    monkeypatch.setitem(sys.modules, "apiclient", ac)


def test_fetch_earnings_dates_edgar(monkeypatch):
    monkeypatch.setattr(el, "_CIK", {"AAPL": 320193})
    payload = {"filings": {"recent": {"form": ["10-Q", "10-K"], "filingDate": ["2019-01-01", "2024-01-01"]}}}
    seen = []
    _install_http(monkeypatch, lambda src, url, headers=None, retries=None: (
        seen.append((src, url, retries)) or types.SimpleNamespace(status_code=200, json=lambda: payload)))
    assert el.fetch_earnings_dates_edgar("aapl", since="2020-01-01") == ["2024-01-01"]
    assert seen[0][1] == "https://data.sec.gov/submissions/CIK0000320193.json" and seen[0][0] == "edgar" and seen[0][2] == 2
    assert el.fetch_earnings_dates_edgar("MISSING") == []
    _install_http(monkeypatch, lambda *a, **k: types.SimpleNamespace(status_code=500))
    assert el.fetch_earnings_dates_edgar("AAPL") == []
    _install_http(monkeypatch, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert el.fetch_earnings_dates_edgar("AAPL") == []


def _wide_dict(close, vol):
    return {"Close": close, "High": close, "Low": close, "Volume": vol}


@pytest.fixture
def elmkt(monkeypatch):
    close, vol = event_market(n=320, k=6, events=(120, 200), jump=0.10)
    monkeypatch.setattr(el, "_wide", lambda m: None if m == "NONE" else _wide_dict(close, vol))
    import pead_factor
    monkeypatch.setattr(pead_factor, "_liquid_symbols", lambda c, v: list(c.columns))
    return close, vol


def test_el_scan_market(elmkt):
    p = el.scan_market("US")
    assert len(p) > 0 and set(p.columns) == {"market", "ticker", "price", "illiq", "dollar_vol", "vol_surge", "surprise", "fwd_car", "dir_drift"}
    assert (p["vol_surge"] > 3).all() and (p["surprise"].abs() <= pf.SURPRISE_CAP).all()
    assert el.scan_market("NONE").empty
    assert el.scan_market("US", horizon=300).empty                             # not enough history


def test_el_scan_us_edgar(elmkt, monkeypatch):
    close, vol = elmkt
    dates = [str(close.index[120].date()), str(close.index[200].date()), str(close.index[319].date())]
    monkeypatch.setattr(el, "fetch_earnings_dates_edgar", lambda s, since: dates if s != "S3" else [])
    p = el.scan_us_edgar(["S0", "S1", "S3", "NOPE"])
    assert set(p["ticker"]) == {"S0", "S1"}                                    # S3 has no dates, NOPE not in frame
    assert (p["date"].isin(dates[:2])).all()                                   # last date lacks a full drift window
    assert p["market"].eq("US").all() and p["dir_drift"].notna().all()
    assert el.scan_us_edgar(["S0"], horizon=500).empty
    monkeypatch.setattr(el, "_wide", lambda m: None)
    assert el.scan_us_edgar(["S0"]).empty


def _main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["el", *argv])
    el.main()


def test_el_main_default_study(monkeypatch, capsys):
    monkeypatch.setattr(marketdata, "market_list", lambda: ["US"])
    monkeypatch.setattr(el, "scan_market", lambda m, h: _panel(80, m))
    _main(monkeypatch, "--horizon", "30")
    out = capsys.readouterr().out
    assert "PEAD conditioning study" in out and "US  ·  80 announcement-proxy events  ·  30d drift" in out
    assert "by Amihud ILLIQUIDITY" in out and "by dollar VOLUME" in out and "by PRICE level" in out
    assert "Hypothesis (Chordia-Sadka)" in out and "median" in out
    monkeypatch.setattr(el, "scan_market", lambda m, h: pd.DataFrame())
    with pytest.raises(SystemExit, match="no earnings-event panel"):
        _main(monkeypatch)


def test_el_main_by_market(monkeypatch, capsys):
    monkeypatch.setattr(marketdata, "market_list", lambda: ["US", "JP"])
    data = {"US": _panel(60, "US"), "JP": _panel(60, "JP", seed=2, corr=False)}
    monkeypatch.setattr(el, "scan_market", lambda m, h: data[m])
    _main(monkeypatch, "--by-market")
    out = capsys.readouterr().out
    assert "PER-COUNTRY test (2 markets)" in out and "1/2 markets show the Chordia-Sadka sign" in out and "strongest: US, JP" in out
    monkeypatch.setattr(el, "scan_market", lambda m, h: _panel(10, m))
    _main(monkeypatch, "--by-market")
    assert "insufficient events per market" in capsys.readouterr().out


def test_el_main_edgar(monkeypatch, capsys):
    close, vol = event_market(n=300, k=4)
    monkeypatch.setattr(el, "_wide", lambda m: _wide_dict(close, vol))
    import pead_factor
    monkeypatch.setattr(pead_factor, "_liquid_symbols", lambda c, v: list(c.columns))
    seen = {}
    monkeypatch.setattr(el, "scan_us_edgar", lambda liquid, h: (seen.update(l=list(liquid), h=h), _panel(60))[1])
    _main(monkeypatch, "--edgar", "--limit", "3", "--horizon", "25")
    out = capsys.readouterr()
    assert seen == {"l": ["S0", "S1", "S2"], "h": 25} and "fetching EDGAR 10-Q/10-K filing dates for 3 liquid US names" in out.err
    assert "REAL EDGAR filing dates (60 10-Q/10-K events)" in out.out and "illiq_IC (real dates)" in out.out and "Q5−Q1" in out.out
    monkeypatch.setattr(el, "scan_us_edgar", lambda liquid, h: pd.DataFrame())
    _main(monkeypatch, "--edgar")
    assert "no EDGAR-dated events" in capsys.readouterr().out
