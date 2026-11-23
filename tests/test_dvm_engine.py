"""dvm_engine.py: momentum/durability scoring, screens and the main() flow (offline)."""
import os
import sqlite3
import sys
import types

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dvm_engine as dv  # noqa: E402


def _ohlcv(n=300, slope=0.5, base=50.0, vol=1_000_000, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    c = base + slope * np.arange(n) + noise * rng.standard_normal(n)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c,
                         "Volume": np.full(n, float(vol))}, index=idx)


def test_momentum_requires_220_rows_and_not_none():
    assert np.isnan(dv.momentum_score(None))
    assert np.isnan(dv.momentum_score(_ohlcv(219, noise=1.0)))
    assert not np.isnan(dv.momentum_score(_ohlcv(260, noise=1.0)))


def test_momentum_uptrend_beats_downtrend_and_is_bounded():
    up = dv.momentum_score(_ohlcv(300, slope=0.5, noise=1.0))
    down = dv.momentum_score(_ohlcv(300, slope=-0.1, base=200, noise=1.0))
    assert 0 <= down < up <= 100


def test_momentum_zero_volume_does_not_crash():
    df = _ohlcv(300, slope=0.3, noise=1.0, vol=0)
    assert 0 <= dv.momentum_score(df) <= 100


def _fake_pit(monkeypatch, d, F=7):
    mod = types.ModuleType("pit_fundamentals")
    mod.as_of = lambda t, date: d
    mod.piotroski_asof = lambda t, date: (F, {})
    mod._g = lambda lst, i=0: lst[i] if lst and len(lst) > i else None
    monkeypatch.setitem(sys.modules, "pit_fundamentals", mod)
    return mod


def _fund(**kw):
    d = {"ni": [10.0], "roe_hist": [25.0, 21.0], "de": 0.5, "cfo": 10.0, "capex": 4.0,
         "rev": [120.0, 100.0], "shares": 10.0}
    d.update(kw)
    return d


def test_durability_none_without_facts(monkeypatch):
    _fake_pit(monkeypatch, {})
    assert dv.durability_score("X", "2026-01-01") == (None, {})
    _fake_pit(monkeypatch, _fund(ni=[]))
    assert dv.durability_score("X", "2026-01-01") == (None, {})


def test_durability_strong_company(monkeypatch):
    _fake_pit(monkeypatch, _fund(), F=9)
    s, det = dv.durability_score("X", "2026-01-01")
    # roe 23 ->90, de .5 ->85, fcf>0 ->80, rev up ->75, F 9/9 -> 100
    assert s == pytest.approx((90 + 85 + 80 + 75 + 100) / 5)
    assert det["piotroski"] == 9 and det["fcf_pos"] is True and det["roe"] == 23.0


def test_durability_weak_company_and_missing_piotroski(monkeypatch):
    d = _fund(roe_hist=[3.0], de=3.0, cfo=1.0, capex=5.0, rev=[90.0, 100.0])
    _fake_pit(monkeypatch, d, F=None)
    s, det = dv.durability_score("X", "2026-01-01")
    assert s == pytest.approx((15 + 20 + 20 + 35 + 50) / 5)
    assert det["fcf_pos"] is False


@pytest.mark.parametrize("roe,expected", [(21, 90), (20, 70), (16, 70), (15, 45), (9, 45), (8, 15)])
def test_durability_roe_bands(monkeypatch, roe, expected):
    _fake_pit(monkeypatch, _fund(roe_hist=[float(roe)], de=None, cfo=None, capex=None,
                                 rev=[1.0]), F=None)
    s, _ = dv.durability_score("X", "2026-01-01")
    # de None ->20, fcf None ->20, rev single ->35, F None ->50
    assert s == pytest.approx((expected + 20 + 20 + 35 + 50) / 5)


@pytest.mark.parametrize("de,expected", [(0.99, 85), (1.0, 50), (1.99, 50), (2.0, 20)])
def test_durability_de_bands(monkeypatch, de, expected):
    _fake_pit(monkeypatch, _fund(roe_hist=[], de=de, cfo=None, capex=None, rev=[1.0]), F=None)
    s, _ = dv.durability_score("X", "2026-01-01")
    assert s == pytest.approx((15 + expected + 20 + 35 + 50) / 5)


def test_screens_logic():
    S = dv.SCREENS
    r = {"D": 70, "V": 70, "M": 70}
    assert S["high_dvm"](r)
    assert S["high_dvm"]({"D": 70, "V": 70, "M": 55})        # avg exactly 65
    assert not S["high_dvm"]({"D": 70, "V": 70, "M": 54})    # avg 64.67
    assert not S["high_dvm"]({"D": None, "V": 70, "M": 99})
    assert S["durable_momentum"]({"D": 60, "V": None, "M": 60})
    assert not S["durable_momentum"]({"D": 59.9, "V": None, "M": 90})
    assert not S["durable_momentum"]({"D": None, "V": None, "M": 90})
    assert S["value_under_radar"]({"D": 55, "V": 55, "M": 49.9})
    assert not S["value_under_radar"]({"D": 55, "V": 55, "M": 50})
    assert S["momentum_breakout"]({"D": None, "V": None, "M": 75})
    assert not S["momentum_breakout"]({"D": None, "V": None, "M": 74.9})


def test_labels_cover_all_eight_codes():
    assert len(dv.LABELS) == 8
    assert dv.LABELS["GGG"] == "Strong Performer" and dv.LABELS["BBB"] == "Weak / Avoid"


def _fake_excel(monkeypatch, df):
    seen = {}

    class FakeXL:
        def __init__(self, path):
            seen["path"] = path

        def parse(self, sheet):
            seen["sheet"] = sheet
            return df

    monkeypatch.setattr(dv.pd, "ExcelFile", FakeXL)
    monkeypatch.setattr(dv.glob, "glob", lambda pat, recursive=False: ["/b.xlsx", "/a.xlsx"])
    return seen


def test_load_universe_variants(monkeypatch):
    seen = _fake_excel(monkeypatch, pd.DataFrame({"Symbol": ["AAA", "BBB", "nan", ""]}))
    assert dv.load_universe("US", 0) == ["AAA", "BBB"]
    assert seen["path"] == "/b.xlsx" and seen["sheet"] == "All_Stocks"   # last sorted file wins
    assert dv.load_universe("US", 1) == ["AAA"]


def test_load_universe_india_and_asia(monkeypatch):
    _fake_excel(monkeypatch, pd.DataFrame({"Symbol": ["TCS", "INFY"], "Suffix": [".NS", ".BO"],
                                           "YF_Ticker": ["7203.T", "005930.KS"]}))
    assert dv.load_universe("India", None) == ["TCS.NS", "INFY.BO"]
    assert dv.load_universe("Japan", 5) == ["7203.T", "005930.KS"]
    assert dv.load_universe("Korea", 1) == ["7203.T"]


def _run_main(monkeypatch, tmp_path, universe, ohlc, *argv):
    ms = types.ModuleType("market_store")
    ms.cached_download = lambda tickers, years=5: ohlc
    monkeypatch.setitem(sys.modules, "market_store", ms)
    monkeypatch.setattr(dv, "load_universe", lambda m, l: universe)
    db = str(tmp_path / "out.db")
    monkeypatch.setattr(sys, "argv", ["dvm_engine.py", "--db", db, *argv])
    dv.main()
    con = sqlite3.connect(db)
    rows = con.execute("SELECT ticker, market, D, V, M, code, label FROM dvm ORDER BY ticker").fetchall()
    con.close()
    return rows


def test_main_japan_filters_illiquid_and_short(monkeypatch, tmp_path, capsys):
    good = _ohlcv(300, slope=0.5, noise=1.0, vol=1_000_000)
    short = _ohlcv(250, noise=1.0)  # len > 250 is False
    illiquid = _ohlcv(300, noise=1.0, vol=1)
    ohlc = {"G.T": good, "S.T": short, "I.T": illiquid}
    rows = _run_main(monkeypatch, tmp_path, list(ohlc), ohlc, "--market", "Japan", "--screen", "momentum_breakout")
    assert [r[0] for r in rows] == ["G.T"]
    t, mkt, D, V, M, code, label = rows[0]
    assert mkt == "Japan" and D is None and V is None
    assert code == ("B" + "B" + ("G" if M >= 50 else "B"))
    assert label == dv.LABELS[code]
    out = capsys.readouterr().out
    assert "DVM SCORES" in out and "1 stocks" in out


def test_main_us_valuation_rank_and_durability(monkeypatch, tmp_path, capsys):
    ohlc = {t: _ohlcv(300, slope=0.5, noise=1.0) for t in ("CHEAP", "DEAR")}
    price = float(ohlc["CHEAP"]["Close"].iloc[-1])
    facts = {"CHEAP": _fund(ni=[10.0 * price], shares=10.0),
             "DEAR": _fund(ni=[1.0 * price], shares=10.0)}
    mod = types.ModuleType("pit_fundamentals")
    mod.as_of = lambda t, d: facts[t]
    mod.piotroski_asof = lambda t, d: (9, {})
    mod._g = lambda lst, i=0: lst[i] if lst and len(lst) > i else None
    monkeypatch.setitem(sys.modules, "pit_fundamentals", mod)
    rows = _run_main(monkeypatch, tmp_path, list(ohlc), ohlc, "--market", "US", "--screen", "high_dvm")
    by = {r[0]: r for r in rows}
    assert by["CHEAP"][3] == 100.0 and by["DEAR"][3] == 50.0   # earnings-yield percentile rank
    assert by["CHEAP"][2] is not None and by["CHEAP"][2] > 50   # durability computed for US
    assert by["CHEAP"][5][0] == "G" and by["CHEAP"][5][1] == "G"
    assert "SCREEN: high_dvm" in capsys.readouterr().out


def test_main_us_without_facts_has_no_valuation(monkeypatch, tmp_path):
    ohlc = {"X": _ohlcv(300, slope=0.5, noise=1.0)}
    mod = types.ModuleType("pit_fundamentals")
    mod.as_of = lambda t, d: {}
    mod.piotroski_asof = lambda t, d: (None, {})
    mod._g = lambda lst, i=0: None
    monkeypatch.setitem(sys.modules, "pit_fundamentals", mod)
    rows = _run_main(monkeypatch, tmp_path, ["X"], ohlc, "--market", "US")
    assert rows[0][2] is None and rows[0][3] is None


def test_momentum_exactly_220_rows_is_scored():
    assert not np.isnan(dv.momentum_score(_ohlcv(220, noise=1.0)))


def test_main_valuation_exactly_50_counts_as_good(monkeypatch, tmp_path):
    ohlc = {t: _ohlcv(300, slope=0.5, noise=1.0) for t in ("CHEAP", "DEAR")}
    price = float(ohlc["CHEAP"]["Close"].iloc[-1])
    facts = {"CHEAP": _fund(ni=[10.0 * price], shares=10.0), "DEAR": _fund(ni=[1.0 * price], shares=10.0)}
    mod = types.ModuleType("pit_fundamentals")
    mod.as_of = lambda t, d: facts[t]
    mod.piotroski_asof = lambda t, d: (9, {})
    mod._g = lambda lst, i=0: lst[i] if lst and len(lst) > i else None
    monkeypatch.setitem(sys.modules, "pit_fundamentals", mod)
    rows = {r[0]: r for r in _run_main(monkeypatch, tmp_path, list(ohlc), ohlc, "--market", "US")}
    assert rows["DEAR"][3] == 50.0 and rows["DEAR"][5][1] == "G"
