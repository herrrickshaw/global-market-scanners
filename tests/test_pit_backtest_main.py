"""Characterisation of ``pit_backtest.main`` (synthetic prices, faked PIT lookups, temp sqlite)."""
from __future__ import annotations

import io
import json
import sqlite3
import sys
import types
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import numpy as np
import pandas as pd

import pit_backtest as mod

GOLDEN = Path(__file__).parent / "golden_pit_backtest.json"


def _ohlc():
    idx = pd.bdate_range("2023-01-02", periods=420)
    out = {}
    for i, t in enumerate(["AAA", "BBB", "CCC", "DDD", "THIN"]):
        rs = np.random.RandomState(i)
        close = 50 * np.exp(np.cumsum(rs.normal(0.0008 * (i - 1), 0.015, len(idx))))
        vol = np.full(len(idx), 5e6 if t != "THIN" else 10.0)
        out[t] = pd.DataFrame({"Close": close, "Volume": vol}, index=idx)
    out["SHORT"] = out["AAA"].iloc[:30]
    return out


def run(monkeypatch, tmp_path, argv):
    monkeypatch.setattr(sys, "argv", ["pit"] + argv + ["--db", str(tmp_path / "o.db")])
    fake = types.ModuleType("market_store")
    fake.cached_download = lambda tickers, years=5: _ohlc()
    monkeypatch.setitem(sys.modules, "market_store", fake)
    monkeypatch.setattr(mod, "load_scan_universe", lambda limit=None: ["AAA", "BBB", "CCC", "DDD", "THIN", "SHORT"])
    monkeypatch.setattr(mod, "piotroski_asof", lambda t, d: ((8 if t != "CCC" else 5), None))
    monkeypatch.setattr(mod, "as_of", lambda t, d: {"shares": 1e9} if t != "DDD" else None)
    monkeypatch.setattr(mod, "coffeecan_asof", lambda t, d, mktcap=None: (t == "AAA", None))
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        mod.main()
    con = sqlite3.connect(tmp_path / "o.db")
    rows = con.execute("select * from arm_summary").fetchall()
    con.close()
    return {"stdout": out.getvalue(), "stderr": err.getvalue(), "rows": json.loads(json.dumps(rows, default=str))}


def collect(monkeypatch, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    return {"scan": run(monkeypatch, a, ["--universe", "scan", "--min-dollar-vol", "2e6"]),
            "builtin": run(monkeypatch, b, ["--years", "2", "--limit", "3", "--min-dollar-vol", "0"])}


def test_main_matches_golden(monkeypatch, tmp_path):
    assert json.dumps(collect(monkeypatch, tmp_path)) == json.dumps(json.loads(GOLDEN.read_text()))
