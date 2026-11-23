"""screen_viability.py: price-screen signals, forward-return stats, SQLite store and main() (offline)."""
import os
import sqlite3
import sys
import types

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import screen_viability as sv  # noqa: E402
from conftest_helpers import make_ohlcv  # noqa: E402


def _flat(n=320, price=50.0, vol=2e5):
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    return pd.DataFrame({"Open": price, "High": price + 1, "Low": price - 1, "Close": price,
                         "Volume": vol}, index=idx)


def test_screens_for_keys_and_rsi_oversold():
    df = _flat(300)
    s = sv.screens_for(df)
    assert set(s) == {"rsi_oversold", "near_52w_high", "price_vol_breakout", "darvas_proximity",
                      "golden_crossover"}
    assert not s["rsi_oversold"].any()                      # flat -> RSI NaN -> False
    down = _flat(300)
    down["Close"] = np.linspace(100, 20, 300)
    assert sv.screens_for(down)["rsi_oversold"].iloc[-1]


def test_screens_near_high_and_darvas_boundaries():
    df = _flat(300, price=100.0)
    c = df["Close"].copy()
    c.iloc[-1] = 90.0            # exactly 0.90 * hi52 -> >= passes
    df["Close"] = c
    s = sv.screens_for(df)
    assert bool(s["near_52w_high"].iloc[-1])
    c.iloc[-1] = 89.99
    df["Close"] = c
    assert not bool(sv.screens_for(df)["near_52w_high"].iloc[-1])
    # darvas needs price > 10 and vol > 1e5 (and >=10% above the 52w low -> use a ramp)
    def ramp(top, vol):
        d = _flat(300, vol=vol)
        d["Close"] = np.linspace(top * 0.8, top, 300)
        return d
    assert sv.screens_for(ramp(100.0, 1e5 + 1))["darvas_proximity"].iloc[-1]
    assert not sv.screens_for(ramp(100.0, 1e5))["darvas_proximity"].iloc[-1]      # vol must be > 1e5
    assert not sv.screens_for(ramp(10.0, 2e5))["darvas_proximity"].iloc[-1]       # price must be > 10
    assert sv.screens_for(ramp(10.5, 2e5))["darvas_proximity"].iloc[-1]
    flat = _flat(300, price=100.0)                                                 # flat: not 10% above low
    assert not sv.screens_for(flat)["darvas_proximity"].iloc[-1]


def test_screens_price_volume_breakout_needs_5x_and_up_day():
    df = _flat(60)
    df.loc[df.index[-1], "Volume"] = 1e6          # 20d avg incl. today = (19*2e5+1e6)/20 = 2.2e5 -> 4.5x
    assert not sv.screens_for(df)["price_vol_breakout"].iloc[-1]
    df2 = _flat(60, vol=1e5)
    df2.loc[df2.index[-1], "Volume"] = 1e7       # ratio ~ 9.6x
    df2.loc[df2.index[-1], "Close"] = 51.0       # up day
    assert sv.screens_for(df2)["price_vol_breakout"].iloc[-1]
    df2.loc[df2.index[-1], "Close"] = 49.0       # down day
    assert not sv.screens_for(df2)["price_vol_breakout"].iloc[-1]


def test_screens_golden_crossover_is_event():
    n = 400
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    c = np.r_[np.linspace(200, 100, 300), np.linspace(100, 400, 100)]
    df = pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": 1e6}, index=idx)
    gc = sv.screens_for(df)["golden_crossover"]
    assert gc.sum() == 1                       # single cross event, not a persistent state
    i = list(gc).index(True)
    d50, d200 = df.Close.rolling(50).mean(), df.Close.rolling(200).mean()
    assert d50.iloc[i] > d200.iloc[i] and d50.iloc[i - 1] <= d200.iloc[i - 1]


def test_eval_ticker_too_short_or_none():
    assert sv.eval_ticker(None) == []
    assert sv.eval_ticker(_flat(sv.MIN_BARS + sv.FWD - 1)) == []
    assert sv.eval_ticker(_flat(sv.MIN_BARS + sv.FWD)) != []


def test_eval_ticker_stats_math():
    df = make_ohlcv(500, seed=3, drift=0.001)
    rows = {r["screen"]: r for r in sv.eval_ticker(df)}
    assert set(rows) >= {"rsi_oversold", "near_52w_high", "golden_crossover"}
    close = df["Close"]
    fwd = (close.pct_change(5).shift(-5) * 100).clip(-30, 30)
    base = float(fwd.mean())
    mask = sv.screens_for(df)["near_52w_high"].fillna(False)
    r = fwd[mask].dropna()
    n = rows["near_52w_high"]
    assert n["n_signals"] == len(r) and n["baseline"] == round(base, 4)
    assert n["avg_fwd5d"] == round(float(r.mean()), 4)
    assert n["hit_pct"] == round(float((r > 0).mean() * 100), 1)
    assert n["edge"] == round(float(r.mean()) - base, 4)


def test_eval_ticker_zero_signal_screen_row():
    rows = {r["screen"]: r for r in sv.eval_ticker(_flat(320))}
    z = rows["rsi_oversold"]
    assert z["n_signals"] == 0 and z["avg_fwd5d"] is None and z["hit_pct"] is None and z["edge"] is None


def test_eval_ticker_clip_limits_outliers():
    df = make_ohlcv(400, seed=1)
    df.iloc[200:, df.columns.get_loc("Close")] *= 10      # +900% jump
    wide = {r["screen"]: r for r in sv.eval_ticker(df, clip=1000)}
    narrow = {r["screen"]: r for r in sv.eval_ticker(df, clip=30)}
    assert wide["near_52w_high"]["baseline"] != narrow["near_52w_high"]["baseline"]


def test_eval_ticker_horizon_changes_forward_window():
    df = make_ohlcv(500, seed=3)
    a = sv.eval_ticker(df, horizon=5)[0]["baseline"]
    b = sv.eval_ticker(df, horizon=21)[0]["baseline"]
    assert a != b


def test_eval_ticker_include_ml_adds_screen_or_swallows(monkeypatch):
    df = make_ohlcv(700, seed=2)
    from ml_signal_engine import MLSignalEngine
    rows = sv.eval_ticker(df, include_ml=True, ml_engine=MLSignalEngine("ridge"))
    assert "ml_bullish" in {r["screen"] for r in rows}
    assert "ml_bullish" not in {r["screen"] for r in sv.eval_ticker(df, include_ml=True, ml_engine=None)}
    broken = types.SimpleNamespace(_make_sequences=lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    rows2 = sv.eval_ticker(df, include_ml=True, ml_engine=broken)     # error swallowed
    assert "ml_bullish" not in {r["screen"] for r in rows2}


def test_db_helpers_roundtrip(tmp_path):
    conn = sv.init_db(str(tmp_path / "v.db"))
    conn.executemany("INSERT INTO done VALUES (?,?)", [("US", "A"), ("US", "B"), ("JP", "C")])
    conn.commit()
    assert sv.already_done(conn, "US") == {"A", "B"} and sv.already_done(conn, "KR") == set()
    sv.init_db(str(tmp_path / "v.db")).close()          # idempotent


def test_write_summary_aggregates_and_viability(tmp_path):
    conn = sv.init_db(str(tmp_path / "v.db"))
    rows = [("US", "A", "s1", 5, 1.0, 60.0, 0.5, 0.5), ("US", "B", "s1", 3, 2.0, 70.0, 0.5, 1.5),
            ("US", "C", "s1", 0, None, None, 0.5, None),          # n_signals=0 excluded
            ("US", "A", "s2", 4, -1.0, 40.0, 0.5, -1.5),
            ("JP", "A", "s3", 2, 1.0, 50.0, 0.0, 1.0)]            # hit exactly 50 -> not viable
    conn.executemany("INSERT INTO ticker_screen VALUES (?,?,?,?,?,?,?,?)", rows)
    sv.write_summary(conn)
    df = pd.read_sql("SELECT * FROM market_screen_summary", conn).set_index(["market", "screen"])
    s1 = df.loc[("US", "s1")]
    assert s1["n_tickers"] == 2 and s1["total_signals"] == 8 and s1["avg_edge"] == 1.0
    assert s1["avg_hit_pct"] == 65.0 and s1["pct_tickers_pos_edge"] == 100.0 and s1["viable"] == "YES"
    assert df.loc[("US", "s2")]["viable"] == "no" and df.loc[("US", "s2")]["pct_tickers_pos_edge"] == 0.0
    assert df.loc[("JP", "s3")]["viable"] == "no"
    sv.write_summary(conn)                                  # re-runnable (DROP + CREATE)


def test_load_universe(monkeypatch):
    monkeypatch.setattr(sv.glob, "glob", lambda p, recursive=False: [])
    assert sv.load_universe("US") == []
    monkeypatch.setattr(sv.glob, "glob", lambda p, recursive=False: ["/b.xlsx", "/a.xlsx"])
    seen = {}
    frames = {
        "US": pd.DataFrame({"Symbol": [" AAA ", "BBB", "nan", "", "AAA"]}),
        "India": pd.DataFrame({"Symbol": ["TCS", "INFY"], "Suffix": [".NS", ".BO"]}),
        "Japan": pd.DataFrame({"YF_Ticker": ["7203.T", "nan", " 6758.T "]}),
    }
    cur = {}

    class FakeXL:
        def __init__(self, p):
            seen["p"] = p

        def parse(self, sheet):
            seen["sheet"] = sheet
            return cur["df"]

    monkeypatch.setattr(sv.pd, "ExcelFile", FakeXL)
    cur["df"] = frames["US"]
    assert sv.load_universe("US") == ["AAA", "BBB"]               # stripped, nan/blank dropped, deduped
    assert seen["p"] == "/b.xlsx" and seen["sheet"] == "All_Stocks"
    assert sv.load_universe("US", limit=1) == ["AAA"]
    cur["df"] = frames["India"]
    assert sv.load_universe("India") == ["TCS.NS", "INFY.BO"]
    cur["df"] = frames["Japan"]
    assert sv.load_universe("Japan") == ["7203.T", "6758.T"]


def _install_store(monkeypatch, data):
    ms = types.ModuleType("market_store")
    calls = []
    ms.cached_download = lambda batch, years: (calls.append((list(batch), years)) or
                                               {t: data.get(t) for t in batch})
    monkeypatch.setitem(sys.modules, "market_store", ms)
    return calls


def test_main_batches_resumes_and_summarises(monkeypatch, tmp_path, capsys):
    data = {t: make_ohlcv(450, seed=i) for i, t in enumerate(["A", "B", "C"])}
    calls = _install_store(monkeypatch, data)
    monkeypatch.setattr(sv, "load_universe", lambda m, limit=None: ["A", "B", "C"])
    db = str(tmp_path / "v.db")
    monkeypatch.setattr(sys, "argv", ["screen_viability.py", "--markets", "US", "--db", db,
                                      "--batch", "2", "--horizon", "5"])
    sv.main()
    assert calls == [(["A", "B"], 5), (["C"], 5)]
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM done").fetchone()[0] == 3
    assert con.execute("SELECT COUNT(DISTINCT ticker) FROM ticker_screen").fetchone()[0] == 3
    con.close()
    cap = capsys.readouterr()
    assert "[US] universe=3 done=0 todo=3" in cap.err and "2/3 processed" in cap.err and "3/3 processed" in cap.err
    assert "SCREEN VIABILITY BY MARKET" in cap.out
    calls.clear()
    sv.main()                                           # second run: everything already done
    assert calls == []
    assert "done=3 todo=0" in capsys.readouterr().err


def test_main_clip_scales_with_horizon(monkeypatch, tmp_path):
    _install_store(monkeypatch, {"A": make_ohlcv(450)})
    monkeypatch.setattr(sv, "load_universe", lambda m, limit=None: ["A"])
    seen = []
    monkeypatch.setattr(sv, "eval_ticker", lambda df, h, clip, inc, eng: seen.append((h, clip)) or [])
    monkeypatch.setattr(sys, "argv", ["screen_viability.py", "--markets", "US", "--db", str(tmp_path / "v.db"),
                                      "--horizon", "20"])
    sv.main()
    assert seen == [(20, 60)]                      # 30 * sqrt(20/5) = 60


def test_main_include_ml_builds_engine(monkeypatch, tmp_path):
    _install_store(monkeypatch, {"A": make_ohlcv(450)})
    monkeypatch.setattr(sv, "load_universe", lambda m, limit=None: ["A"])
    seen = []
    monkeypatch.setattr(sv, "eval_ticker", lambda df, h, clip, inc, eng: seen.append((inc, type(eng).__name__)) or [])
    monkeypatch.setattr(sys, "argv", ["screen_viability.py", "--markets", "US", "--db", str(tmp_path / "v.db"),
                                      "--include-ml"])
    sv.main()
    assert seen == [(True, "MLSignalEngine")]


def test_main_export_summary(monkeypatch, tmp_path, capsys):
    db = str(tmp_path / "v.db")
    conn = sv.init_db(db)
    conn.execute("INSERT INTO ticker_screen VALUES ('US','A','s1',3,1.0,60.0,0.5,0.5)")
    conn.commit(); conn.close()
    out = str(tmp_path / "summary.db")
    monkeypatch.setattr(sys, "argv", ["screen_viability.py", "--db", db, "--export-summary", out])
    sv.main()
    con = sqlite3.connect(out)
    assert con.execute("SELECT market, screen, n_tickers FROM market_screen_summary").fetchall() == [("US", "s1", 1)]
    names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    con.close()
    assert names == ["market_screen_summary"]
    assert "exported summary (1 rows)" in capsys.readouterr().err
