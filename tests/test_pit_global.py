"""pit_global.py: global price-only PIT backtest helpers and run_market (offline)."""
import os
import sqlite3
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pit_global as pg  # noqa: E402


def test_market_cost_buckets_and_default():
    assert pg.market_cost("US") == 0.10
    assert pg.market_cost("JP") == 0.20
    assert pg.market_cost("KR") == 0.25
    assert pg.market_cost("DE") == 0.40 and pg.market_cost("UK") == 0.40
    assert pg.market_cost("ZZ") == 0.30       # unknown market -> default


def _long(symbols, n=400, seed=0, short=()):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-02", periods=n, freq="B")
    frames = []
    for s in symbols:
        nn = 50 if s in short else n
        c = 100 * np.cumprod(1 + 0.0005 + 0.01 * rng.standard_normal(nn))
        frames.append(pd.DataFrame({"Date": idx[-nn:].astype(str), "Symbol": s, "Close": c}))
    return pd.concat(frames, ignore_index=True)


def test_load_closes_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(pg, "SEED", str(tmp_path))
    assert pg.load_closes("US", 5) == {}


def test_load_closes_filters_short_and_cutoff(monkeypatch, tmp_path):
    monkeypatch.setattr(pg, "SEED", str(tmp_path))
    (tmp_path / "cleaned_long_US.parquet").write_bytes(b"x")
    df = _long(["A", "B", "SHORT"], n=400, short=("SHORT",))
    df.loc[df.Symbol == "B", "Close"] = np.nan                 # all-NaN -> dropped
    monkeypatch.setattr(pg.pd, "read_parquet", lambda p: df.copy())
    out = pg.load_closes("US", 1)
    assert set(out) == {"A"}
    s = out["A"]
    cutoff = pd.to_datetime(df.Date).max() - pd.Timedelta(days=365 + 30)
    assert s.index.min() >= cutoff and s.index.is_monotonic_increasing and len(s) > 80


def test_load_closes_exactly_81_points_kept_80_dropped(monkeypatch, tmp_path):
    monkeypatch.setattr(pg, "SEED", str(tmp_path))
    (tmp_path / "cleaned_long_US.parquet").write_bytes(b"x")
    idx = pd.date_range("2024-01-01", periods=81, freq="B")
    rows = [pd.DataFrame({"Date": idx.astype(str), "Symbol": "K81", "Close": 1.0}),
            pd.DataFrame({"Date": idx[:80].astype(str), "Symbol": "K80", "Close": 1.0})]
    monkeypatch.setattr(pg.pd, "read_parquet", lambda p: pd.concat(rows))
    assert set(pg.load_closes("US", 5)) == {"K81"}


def _fund_db(path, rows):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE fund(ticker TEXT, market TEXT, roe REAL, de REAL)")
    con.executemany("INSERT INTO fund VALUES (?,?,?,?)", rows)
    con.commit()
    con.close()


def test_durable_names_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(pg, "HERE", str(tmp_path))
    assert pg.durable_names("US") == set()                      # no cache db
    _fund_db(str(tmp_path / "fundamentals_cache.db"), [
        ("aaa.NS", "US", 13, 1.0), ("BBB", "US", 12, 1.0), ("CCC", "US", 20, 1.5),
        ("DDD", "US", 20, 1.49), ("EEE", "JP", 50, 0.1), ("FFF", "US", None, 0.1)])
    assert pg.durable_names("US") == {"AAA", "DDD"}
    assert pg.durable_names("JP") == {"EEE"}


def _closes(n_sym=25, n=400, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-02", periods=n, freq="B")
    return {f"S{i:02d}": pd.Series(100 * np.cumprod(1 + 0.001 + 0.012 * rng.standard_normal(n)), index=idx)
            for i in range(n_sym)}


def test_run_market_skips_when_few_series(monkeypatch, capsys):
    monkeypatch.setattr(pg, "load_closes", lambda m, y: _closes(19))
    assert pg.run_market("US", 5, False).empty
    assert "only 19 usable series" in capsys.readouterr().err


def test_run_market_arms_and_cost_applied(monkeypatch):
    closes = _closes(25)
    monkeypatch.setattr(pg, "load_closes", lambda m, y: closes)
    monkeypatch.setattr(pg, "darvas_breakout", lambda s, t: True)    # everyone breaks out
    r = pg.run_market("US", 5, False)
    assert list(r["arm"]) == ["A_darvas", "BENCH_all"] and (r["market"] == "US").all()
    a, b = r.iloc[0], r.iloc[1]
    assert a["n_months"] == b["n_months"] > 5
    # identical holdings -> identical net returns
    assert a["avg_mth%"] == pytest.approx(b["avg_mth%"])
    # cost really deducted: avg gross monthly minus 0.10 (US) equals reported
    r0 = pg.run_market("ZZ", 5, False)       # default 0.30 cost
    assert b["avg_mth%"] - r0.iloc[1]["avg_mth%"] == pytest.approx(0.20, abs=1e-3)


def test_run_market_no_breakouts_gives_zero_months(monkeypatch):
    monkeypatch.setattr(pg, "load_closes", lambda m, y: _closes(25))
    monkeypatch.setattr(pg, "darvas_breakout", lambda s, t: False)
    r = pg.run_market("US", 5, False)
    assert r.iloc[0]["arm"] == "A_darvas" and r.iloc[0]["n_months"] == 0
    assert r.iloc[1]["n_months"] > 0


def test_run_market_overlay_adds_durable_arm(monkeypatch):
    closes = _closes(25)
    monkeypatch.setattr(pg, "load_closes", lambda m, y: closes)
    monkeypatch.setattr(pg, "darvas_breakout", lambda s, t: True)
    monkeypatch.setattr(pg, "durable_names", lambda m: {"S00", "S01", "S02"})
    r = pg.run_market("US", 5, True)
    assert list(r["arm"]) == ["A_darvas", "BENCH_all", "D_darvas_durable"]
    assert r.iloc[2]["n_months"] == r.iloc[0]["n_months"]
    # overlay arm holds a strict subset -> different average than the full breakout set
    assert r.iloc[2]["avg_mth%"] != r.iloc[0]["avg_mth%"]


def test_run_market_overlay_false_does_not_consult_durable(monkeypatch):
    monkeypatch.setattr(pg, "load_closes", lambda m, y: _closes(25))
    monkeypatch.setattr(pg, "darvas_breakout", lambda s, t: True)

    def boom(m):
        raise AssertionError("durable_names must not run without --overlay")
    monkeypatch.setattr(pg, "durable_names", boom)
    pg.run_market("US", 5, False)


def test_main_single_market_saves_db(monkeypatch, tmp_path, capsys):
    res = pd.DataFrame({"market": ["JP"], "arm": ["A_darvas"], "n_months": [3]})
    monkeypatch.setattr(pg, "run_market", lambda m, y, o: res)
    db = str(tmp_path / "g.db")
    monkeypatch.setattr(sys, "argv", ["pit_global.py", "--market", "JP", "--db", db])
    pg.main()
    con = sqlite3.connect(db)
    assert con.execute("SELECT market, arm, n_months FROM global_arm_summary").fetchall() == [("JP", "A_darvas", 3)]
    con.close()
    cap = capsys.readouterr()
    assert "=== JP — Darvas breakout, monthly, net of 0.20%" in cap.out
    assert "saved 1 rows" in cap.err
    assert "NOTE" not in cap.out


def test_main_all_with_overlay_and_empty_results(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pg.marketdata, "market_list", lambda: ["US", "XX"])
    monkeypatch.setattr(pg, "run_market", lambda m, y, o: (
        pd.DataFrame({"market": [m], "arm": ["A"], "n_months": [1]}) if m == "US" else pd.DataFrame()))
    db = str(tmp_path / "g.db")
    monkeypatch.setattr(sys, "argv", ["pit_global.py", "--all", "--overlay", "--db", db])
    pg.main()
    cap = capsys.readouterr()
    assert "=== US" in cap.out and "=== XX" not in cap.out
    assert "NOTE: D_darvas_durable" in cap.out


def test_main_nothing_to_save(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pg, "run_market", lambda m, y, o: pd.DataFrame())
    db = tmp_path / "g.db"
    monkeypatch.setattr(sys, "argv", ["pit_global.py", "--db", str(db)])
    pg.main()
    assert not db.exists()


def test_run_market_runs_with_exactly_20_series(monkeypatch):
    monkeypatch.setattr(pg, "load_closes", lambda m, y: _closes(20))
    assert not pg.run_market("US", 5, False).empty
