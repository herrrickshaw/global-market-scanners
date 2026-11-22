"""pit_backtest.py: breakout rule, metrics and the monthly walk-forward main() (all inputs faked)."""
import os
import sqlite3
import sys
import types

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pit_backtest as pb  # noqa: E402
from conftest_helpers import make_ohlcv  # noqa: E402


def test_load_scan_universe(monkeypatch):
    monkeypatch.setattr(pb.glob, "glob", lambda p, recursive=False: [])
    assert pb.load_scan_universe() == []
    monkeypatch.setattr(pb.glob, "glob", lambda p, recursive=False: ["/b.xlsx", "/a.xlsx"])
    seen = {}

    class XL:
        def __init__(self, p):
            seen["p"] = p

        def parse(self, s):
            return pd.DataFrame({"Symbol": [" AAA ", "nan", "BBB", ""]})
    monkeypatch.setattr(pb.pd, "ExcelFile", XL)
    assert pb.load_scan_universe() == ["AAA", "BBB"] and seen["p"] == "/b.xlsx" and pb.load_scan_universe(1) == ["AAA"]


def test_darvas_breakout_boundary():
    idx = pd.date_range("2024-01-01", periods=70, freq="B")
    s = pd.Series(np.r_[np.full(60, 100.0), np.full(10, 90.0)], index=idx)
    s.iloc[65] = 100.0
    assert pb.darvas_breakout(s, idx[65]) is True                                # equals prior max -> breakout (>=)
    s.iloc[66] = 100.01
    assert pb.darvas_breakout(s, idx[66]) is True
    s.iloc[67] = 99.99
    assert pb.darvas_breakout(s, idx[67]) is False
    assert pb.darvas_breakout(s, idx[59]) is False                               # fewer than 60 prior bars


def test_metrics():
    assert pb.metrics([]) == {"n_months": 0}
    m = pb.metrics([10.0, -10.0, 10.0, -10.0])
    assert m["n_months"] == 4 and m["hit_rate%"] == 50.0 and m["avg_mth%"] == 0.0
    assert m["ann_return%"] == round((0.9801 ** 3 - 1) * 100, 2) and m["max_dd%"] == -10.9
    flat = pb.metrics([1.0, 1.0, 1.0])
    assert flat["sharpe"] == 0 and flat["max_dd%"] == 0.0 and flat["hit_rate%"] == 100.0
    up = pb.metrics([2.0, 4.0, 6.0])
    assert up["sharpe"] > 0 and up["avg_mth%"] == 4.0


def _install(monkeypatch, ohlc, piot=None, cc=None, asof_fn=None):
    ms = types.ModuleType("market_store"); ms.cached_download = lambda t, years: ohlc
    monkeypatch.setitem(sys.modules, "market_store", ms)
    monkeypatch.setattr(pb, "piotroski_asof", piot or (lambda t, d: (8, {})))
    monkeypatch.setattr(pb, "coffeecan_asof", cc or (lambda t, d, mktcap=None: (True, {})))
    monkeypatch.setattr(pb, "as_of", asof_fn or (lambda t, d: {"shares": 1e6}))


def _ohlc(k=6, n=260, seed=0):
    out = {}
    for i in range(k):
        df = make_ohlcv(n, seed=seed + i, drift=0.002, start="2023-01-02", volume=1e6)
        out[f"T{i}"] = df
    return out


def test_main_arms_and_db(monkeypatch, tmp_path, capsys):
    ohlc = _ohlc()
    ohlc["ILLIQ"] = make_ohlcv(260, start="2023-01-02", volume=1.0)
    ohlc["SHORT"] = make_ohlcv(70, start="2023-01-02")
    _install(monkeypatch, ohlc)
    db = str(tmp_path / "o.db")
    monkeypatch.setattr(sys, "argv", ["pb", "--db", db, "--limit", "9"])
    pb.main()
    cap = capsys.readouterr()
    assert "7 usable series" in cap.err and "-> 6 tradeable names" in cap.err
    con = sqlite3.connect(db)
    res = pd.read_sql("SELECT * FROM arm_summary", con).set_index("arm"); con.close()
    assert list(res.index) == ["A_darvas", "B_darvas_F7", "C_triple_hit", "BENCH_all"] and res.loc["BENCH_all", "avg_picks"] == "all"
    assert res.loc["BENCH_all", "n_months"] >= 3
    assert res.loc["A_darvas", "n_months"] == res.loc["B_darvas_F7", "n_months"] == res.loc["C_triple_hit", "n_months"]
    assert "breakouts=" in cap.err and "TRIPLE-HIT vs GATE COMPONENTS" in cap.out and "Net of 0.10% US round-trip" in cap.out


def test_main_piotroski_gate_blocks_later_arms(monkeypatch, tmp_path, capsys):
    _install(monkeypatch, _ohlc(), piot=lambda t, d: (6, {}))                    # F=6 (<7) -> B and C empty
    monkeypatch.setattr(sys, "argv", ["pb", "--db", str(tmp_path / "o.db")])
    pb.main()
    con = sqlite3.connect(str(tmp_path / "o.db"))
    res = pd.read_sql("SELECT * FROM arm_summary", con).set_index("arm"); con.close()
    assert res.loc["B_darvas_F7", "n_months"] == 0 and res.loc["C_triple_hit", "n_months"] == 0 and res.loc["A_darvas", "n_months"] > 0
    _install(monkeypatch, _ohlc(), piot=lambda t, d: (7, {}), cc=lambda t, d, mktcap=None: (False, {}))
    pb.main()
    con = sqlite3.connect(str(tmp_path / "o.db"))
    res = pd.read_sql("SELECT * FROM arm_summary", con).set_index("arm"); con.close()
    assert res.loc["B_darvas_F7", "n_months"] > 0 and res.loc["C_triple_hit", "n_months"] == 0     # F>=7 boundary passes
    _install(monkeypatch, _ohlc(), piot=lambda t, d: (None, {}))
    pb.main()


def test_main_scan_universe_and_costs(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(pb, "load_scan_universe", lambda limit: (called.append(limit), ["T0", "T1"])[1])
    _install(monkeypatch, _ohlc(2))
    monkeypatch.setattr(sys, "argv", ["pb", "--universe", "scan", "--limit", "2", "--db", str(tmp_path / "o.db")])
    pb.main()
    assert called == [2]
    con = sqlite3.connect(str(tmp_path / "o.db"))
    bench = pd.read_sql("SELECT * FROM arm_summary WHERE arm='BENCH_all'", con).iloc[0]; con.close()
    assert bench["n_months"] > 0
