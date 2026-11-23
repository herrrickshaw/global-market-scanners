"""nse_data_fetcher.py: NSE data layer. nsepython/yfinance/nse are replaced by in-memory fakes, so only the
parsing, caching, retry, regime and scoring logic runs."""
import importlib
import json
import os
import sys
import types
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nse_data_fetcher as nd  # noqa: E402


class FakeYF:
    def __init__(self):
        self.calls, self.fail, self.frame = [], [], None

    def download(self, t, **kw):
        self.calls.append((t, kw))
        if self.fail:
            raise self.fail.pop(0)
        return self.frame if self.frame is not None else pd.DataFrame()


@pytest.fixture
def f(monkeypatch, tmp_path):
    yf = FakeYF()
    monkeypatch.setattr(nd, "yf", yf, raising=False)
    monkeypatch.setattr(nd, "_YF_OK", True)
    monkeypatch.setattr(nd, "_NSEPY_OK", True)
    monkeypatch.setattr(nd, "_NSEPY_LIB_OK", False)
    monkeypatch.setattr(nd, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(nd.time, "sleep", lambda s: None)
    for name in ("nse_eq_symbols", "indiavix", "get_bulkdeals", "get_blockdeals", "nse_events", "nse_get_index_quote",
                 "nse_fiidii", "fnolist"):
        monkeypatch.setattr(nd, name, lambda *a, **k: None, raising=False)
    fx = nd.NSEDataFetcher(cache_ttl_sec=3600)
    fx.yf = yf
    return fx


def _px(n=30, start=100.0, step=1.0):
    idx = pd.bdate_range(end="2026-11-20", periods=n)
    c = start + step * np.arange(n)
    return pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": 100.0}, index=idx)


def _multi(tick_frames):
    return pd.concat({fld: pd.concat({t: d[fld] for t, d in tick_frames.items()}, axis=1)
                      for fld in ["Open", "High", "Low", "Close", "Volume"]}, axis=1)


def test_cache_ttl(f, monkeypatch):
    assert f._get_cache("k") is None
    f._set_cache("k", [1])
    assert f._get_cache("k") == [1]
    t0 = f._cache["k"]["ts"]
    monkeypatch.setattr(nd.time, "time", lambda: t0 + 3599)
    assert f._get_cache("k") == [1]
    monkeypatch.setattr(nd.time, "time", lambda: t0 + 3601)
    assert f._get_cache("k") is None


def test_get_all_symbols_paths(f, monkeypatch, capsys):
    syms = [f"S{i}" for i in range(150)]
    monkeypatch.setattr(nd, "nse_eq_symbols", lambda: syms)
    assert f.get_all_symbols() == syms and "150 NSE EQ symbols" in capsys.readouterr().out
    monkeypatch.setattr(nd, "nse_eq_symbols", lambda: (_ for _ in ()).throw(AssertionError("cached")))
    assert f.get_all_symbols() == syms                                          # served from the cache


def test_get_all_symbols_falls_back_to_bhavcopy_then_nifty50(f, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(nd, "nse_eq_symbols", lambda: ["only", "few"])         # < 100 -> rejected
    csv = tmp_path / "b.csv"
    csv.write_text("SctySrs,TckrSymb\nEQ, ABC \nEQ,XYZ\nBE,QQQ\nEQ,\n")
    calls = []

    class FakeNSE:
        def __init__(self, download_folder, server):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def equityBhavcopy(self, d):
            calls.append(d)
            if len(calls) < 3:
                raise RuntimeError("holiday")
            return csv
    monkeypatch.setitem(sys.modules, "nse", types.SimpleNamespace(NSE=FakeNSE))
    assert f.get_all_symbols() == ["ABC", "XYZ"] and len(calls) == 3
    assert "nse bhavcopy" in capsys.readouterr().out
    f2 = nd.NSEDataFetcher()
    monkeypatch.setitem(sys.modules, "nse", None)                              # ImportError -> Nifty 50 fallback
    monkeypatch.setattr(nd, "_NSEPY_OK", False)
    out = f2.get_all_symbols()
    assert len(out) == 50 and "RELIANCE" in out and "Nifty 50 fallback" in capsys.readouterr().out


def test_get_all_symbols_bhavcopy_without_series_column(f, monkeypatch, tmp_path):
    monkeypatch.setattr(nd, "_NSEPY_OK", False)
    csv = tmp_path / "b.csv"; csv.write_text("A,B\n1,2\n")

    class FakeNSE:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def equityBhavcopy(self, d): return csv
    monkeypatch.setitem(sys.modules, "nse", types.SimpleNamespace(NSE=FakeNSE))
    assert len(f.get_all_symbols()) == 50                                       # no SctySrs -> fallback list


def test_get_fno_symbols(f, monkeypatch):
    monkeypatch.setattr(nd, "fnolist", lambda: ["A", "B"])
    assert f.get_fno_symbols() == ["A", "B"]
    monkeypatch.setattr(nd, "fnolist", lambda: pd.Series(["X"]))
    assert f.get_fno_symbols() == ["X"]
    monkeypatch.setattr(nd, "fnolist", lambda: "bad")
    assert f.get_fno_symbols() == []
    monkeypatch.setattr(nd, "fnolist", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert f.get_fno_symbols() == []
    monkeypatch.setattr(nd, "_NSEPY_OK", False)
    assert f.get_fno_symbols() == []


def _regime_setup(f, monkeypatch, last=21000.0, dma_target=20000, vix=14.0, fii=None, quote=True):
    n = 250
    idx = pd.bdate_range(end="2026-11-20", periods=n)
    close = np.full(n, float(dma_target)); close[-1] = last
    f.yf.frame = _multi({"^NSEI": pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1.0}, index=idx)})
    monkeypatch.setattr(nd, "nse_get_index_quote", lambda s: {"last": "21000", "yearHigh": "22000", "yearLow": "17000"} if quote else None)
    monkeypatch.setattr(nd, "indiavix", lambda: vix)
    monkeypatch.setattr(nd, "nse_fiidii", lambda: fii)


def test_regime_classification(f, monkeypatch):
    _regime_setup(f, monkeypatch, last=20010.0)
    assert f.get_regime()["regime"] == "SIDEWAYS"                               # within 1.5% of the 200DMA
    _regime_setup(f, monkeypatch, last=21500.0, vix=14.0)
    r = f.get_regime()
    assert r["regime"] == "BULL" and r["pct_from_dma200"] > 1.5 and r["vix"] == 14.0 and r["nifty_52w_h"] == 22000.0 and r["pct_from_52wh"] == -4.55
    _regime_setup(f, monkeypatch, last=21500.0, vix=18.0)
    assert f.get_regime()["regime"] == "BULL_VOLATILE"                           # vix >= 18
    _regime_setup(f, monkeypatch, last=19000.0, vix=20.0)
    assert f.get_regime()["regime"] == "BEAR"
    _regime_setup(f, monkeypatch, last=19000.0, vix=26.0)
    assert f.get_regime()["regime"] == "BEAR_EXTREME"
    _regime_setup(f, monkeypatch, last=19000.0, vix=25.0)
    assert f.get_regime()["regime"] == "BEAR"                                    # > 25 strictly
    _regime_setup(f, monkeypatch, last=21500.0, vix=None)
    assert f.get_regime()["regime"] == "BULL"                                    # unknown vix defaults to 15


def test_regime_fii_and_fallbacks(f, monkeypatch):
    fii = pd.DataFrame({"FII Net": [2e7, 1e7, 1e7, 1e7, 1e7, -9e9], "DII Net": [1.0] * 6})
    _regime_setup(f, monkeypatch, fii=fii)
    r = f.get_regime()
    assert r["fii_sentiment"] == "BUYING" and r["fii_net_cr"] == 6.0               # first 5 rows only, /1e7
    _regime_setup(f, monkeypatch, fii=pd.DataFrame({"net_flow": [-5.0, -1.0]}))
    r = f.get_regime()
    assert r["fii_sentiment"] == "SELLING" and r["fii_net_cr"] == -6.0               # small sums are not rescaled
    _regime_setup(f, monkeypatch, fii=pd.DataFrame({"x": [1]}))
    assert f.get_regime()["fii_sentiment"] == "UNKNOWN"
    _regime_setup(f, monkeypatch, quote=False)
    r = f.get_regime()
    assert r["nifty_last"] == 21000.0 and r["nifty_52w_h"] is None                  # falls back to the yfinance last close
    monkeypatch.setattr(nd, "nse_get_index_quote", lambda s: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(nd, "indiavix", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(nd, "nse_fiidii", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    f.yf.fail = [RuntimeError("down")]
    r = f.get_regime()
    assert r["regime"] == "SIDEWAYS" and r["vix"] is None and r["nifty_last"] is None
    monkeypatch.setattr(nd, "_NSEPY_OK", False); monkeypatch.setattr(nd, "_YF_OK", False)
    assert f.get_regime()["regime"] == "SIDEWAYS"


def test_get_ohlc_yfinance_and_nsepy(f, monkeypatch, capsys):
    f.yf.frame = _multi({"TCS.NS": _px()})
    out = f.get_ohlc("TCS", period="6mo")
    assert list(out.columns) == ["Open", "High", "Low", "Close", "Volume"] and len(out) == 30
    assert f.yf.calls[0] == ("TCS.NS", {"auto_adjust": True, "progress": False, "period": "6mo"})
    f.get_ohlc("TCS", start=datetime(2026, 1, 1).date(), end=datetime(2026, 2, 1).date())
    assert f.yf.calls[1][1]["start"] and "period" not in f.yf.calls[1][1]
    f.yf.frame = pd.DataFrame()
    assert f.get_ohlc("X").empty
    f.yf.fail = [RuntimeError("net")]
    assert f.get_ohlc("X").empty and "yfinance failed for X: net" in capsys.readouterr().out
    monkeypatch.setattr(nd, "_NSEPY_LIB_OK", True)
    monkeypatch.setattr(nd, "_nsepy_get_history", lambda symbol, start, end: _px().sort_index(ascending=False), raising=False)
    s, e = datetime(2026, 1, 1).date(), datetime(2026, 2, 1).date()
    before = len(f.yf.calls)
    got = f.get_ohlc("TCS", start=s, end=e)
    assert got.index.is_monotonic_increasing and len(got) == 30 and len(f.yf.calls) == before      # nsepy served it
    n = len(f.yf.calls)
    monkeypatch.setattr(nd, "_nsepy_get_history", lambda **k: (_ for _ in ()).throw(RuntimeError("ssl")), raising=False)
    f.yf.frame = _multi({"TCS.NS": _px()})
    assert len(f.get_ohlc("TCS", start=s, end=e)) == 30 and len(f.yf.calls) == n + 1     # falls through to yfinance
    monkeypatch.setattr(nd, "_nsepy_get_history", lambda **k: pd.DataFrame(), raising=False)
    assert len(f.get_ohlc("TCS", start=s, end=e)) == 30
    monkeypatch.setattr(nd, "_YF_OK", False); monkeypatch.setattr(nd, "_NSEPY_LIB_OK", False)
    assert f.get_ohlc("TCS").empty


def test_get_ohlc_bulk(f, monkeypatch, capsys):
    f.yf.frame = _multi({"A.NS": _px(40), "B.NS": _px(40), "C.NS": _px(10)})
    out = f.get_ohlc_bulk(["A", "B", "C", "D"], period="2y", batch_size=100)
    assert set(out) == {"A", "B"}                                              # C < 30 bars, D absent
    assert "OK (2 usable)" in capsys.readouterr().out and f.yf.calls[0][1]["period"] == "2y"
    f.yf.calls.clear()
    f.get_ohlc_bulk(["A", "B", "C"], batch_size=2)
    assert [len(c[0]) for c in f.yf.calls] == [2, 1]
    f.yf.frame = _px(40)                                                        # flat columns -> first symbol of the batch
    assert set(f.get_ohlc_bulk(["Q"])) == {"Q"}
    f.yf.frame = pd.DataFrame()
    assert f.get_ohlc_bulk(["Q"]) == {} and "empty" in capsys.readouterr().out
    sleeps = []
    monkeypatch.setattr(nd.time, "sleep", lambda s: sleeps.append(s))
    f.yf.frame = _px(40); f.yf.fail = [RuntimeError("429 Too Many"), RuntimeError("Rate limit")]
    assert "Q" in f.get_ohlc_bulk(["Q"]) and sleeps == [30, 60]
    f.yf.fail = [ValueError("weird")]
    assert f.get_ohlc_bulk(["Q"]) == {} and "ERROR: weird" in capsys.readouterr().out


def _events():
    today = datetime.today()
    fmt = lambda d: d.strftime("%d-%b-%Y")
    return pd.DataFrame({"symbol": ["A", "B", "C", "D"], "company": ["a", "b", "c", "d"],
                         "purpose": ["Quarterly Results", "Dividend", "Financial Result", "AGM"],
                         "date": [fmt(today + timedelta(days=2)), fmt(today + timedelta(days=10)),
                                  fmt(today + timedelta(days=5)), "garbage"]})


def test_events_and_results_calendar(f, monkeypatch, capsys):
    monkeypatch.setattr(nd, "nse_events", lambda: _events())
    ev = f.get_upcoming_events(days_ahead=7)
    assert list(ev["symbol"]) == ["A", "C"] and list(ev.columns) == ["symbol", "company", "purpose", "date", "days_away"]
    assert ev["days_away"].between(0, 5).all() and ev["days_away"].is_monotonic_increasing
    assert list(f.get_upcoming_events(days_ahead=30)["symbol"]) == ["A", "C", "B"]
    assert list(f.get_results_calendar(30)["symbol"]) == ["A", "C"]
    monkeypatch.setattr(nd, "nse_events", lambda: pd.DataFrame())
    assert f.get_upcoming_events().empty and f.get_results_calendar().empty
    monkeypatch.setattr(nd, "nse_events", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert f.get_upcoming_events().empty and "nse_events error: x" in capsys.readouterr().out
    monkeypatch.setattr(nd, "_NSEPY_OK", False)
    assert f.get_upcoming_events().empty


def test_deal_and_fii_wrappers(f, monkeypatch, capsys):
    df = pd.DataFrame({"a": [1]})
    for name, meth in (("get_bulkdeals", f.get_bulk_deals), ("get_blockdeals", f.get_block_deals)):
        monkeypatch.setattr(nd, name, lambda df=df: df)
        assert meth() is df
        monkeypatch.setattr(nd, name, lambda: pd.DataFrame())
        assert meth().empty
        monkeypatch.setattr(nd, name, lambda: (_ for _ in ()).throw(RuntimeError("bad")))
        assert meth().empty and f"{name} error: bad" in capsys.readouterr().out
    monkeypatch.setattr(nd, "nse_fiidii", lambda: df)
    assert f.get_fii_dii() is df
    monkeypatch.setattr(nd, "nse_fiidii", lambda: "str")
    assert f.get_fii_dii().empty
    monkeypatch.setattr(nd, "nse_fiidii", lambda: (_ for _ in ()).throw(RuntimeError("bad")))
    assert f.get_fii_dii().empty and "nse_fiidii error" in capsys.readouterr().out
    monkeypatch.setattr(nd, "_NSEPY_OK", False)
    assert f.get_bulk_deals().empty and f.get_block_deals().empty and f.get_fii_dii().empty


def test_institutional_score(f, monkeypatch):
    fii = pd.DataFrame({"FII Net": [3e7] * 5, "DII Net": [1e7] * 5})
    deals = pd.DataFrame({"Buy/Sell": ["buy", "BUY", "Buy", "sell", "BUY"]})
    monkeypatch.setattr(f, "get_fii_dii", lambda: fii)
    monkeypatch.setattr(f, "get_bulk_deals", lambda: deals)
    r = f.get_institutional_activity_score()
    assert r["score"] == 3 and r["label"] == "STRONG_BUY" and r["fii_5d_net_cr"] == 15.0 and r["dii_5d_net_cr"] == 5.0
    assert r["bulk_buys"] == 4 and r["bulk_sells"] == 1 and r["bulk_buy_ratio"] == 80.0
    monkeypatch.setattr(f, "get_fii_dii", lambda: pd.DataFrame({"FII Net": [-5.0] * 5, "DII Net": [2.0] * 5}))
    monkeypatch.setattr(f, "get_bulk_deals", lambda: pd.DataFrame({"Buy/Sell": ["sell", "sell", "buy"]}))
    r = f.get_institutional_activity_score()
    assert r["score"] == -1 and r["label"] == "SELL" and r["fii_5d_net_cr"] == -25.0       # small magnitudes unscaled
    monkeypatch.setattr(f, "get_fii_dii", lambda: pd.DataFrame({"FII Net": [-5.0], "DII Net": [-2.0]}))
    monkeypatch.setattr(f, "get_bulk_deals", lambda: pd.DataFrame({"Buy/Sell": ["sell", "sell"]}))
    r = f.get_institutional_activity_score()
    assert r["score"] == -3 and r["label"] == "STRONG_SELL"
    monkeypatch.setattr(f, "get_fii_dii", lambda: pd.DataFrame())
    monkeypatch.setattr(f, "get_bulk_deals", lambda: pd.DataFrame({"Buy/Sell": ["buy", "sell"]}))
    r = f.get_institutional_activity_score()
    assert r["score"] == 0 and r["label"] == "NEUTRAL"                                      # ratio 0.5 -> neutral
    monkeypatch.setattr(f, "get_bulk_deals", lambda: pd.DataFrame({"Buy/Sell": ["buy", "buy", "sell", "sell", "buy"]}))
    assert f.get_institutional_activity_score()["score"] == 0                               # 0.6 is not > 0.6
    monkeypatch.setattr(f, "get_bulk_deals", lambda: pd.DataFrame({"Buy/Sell": ["buy", "buy", "sell", "sell", "sell"]}))
    assert f.get_institutional_activity_score()["score"] == 0                               # 0.4 is not < 0.4
    monkeypatch.setattr(f, "get_fii_dii", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(f, "get_bulk_deals", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert f.get_institutional_activity_score() == {"score": 0, "label": "NEUTRAL"}


def test_market_breadth(f, monkeypatch):
    monkeypatch.setattr(nd, "nse_get_index_quote", lambda s: {"last": 21000, "yearHigh": 22000, "yearLow": 18000})
    for vix, label in [(31, "PANIC"), (30, "HIGH_FEAR"), (23, "HIGH_FEAR"), (22, "ELEVATED"), (18, "ELEVATED"), (17, "NORMAL"),
                       (13, "NORMAL"), (12, "COMPLACENCY")]:
        monkeypatch.setattr(nd, "indiavix", lambda v=vix: v)
        assert f.get_market_breadth()["vix_regime"] == label, vix
    b = f.get_market_breadth()
    assert b["pct_from_52wh"] == -4.55 and b["pct_from_52wl"] == 16.67 and b["nifty_52w_high"] == 22000.0
    monkeypatch.setattr(nd, "indiavix", lambda: None)
    assert f.get_market_breadth()["vix"] is None and f.get_market_breadth()["vix_regime"] == "NORMAL"
    monkeypatch.setattr(nd, "indiavix", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert f.get_market_breadth() == {"vix": None, "vix_regime": "NORMAL", "nifty_52w_pct": None}
    monkeypatch.setattr(nd, "_NSEPY_OK", False)
    assert f.get_market_breadth()["vix"] is None


def test_nifty_pe_pb(f, monkeypatch, tmp_path):
    none = {"pe": None, "pb": None, "div_yield": None, "date": None}
    monkeypatch.setattr(nd, "_NSEPY_OK", False)
    assert f.get_nifty_pe_pb() == none
    monkeypatch.setattr(nd, "_NSEPY_OK", True)
    idx = pd.to_datetime(["2026-11-18", "2026-11-19"])
    seen = []
    mod = types.ModuleType("nsepython")
    mod.index_pe_pb_div = lambda name, a, b: (seen.append((name, a, b)), pd.DataFrame({"PE": [20.0, 22.5], "PB": [3.0, 3.5], "DivYield": [1.1, 1.2]}, index=idx))[1]
    monkeypatch.setitem(sys.modules, "nsepython", mod)
    r = f.get_nifty_pe_pb()
    assert r == {"pe": 22.5, "pb": 3.5, "div_yield": 1.2, "date": str(idx[-1])} and seen[0][0] == "NIFTY 50"
    assert json.load(open(tmp_path / "nifty_pe_pb.json")) == r
    mod.index_pe_pb_div = lambda *a: (_ for _ in ()).throw(AssertionError("cache must be used"))
    assert f.get_nifty_pe_pb() == r
    old = datetime.now().timestamp() - 7200
    os.utime(tmp_path / "nifty_pe_pb.json", (old, old))                                      # stale cache -> refetch -> error -> default
    assert f.get_nifty_pe_pb() == none
    mod.index_pe_pb_div = lambda *a: pd.DataFrame()
    assert f.get_nifty_pe_pb() == none


def test_strategy_recommendations():
    rec = nd._get_strategy_recommendation
    assert "BULL MARKET" in rec("BULL", 14, 0)[0] and len(rec("BULL", 14, 0)) == 4
    assert any("PANIC" in r for r in rec("BEAR_EXTREME", 26, 0)) and not any("PANIC" in r for r in rec("BEAR_EXTREME", 25, 0))
    assert any("ELEVATED" in r for r in rec("BULL_VOLATILE", 19, 0)) and not any("ELEVATED" in r for r in rec("BULL_VOLATILE", 18, 0))
    assert any("COMPLACENCY" in r for r in rec("SIDEWAYS", 11, 0)) and not any("COMPLACENCY" in r for r in rec("SIDEWAYS", 12, 0))
    assert any("Institutional buying" in r for r in rec("BULL", 14, 2)) and not any("Institutional" in r for r in rec("BULL", 14, 1))
    assert any("Institutional selling" in r for r in rec("BEAR", 14, -2)) and not any("Institutional" in r for r in rec("BEAR", 14, -1))
    assert rec("WEIRD", 14, 0) == ["⚠️  Unknown regime — wait for clarity"]
    assert rec("BEAR", None, 0)[0].startswith("📉")


def test_live_context_and_print(f, monkeypatch, capsys):
    monkeypatch.setattr(f, "get_regime", lambda: {"regime": "BULL", "vix": 14.0, "nifty_last": 21000.0, "dma200": 20000.0,
                                                  "pct_from_dma200": 5.0, "pct_from_52wh": -4.5, "nifty_52w_low": 17000.0,
                                                  "nifty_52w_high": 22000.0, "fii_sentiment": "BUYING", "fii_net_cr": 12.0})
    monkeypatch.setattr(f, "get_market_breadth", lambda: {"vix": 99, "vix_regime": "NORMAL", "extra": 1})
    monkeypatch.setattr(f, "get_institutional_activity_score", lambda: {"score": 2, "label": "BUY", "bulk_buy_ratio": 70.0})
    monkeypatch.setattr(f, "get_results_calendar", lambda days_ahead: pd.DataFrame({"symbol": list("ABCDEFGHIJ")}))
    ctx = f.get_live_context()
    assert ctx["vix"] == 14.0 and ctx["extra"] == 1 and ctx["vix_regime"] == "NORMAL"          # regime keys win over breadth
    assert ctx["institutional_score"] == 2 and ctx["results_count_7d"] == 10 and any("Institutional buying" in r for r in ctx["recommendations"])
    f.print_live_context()
    out = capsys.readouterr().out
    assert "LIVE MARKET CONTEXT" in out and "BULL" in out and "Results next 7d: 10 stocks" in out and "..." in out
    assert "Bulk deal buy%: 70.0%" in out and "Low volatility" in out and "(score=+2)" in out
    monkeypatch.setattr(f, "get_regime", lambda: {"regime": "WEIRD", "vix": 23.0})
    monkeypatch.setattr(f, "get_results_calendar", lambda days_ahead: pd.DataFrame())
    monkeypatch.setattr(f, "get_institutional_activity_score", lambda: {})
    f.print_live_context()
    out = capsys.readouterr().out
    assert "HIGH FEAR" in out and "200 DMA:        —" in out and "Results next 7d: 0 stocks" in out
    monkeypatch.setattr(f, "get_regime", lambda: {"regime": "BEAR", "vix": 20.0})
    f.print_live_context()
    assert "Elevated — widen stop-losses" in capsys.readouterr().out


def test_module_helpers(f, monkeypatch):
    monkeypatch.setattr(nd, "_fetcher", None)
    a = nd.get_fetcher()
    assert nd.get_fetcher() is a and isinstance(a, nd.NSEDataFetcher)
    monkeypatch.setattr(a, "get_all_symbols", lambda: ["X"])
    monkeypatch.setattr(a, "get_regime", lambda: {"regime": "BULL"})
    monkeypatch.setattr(a, "get_results_calendar", lambda days_ahead: pd.DataFrame({"d": [days_ahead]}))
    assert nd.get_nse_symbols() == ["X"] and nd.get_live_regime() == {"regime": "BULL"} and nd.get_upcoming_results(3)["d"].iloc[0] == 3
    monkeypatch.setattr(a, "get_bulk_deals", lambda: pd.DataFrame({"Symbol": ["tcs", "INFY"]}))
    assert nd.get_institutional_confirmation("TCS") is True and nd.get_institutional_confirmation("wipro") is False
    monkeypatch.setattr(a, "get_bulk_deals", lambda: pd.DataFrame())
    assert nd.get_institutional_confirmation("TCS") is False
    monkeypatch.setattr(a, "get_bulk_deals", lambda: pd.DataFrame({"x": [1]}))
    assert nd.get_institutional_confirmation("TCS") is False
