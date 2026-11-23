"""accumulation_screener.py: CMF/accumulation signal, quantile validation, panels, main() (offline)."""
import os
import sys
import types

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import accumulation_screener as acc  # noqa: E402
import marketdata  # noqa: E402


def _bars(n=60, up=True, seed=0):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.standard_normal(n) * 0.5 + (0.3 if up else -0.3))
    high = close + 1.0
    low = close - 1.0
    if up:
        close = high - 0.1         # closes near the high on rising volume -> accumulation
    else:
        close = low + 0.1
    vol = np.linspace(1e5, 3e5, n) if up else np.linspace(3e5, 1e5, n)
    return high, low, close, vol


def test_accumulation_signal_direction():
    a = acc.accumulation_signal(*_bars(up=True))
    d = acc.accumulation_signal(*_bars(up=False))
    assert a["cmf"] > 0 > d["cmf"]
    assert a["accum"] > d["accum"] and -6 < a["accum"] < 6


def test_accumulation_signal_degenerate_inputs():
    z = np.ones(30)
    r = acc.accumulation_signal(z, z, z, z)
    assert set(r) == {"cmf", "accum"}
    nan = np.full(30, np.nan)
    r = acc.accumulation_signal(nan, nan, nan, nan)
    assert np.isnan(r["cmf"])


def _panel(n=50, noise=0.5, seed=0):
    rng = np.random.default_rng(seed)
    s = rng.standard_normal(n)
    return pd.DataFrame({"cmf": s, "accum": s * 2, "fwd_ret": s * 0.05 + rng.standard_normal(n) * 0.05 * noise})


def test_quantile_returns_buckets_and_monotone():
    p = _panel(100, noise=0.0)
    c = acc.quantile_returns(p, "cmf")
    assert list(c["bucket"]) == ["Q1", "Q2", "Q3", "Q4", "Q5"] and list(c.columns) == ["bucket", "median_fwd%", "mean_fwd%", "n"]
    assert c["n"].sum() == 100
    assert c["median_fwd%"].is_monotonic_increasing
    assert acc.monotonicity(c) == pytest.approx(1.0)


@pytest.mark.xfail(strict=True, raises=ValueError,
                   reason="accumulation_screener.py quantile_returns: pd.qcut(duplicates='drop') with a fixed 5-label "
                          "list raises 'Bin labels must be one fewer than the number of bin edges' when the signal has ties")
def test_quantile_returns_handles_tied_signal_values():
    p = pd.DataFrame({"cmf": [0.0] * 20 + list(np.linspace(0.1, 1, 10)), "fwd_ret": np.linspace(-0.1, 0.1, 30)})
    assert not acc.quantile_returns(p, "cmf").empty


def test_quantile_returns_too_few_and_nan_drop():
    assert acc.quantile_returns(_panel(14), "cmf").empty                     # < q*3 rows
    assert not acc.quantile_returns(_panel(15), "cmf").empty                 # exactly q*3
    p = _panel(30)
    p.loc[:9, "cmf"] = np.nan
    assert acc.quantile_returns(p, "cmf")["n"].sum() == 20
    c = acc.quantile_returns(_panel(60), "cmf", q=3)
    assert list(c["bucket"]) == ["Q1", "Q2", "Q3"]


def _wide_frames(n=300, k=12, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    cols = [f"S{i:02d}" for i in range(k)]
    close = pd.DataFrame(100 * np.cumprod(1 + 0.0005 + 0.01 * rng.standard_normal((n, k)), axis=0), index=idx, columns=cols)
    return {"Close": close, "High": close * (1 + 0.02 * rng.random((n, k))), "Low": close * (1 - 0.02 * rng.random((n, k))),
            "Volume": pd.DataFrame(1e6 * (1 + 0.1 * rng.standard_normal((n, k))), index=idx, columns=cols)}


@pytest.fixture
def wide(monkeypatch):
    frames = _wide_frames()
    other = _wide_frames(seed=1)
    monkeypatch.setattr(acc, "_wide", lambda m: None if m == "NONE" else (other if m == "JP" else frames))
    import pead_factor as pf
    monkeypatch.setattr(pf, "_liquid_symbols", lambda c, v: list(c.columns[:10]))
    return frames


def test_build_panel_forward_window_is_strictly_after_signal(wide):
    p = acc.build_panel("US", 21)
    close = wide["Close"]
    T = len(close) - 21 - 1
    assert len(p) == 10 and set(p.columns) == {"market", "ticker", "fwd_ret", "cmf", "accum"}
    r = p.set_index("ticker").loc["S03"]
    assert r["fwd_ret"] == pytest.approx(close["S03"].iloc[T + 21] / close["S03"].iloc[T] - 1)
    assert (p["market"] == "US").all()


def test_build_panel_skips_bad_prices_and_sparse_windows(wide):
    close = wide["Close"]
    T = len(close) - 21 - 1
    close.iloc[T, 0] = np.nan                       # S00: p0 NaN
    close.iloc[T + 21, 1] = np.nan                  # S01: p1 NaN
    close.iloc[T, 2] = 0.0                          # S02: p0 == 0 (not > 0)
    close.iloc[T - 42:T - 20, 3] = np.nan           # S03: only 20 finite of 42 (< 21 needed)
    p = acc.build_panel("US", 21)
    assert not {"S00", "S01", "S02", "S03"} & set(p["ticker"])
    close.iloc[T - 42:T - 21, 4] = np.nan           # exactly 21 finite remain -> kept (>= sig_window//2)
    assert "S04" in set(acc.build_panel("US", 21)["ticker"])


def test_build_panel_edge_returns_empty(wide):
    assert acc.build_panel("NONE", 21).empty
    assert acc.build_panel("US", 21, sig_window=10_000).empty


def test_current_screen_uses_latest_window(wide):
    s = acc.current_screen("US")
    assert len(s) == 10 and set(s.columns) == {"market", "ticker", "close", "cmf", "accum"}
    assert s.set_index("ticker").loc["S00", "close"] == round(float(wide["Close"]["S00"].iloc[-1]), 2)
    assert acc.current_screen("NONE").empty
    wide["Close"].iloc[:-40, 5] = np.nan                         # S05: only 40 obs < sig_window + 2 (44)
    assert "S05" not in set(acc.current_screen("US")["ticker"])


def test_report_prints_each_signal(capsys):
    acc._report(_panel(100, noise=0.0), "TEST")
    out = capsys.readouterr().out
    assert "TEST forward (100 stock-obs)" in out and "by cmf" in out and "by accum" in out
    assert "IC=+1.000" in out and "mono=+1.00" in out
    acc._report(_panel(10), "SMALL")
    assert "too few obs" in capsys.readouterr().out


def test_main_full_flow_and_out_csv(monkeypatch, tmp_path, capsys, wide):
    monkeypatch.setattr(marketdata, "market_list", lambda: ["US", "JP"])
    out = str(tmp_path / "screen.csv")
    monkeypatch.setattr(sys, "argv", ["acc", "--out", out, "--top", "3", "--signal", "cmf"])
    acc.main()
    cap = capsys.readouterr()
    assert "1-MONTH (21d) forward" in cap.out and "6-MONTH (126d) forward" in cap.out
    assert "CURRENT SCREEN — top 3 by cmf — US, JP" in cap.out
    df = pd.read_csv(out)
    assert list(df["cmf"]) == sorted(df["cmf"], reverse=True) and len(df) == 20
    assert "wrote" in cap.err


def test_main_single_market_and_outlier_filter(monkeypatch, capsys, wide):
    seen = []

    def bp(m, h, w):
        seen.append((m, h, w))
        d = _panel(60, noise=0.0)
        d["market"] = m
        d.loc[0, "fwd_ret"] = 3.0                               # |ret| >= 3 -> dropped as penny outlier
        return d
    monkeypatch.setattr(acc, "build_panel", bp)
    monkeypatch.setattr(sys, "argv", ["acc", "--market", "US", "--sig-window", "30"])
    acc.main()
    out = capsys.readouterr().out
    assert [s[:2] for s in seen] == [("US", 21), ("US", 126)] and seen[0][2] == 30
    assert "(59 stock-obs)" in out


def test_main_insufficient_history_message(monkeypatch, capsys, wide):
    monkeypatch.setattr(acc, "build_panel", lambda m, h, w: pd.DataFrame())
    monkeypatch.setattr(sys, "argv", ["acc", "--market", "US"])
    acc.main()
    out = capsys.readouterr().out
    assert "1-MONTH (21d): insufficient history for this horizon" in out
    assert "6-MONTH (126d): insufficient history for this horizon" in out
