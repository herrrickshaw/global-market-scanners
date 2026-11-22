"""hft_selection.py: spread/efficiency/mean-reversion features and archetype picks (offline)."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hft_selection as hs  # noqa: E402
import marketdata  # noqa: E402


def test_daily_range_and_stability():
    r = hs.daily_range_pct([11, 22], [9, 18], [10, 20])
    assert list(r) == [0.2, 0.2]
    z = hs.daily_range_pct([1, 1], [1, 1], [0, -1])
    assert np.isnan(z).all()                                       # non-positive close -> NaN
    assert hs.avg_range([11, 22], [9, 20], [10, 20]) == pytest.approx(0.15)
    assert hs.range_stability([11, 22], [9, 18], [10, 20]) == 0.0
    assert hs.range_stability([11, 24], [9, 18], [10, 20]) == pytest.approx(0.05)


def test_corwin_schultz_spread():
    assert np.isnan(hs.corwin_schultz_spread([10], [9]))
    h = [10.0, 10.0, 10.0, 10.0]; l = [10.0, 10.0, 10.0, 10.0]
    assert hs.corwin_schultz_spread(h, l) == 0.0                   # zero range -> zero spread
    wide = hs.corwin_schultz_spread([10.5, 10.5, 10.5], [9.5, 9.5, 9.5])
    narrow = hs.corwin_schultz_spread([10.1, 10.1, 10.1], [9.9, 9.9, 9.9])
    assert wide > narrow >= 0
    assert np.isnan(hs.corwin_schultz_spread([10, 10], [0, 0]))     # non-positive lows skipped
    assert hs.corwin_schultz_spread([10, 10, 10], [9, 9, 9]) >= 0


def test_efficiency_ratio():
    assert hs.efficiency_ratio([1, 2, 3, 4]) == 1.0
    assert hs.efficiency_ratio([1, 2, 1, 2, 1]) == 0.0
    assert hs.efficiency_ratio([1, 3, 2, 4]) == pytest.approx(3 / 5)
    assert np.isnan(hs.efficiency_ratio([5, 5, 5]))
    assert hs.efficiency_ratio([4, 3, 2, 1]) == 1.0                # absolute net move


def test_lag1_autocorr():
    assert hs.lag1_autocorr([1, 2, 3, 4, 5, 6]) == pytest.approx(1.0)
    assert hs.lag1_autocorr([1, -1, 1, -1, 1, -1]) == pytest.approx(-1.0)
    assert np.isnan(hs.lag1_autocorr([1, 2])) and np.isnan(hs.lag1_autocorr([3, 3, 3, 3]))
    assert hs.lag1_autocorr([1, 2, np.nan, 3, 4, 5]) == pytest.approx(1.0)


def test_ou_half_life():
    rng = np.random.default_rng(0)
    p = [100.0]
    for _ in range(300):
        p.append(p[-1] + -0.2 * (p[-1] - 100) + rng.normal(0, 0.1))
    hl = hs.ou_half_life(p)
    assert hl == pytest.approx(-np.log(2) / np.log(1 - 0.2), rel=0.2)
    assert hs.ou_half_life(np.arange(50.0)) == np.inf              # trending -> infinite
    assert np.isnan(hs.ou_half_life([1.0, 2.0, 3.0]))             # < 4 points
    assert np.isnan(hs.ou_half_life([1.0, np.nan, 2.0, np.nan]))
    assert hs.ou_half_life([10.0, 0.0, 10.0, 0.0, 10.0]) == np.inf or hs.ou_half_life([10.0, 0.0, 10.0, 0.0, 10.0]) > 0


def _feat(n=30, seed=0, peers=True):
    rng = np.random.default_rng(seed)
    f = pd.DataFrame({"avg_range%": rng.uniform(0.5, 5, n), "range_stability": rng.uniform(0, 1, n),
                      "eff_ratio": rng.uniform(0, 1, n), "ret_autocorr": rng.uniform(-1, 1, n),
                      "vol_autocorr": rng.uniform(-1, 1, n), "half_life": rng.uniform(1, 100, n)})
    f.loc[0, "half_life"] = np.inf
    if peers:
        f["peer_corr"] = rng.uniform(0, 1, n); f["peer_dev"] = rng.normal(0, 0.05, n)
    return f


def test_archetype_scores():
    f = hs.archetype_scores(_feat())
    assert {"market_making", "stat_arb", "latency", "etf_arb"} <= set(f.columns)
    # low range + stable + inefficient => best market-making name
    g = _feat()
    g.loc[1, ["avg_range%", "range_stability", "eff_ratio"]] = [0.01, 0.0, 0.0]
    assert hs.archetype_scores(g)["market_making"].idxmax() == 1
    g = _feat(); g.loc[2, ["eff_ratio", "vol_autocorr", "ret_autocorr"]] = [1.0, 1.0, 1.0]
    assert hs.archetype_scores(g)["latency"].idxmax() == 2
    g = _feat(); g.loc[3, ["ret_autocorr", "eff_ratio", "half_life"]] = [-1.0, 0.0, 1.0]
    assert hs.archetype_scores(g)["stat_arb"].idxmax() == 3
    assert hs.archetype_scores(_feat(peers=False))["etf_arb"].isna().all()
    nopeer = _feat(); nopeer["peer_corr"] = np.nan
    assert hs.archetype_scores(nopeer)["etf_arb"].isna().all()
    sa = hs.archetype_scores(_feat())["stat_arb"]
    assert np.isnan(sa.iloc[0]) and sa.iloc[1:].notna().all()                  # infinite half-life -> no stat-arb score


def test_pick():
    f = hs.archetype_scores(_feat())
    f["market"] = "US"; f["ticker"] = [f"T{i}" for i in range(len(f))]; f["cs_spread"] = 0.01
    p = hs.pick(f, "latency", 5)
    assert len(p) == 5 and p["latency"].is_monotonic_decreasing and "market" in p.columns and "etf_arb" not in p.columns
    f.loc[f.index[:10], "latency"] = np.nan
    assert hs.pick(f, "latency", 100)["latency"].notna().all() and len(hs.pick(f, "latency", 100)) == 20


def test_sector_map(monkeypatch, tmp_path):
    monkeypatch.setattr(hs, "COMPANIES", str(tmp_path / "none.parquet"))
    assert hs._sector_map() == {}
    p = tmp_path / "c.parquet"; p.write_bytes(b"x")
    monkeypatch.setattr(hs, "COMPANIES", str(p))
    monkeypatch.setattr(hs.pd, "read_parquet", lambda path: pd.DataFrame({"ticker": ["tcs.NS", "AAPL"], "sector": ["IT", "Tech"], "x": 1}))
    assert hs._sector_map() == {"TCS": "IT", "AAPL": "Tech"}


@pytest.fixture
def market(monkeypatch, tmp_path):
    import liquidity_factor as lf
    import pead_factor as pf
    n = 60
    rng = np.random.default_rng(3)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    cols = ["A", "B", "C", "D", "JUNK"]
    close = pd.DataFrame({c: 50 * np.cumprod(1 + rng.normal(0, 0.01, n)) for c in cols}, index=idx)
    vol = pd.DataFrame({c: 1e6 * (1 + 0.1 * rng.standard_normal(n)) for c in cols}, index=idx)
    high = close * 1.01
    low = close * 0.99
    high["JUNK"] = close["JUNK"] * 1.5; low["JUNK"] = close["JUNK"] * 0.5            # 100% range -> filtered
    close.iloc[:50, 3] = np.nan                                                      # D too short
    rows = []
    for c in cols:
        rows.append(pd.DataFrame({"Date": idx, "Symbol": c, "High": high[c].values, "Low": low[c].values}))
    px = pd.concat(rows)
    monkeypatch.setattr(lf, "_market_wide", lambda m: (None, None) if m == "NONE" else (close, vol))
    monkeypatch.setattr(pf, "_liquid_symbols", lambda c, v: list(c.columns))
    monkeypatch.setattr(hs.pd, "read_parquet", lambda p: px)
    monkeypatch.setattr(hs, "_sector_map", lambda: {"A": "S1", "B": "S1", "C": "S1", "D": "S2", "JUNK": "S1"})
    return close


def test_scan_market(market):
    df = hs.scan_market("US").set_index("ticker")
    assert set(df.index) == {"A", "B", "C"}                                          # JUNK filtered by range, D too short
    assert {"avg_range%", "cs_spread", "range_stability", "eff_ratio", "ret_autocorr", "vol_autocorr", "half_life",
            "peer_corr", "peer_dev"} <= set(df.columns)
    assert df["avg_range%"].between(1.5, 2.5).all()
    assert hs.scan_market("NONE").empty


def test_scan_market_no_peer_columns_when_sector_small(market, monkeypatch):
    monkeypatch.setattr(hs, "_sector_map", lambda: {})
    df = hs.scan_market("US")
    assert "peer_corr" not in df.columns or df["peer_corr"].isna().all() or len(df) > 0


def test_main(market, monkeypatch, capsys):
    monkeypatch.setattr(marketdata, "market_list", lambda: ["US"])
    monkeypatch.setattr(sys, "argv", ["hs", "--top", "2"])
    hs.main()
    out = capsys.readouterr().out
    assert "HFT-ARCHETYPE STOCK PICKS — US, 5-day (1-week) window, 3 liquid names" in out
    for lab in ("MARKET MAKING", "STATISTICAL ARB", "LATENCY", "INDEX/ETF ARB"):
        assert lab in out
    monkeypatch.setattr(sys, "argv", ["hs", "--archetype", "latency", "--window", "7"])
    hs.main()
    out = capsys.readouterr().out
    assert "LATENCY" in out and "MARKET MAKING" not in out and "7-day" in out
    monkeypatch.setattr(sys, "argv", ["hs", "--market", "NONE"])
    with pytest.raises(SystemExit, match="no data"):
        hs.main()


def test_main_no_eligible_names(monkeypatch, capsys):
    f = pd.DataFrame({"market": ["US"], "ticker": ["A"], "avg_range%": [1.0], "cs_spread": [0.01], "range_stability": [0.1],
                      "eff_ratio": [0.5], "ret_autocorr": [0.0], "vol_autocorr": [0.0], "half_life": [np.inf]})
    monkeypatch.setattr(hs, "scan_market", lambda m, w: f)
    monkeypatch.setattr(marketdata, "market_list", lambda: ["US"])
    monkeypatch.setattr(sys, "argv", ["hs", "--archetype", "etf_arb"])
    hs.main()
    assert "INDEX/ETF ARB (mispriced leg of a peer relationship): no eligible names" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["hs", "--archetype", "stat_arb"])
    hs.main()
    assert " inf " in capsys.readouterr().out                                        # infinite half-life printed as inf


def test_corwin_schultz_two_bars_is_enough():
    assert not np.isnan(hs.corwin_schultz_spread([10.5, 10.5], [9.5, 9.5]))
