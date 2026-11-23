"""dvm_global.py: per-stock technicals, vectorised market pass, screens, main() (offline)."""
import os
import sqlite3
import sys
from concurrent.futures import Future

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dvm_global as dg  # noqa: E402


def _series(n=300, slope=0.5, base=50.0, noise=1.0, seed=0, vol=1e6):
    rng = np.random.default_rng(seed)
    c = base + slope * np.arange(n) + noise * rng.standard_normal(n)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    c = pd.Series(c, index=idx)
    return c, c + 1, c - 1, pd.Series(vol, index=idx)


def test_tech_requires_min_bars():
    c, h, low, v = _series(dg.MIN_BARS - 1)
    assert dg._tech(c, h, low, v, c.pct_change()) is None
    c, h, low, v = _series(dg.MIN_BARS)
    assert dg._tech(c, h, low, v, c.pct_change()) is not None


def test_tech_uptrend_fields():
    c, h, low, v = _series(300, slope=0.5)
    r = dg._tech(c, h, low, v, c.pct_change())
    assert r["above_200dma"] is True and r["sma50_above_200"] is True and r["macd_bull"] is True
    assert 0 <= r["M"] <= 100 and r["dist_52w"] <= 0
    assert r["vol_ratio"] == 1.0
    assert set(r) == {"M", "rsi", "mfi", "adx", "dist_52w", "above_200dma", "golden_cross",
                      "sma50_above_200", "macd_bull", "vol_ratio", "beta"}


def test_tech_downtrend_scores_lower():
    up = dg._tech(*_series(300, slope=0.5)[:4], pd.Series(dtype=float))
    dn = dg._tech(*_series(300, slope=-0.1, base=200)[:4], pd.Series(dtype=float))
    assert dn["M"] < up["M"]
    assert dn["above_200dma"] is False and dn["macd_bull"] in (True, False)


def test_tech_zero_volume_ratio_defaults_to_one():
    c, h, low, v = _series(300, vol=0)
    r = dg._tech(c, h, low, v, c.pct_change())
    assert r["vol_ratio"] == 1.0


def test_tech_golden_cross_detected():
    # long decline then sharp rally: 50dma crosses over 200dma exactly within the last bars
    n = 400
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    c = pd.Series(np.r_[np.linspace(200, 100, 300), np.linspace(100, 400, 100)], index=idx)
    found = False
    for cut in range(330, 401):
        cc = c.iloc[:cut]
        r = dg._tech(cc, cc + 1, cc - 1, pd.Series(1e6, index=cc.index), cc.pct_change())
        if r["golden_cross"]:
            found = True
            d50, d200 = cc.rolling(50).mean(), cc.rolling(200).mean()
            assert d50.iloc[-1] > d200.iloc[-1] and d50.iloc[-6] <= d200.iloc[-6]
    assert found


def test_tech_beta_nan_when_market_variance_zero():
    c, h, low, v = _series(300)
    flat = pd.Series(0.0, index=c.index)
    assert dg._tech(c, h, low, v, flat)["beta"] is None


def test_tech_beta_matches_cov_over_var():
    c, h, low, v = _series(300, seed=3)
    m, *_ = _series(300, slope=0.2, seed=9)
    mk = m.pct_change()
    r = dg._tech(c, h, low, v, mk)
    ret = c.pct_change()
    exp = ret.tail(200).cov(mk.tail(200)) / mk.tail(200).var()
    assert r["beta"] == pytest.approx(round(exp, 2))


def _row(**kw):
    r = {"M": 0, "rsi": 60, "mfi": 60, "adx": 30, "dist_52w": -5, "above_200dma": True,
         "golden_cross": False, "sma50_above_200": True, "macd_bull": True, "vol_ratio": 1.5}
    r.update(kw)
    return r


def test_screen_momentum_breakout_boundaries():
    f = dg.SCREENS["momentum_breakout"]
    assert f(_row(M=70, dist_52w=-10, adx=25, vol_ratio=1.2))
    assert not f(_row(M=69.9))
    assert not f(_row(M=70, dist_52w=-10.1))
    assert not f(_row(M=70, adx=24.9))
    assert not f(_row(M=70, adx=None))
    assert not f(_row(M=70, vol_ratio=1.19))


def test_screen_others():
    S = dg.SCREENS
    assert S["high_momentum"](_row(M=75)) and not S["high_momentum"](_row(M=74.9))
    assert S["golden_crossover"](_row(golden_cross=True)) and not S["golden_crossover"](_row())
    u = S["uptrend_quality"]
    assert u(_row(rsi=50, adx=20)) and u(_row(rsi=70, adx=20))
    assert not u(_row(rsi=49.9)) and not u(_row(rsi=70.1)) and not u(_row(adx=19.9))
    assert not u(_row(above_200dma=False)) and not u(_row(rsi=None))
    t = S["trendlyne_technical"]
    assert t(_row(rsi=50, mfi=50)) and not t(_row(mfi=49.9)) and not t(_row(macd_bull=False))
    assert not t(_row(mfi=None))
    g = S["sma_golden"]
    assert g(_row()) and not g(_row(sma50_above_200=False)) and not g(_row(above_200dma=False))


def _panel(symbols=("UP", "DOWN", "SHORT"), n=300):
    frames = []
    for i, s in enumerate(symbols):
        slope = {"UP": 0.5, "DOWN": -0.1}.get(s, 0.3)
        base = 200 if s == "DOWN" else 50
        nn = 100 if s == "SHORT" else n
        c, h, low, v = _series(nn, slope=slope, base=base, seed=i)
        frames.append(pd.DataFrame({"Date": c.index, "Symbol": s, "Close": c.values,
                                    "High": h.values, "Low": low.values, "Volume": v.values}))
    return pd.concat(frames, ignore_index=True)


def test_process_market_scores_and_filters(monkeypatch):
    seen = {}

    def fake_read(path):
        seen["path"] = path
        return _panel().sample(frac=1.0, random_state=1)    # unsorted: function must sort by Date

    monkeypatch.setattr(dg.pd, "read_parquet", fake_read)
    rows, hits = dg.process_market("US", "high_momentum")
    assert seen["path"].endswith("cleaned_long_US.parquet")
    by = {r["ticker"]: r for r in rows}
    assert set(by) == {"UP", "DOWN"}          # SHORT has < 200 bars -> dropped
    assert all(r["market"] == "US" for r in rows)
    assert by["UP"]["M"] > by["DOWN"]["M"]
    assert by["UP"]["above_200dma"] and not by["DOWN"]["above_200dma"]
    assert all(h["M"] >= 75 for h in hits)
    assert {h["ticker"] for h in hits} <= {"UP", "DOWN"}


def test_process_market_agrees_with_per_stock_tech(monkeypatch):
    df = _panel(("UP", "DOWN"))
    monkeypatch.setattr(dg.pd, "read_parquet", lambda p: df)
    rows, _ = dg.process_market("US", "golden_crossover")
    for r in rows:
        s = df[df.Symbol == r["ticker"]].set_index("Date")
        t = dg._tech(s["Close"], s["High"], s["Low"], s["Volume"], pd.Series(dtype=float))
        assert r["M"] == pytest.approx(t["M"], abs=0.2)
        assert r["rsi"] == pytest.approx(t["rsi"], abs=0.2)
        assert r["above_200dma"] == t["above_200dma"]
        assert r["dist_52w"] == pytest.approx(t["dist_52w"], abs=0.2)


class _InlineExecutor:
    def __init__(self, max_workers=None):
        self.max_workers = max_workers

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def submit(self, fn, *args):
        f = Future()
        try:
            f.set_result(fn(*args))
        except Exception as e:      # pragma: no cover
            f.set_exception(e)
        return f


def test_main_end_to_end(monkeypatch, tmp_path, capsys):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "cleaned_long_US.parquet").write_bytes(b"x")
    (seed / "cleaned_long_JP.parquet").write_bytes(b"x")
    (seed / "other.parquet").write_bytes(b"x")
    monkeypatch.setattr(dg, "SEED", str(seed))
    monkeypatch.setattr(dg.pd, "read_parquet", lambda p: _panel(("UP", "DOWN")))
    monkeypatch.setattr(dg, "ProcessPoolExecutor", _InlineExecutor)
    db = str(tmp_path / "o.db")
    monkeypatch.setattr(sys, "argv", ["dvm_global.py", "--db", db, "--screen", "high_momentum", "--workers", "1"])
    dg.main()
    con = sqlite3.connect(db)
    n = con.execute("SELECT COUNT(*) FROM dvm_global").fetchone()[0]
    mk = {r[0] for r in con.execute("SELECT DISTINCT market FROM dvm_global")}
    con.close()
    assert n == 4 and mk == {"US", "JP"}
    cap = capsys.readouterr()
    assert "markets with data: ['JP', 'US']" in cap.err
    assert "GLOBAL DVM / TRENDLYNE SCREEN: high_momentum (2 markets)" in cap.out
    assert "TOTAL" in cap.out


def test_main_explicit_markets_subset(monkeypatch, tmp_path, capsys):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "cleaned_long_US.parquet").write_bytes(b"x")
    (seed / "cleaned_long_JP.parquet").write_bytes(b"x")
    monkeypatch.setattr(dg, "SEED", str(seed))
    monkeypatch.setattr(dg.pd, "read_parquet", lambda p: _panel(("UP",)))
    monkeypatch.setattr(dg, "ProcessPoolExecutor", _InlineExecutor)
    db = str(tmp_path / "o.db")
    monkeypatch.setattr(sys, "argv", ["dvm_global.py", "--db", db, "--markets", "JP"])
    dg.main()
    con = sqlite3.connect(db)
    assert {r[0] for r in con.execute("SELECT DISTINCT market FROM dvm_global")} == {"JP"}
    con.close()
    assert "(1 markets)" in capsys.readouterr().out
