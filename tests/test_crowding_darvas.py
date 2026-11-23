"""crowding.py + darvas_volume.py: co-movement crowding score and Darvas×volume monitor (offline)."""
import os
import sys
import types

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import crowding as cr  # noqa: E402
import darvas_volume as dv  # noqa: E402
import marketdata  # noqa: E402


# ───────────────────────────── crowding ─────────────────────────────
def test_corr_to_market():
    rng = np.random.default_rng(0)
    m = pd.Series(rng.normal(0, 0.01, 60))
    assert cr.corr_to_market(m * 2, m) == pytest.approx(1.0)
    assert cr.corr_to_market(-m, m) == pytest.approx(-1.0)
    assert np.isnan(cr.corr_to_market(m.iloc[:19], m.iloc[:19]))             # < 20 obs
    assert not np.isnan(cr.corr_to_market(m.iloc[:20] * 1, m.iloc[:20]))      # exactly 20
    assert np.isnan(cr.corr_to_market(pd.Series(np.ones(40)), m.iloc[:40]))   # zero variance
    s = m.copy(); s.iloc[:30] = np.nan
    assert cr.corr_to_market(s, m) == pytest.approx(1.0)                       # 30 overlapping obs is enough
    s.iloc[:41] = np.nan
    assert np.isnan(cr.corr_to_market(s, m))                                   # only 19 overlapping


def test_rel_strength():
    c = pd.Series(np.linspace(100, 200, 100))
    assert cr.rel_strength(c, 63) == pytest.approx(c.iloc[-1] / c.iloc[-63] - 1)
    assert np.isnan(cr.rel_strength(c.iloc[:63], 63))                          # needs lookback+1 points
    assert not np.isnan(cr.rel_strength(c.iloc[:64], 63))
    bad = c.copy(); bad.iloc[-63] = 0.0
    assert np.isnan(cr.rel_strength(bad, 63))
    withnan = pd.concat([c, pd.Series([np.nan])], ignore_index=True)
    assert cr.rel_strength(withnan, 63) == cr.rel_strength(c, 63)               # NaNs dropped first


def test_crowding_score_weights():
    f = pd.DataFrame({"corr_mkt": [0.1, 0.5, 0.9], "rel_strength": [0.9, 0.5, 0.1]})
    s = cr.crowding_score(f)
    r = [1 / 3, 2 / 3, 1.0]
    assert s.iloc[0] == round(100 * (0.65 * r[0] + 0.35 * r[2]), 1)
    assert s.iloc[2] == round(100 * (0.65 * r[2] + 0.35 * r[0]), 1)
    assert s.iloc[2] > s.iloc[0]                                                # correlation weighs more than run-up
    strs = cr.crowding_score(pd.DataFrame({"corr_mkt": ["0.1", "x"], "rel_strength": [1, 2]}))
    assert strs.notna().sum() >= 1


def _wide(n=200, k=8, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    mk = rng.normal(0, 0.01, n)
    cols = {f"S{i}": 100 * np.cumprod(1 + (0.3 * i / 7) * mk + rng.normal(0, 0.01, n)) for i in range(k)}
    close = pd.DataFrame(cols, index=idx)
    vol = pd.DataFrame(1e6, index=idx, columns=close.columns)
    return close, vol


@pytest.fixture
def liq(monkeypatch):
    import liquidity_factor as lf
    import pead_factor as pf
    close, vol = _wide()
    monkeypatch.setattr(lf, "_market_wide", lambda m: None if m == "NONE" else (close, vol))
    monkeypatch.setattr(pf, "_liquid_symbols", lambda c, v: list(c.columns[:6]))
    return close, vol


def test_crowding_scan_market(liq):
    df = cr.scan_market("US")
    assert len(df) == 6 and set(df.columns) == {"market", "ticker", "corr_mkt", "rel_strength", "crowding"}
    assert df["crowding"].between(0, 100).all()
    assert cr.scan_market("NONE").empty


def test_crowding_scan_drops_non_finite(liq, monkeypatch):
    close, vol = liq
    close.iloc[-100:, 0] = np.nan                                               # S0 has no recent data
    df = cr.scan_market("US")
    assert "S0" not in set(df["ticker"])


def test_crowding_main(liq, monkeypatch, capsys):
    monkeypatch.setattr(marketdata, "market_list", lambda: ["US", "JP"])
    monkeypatch.setattr(sys, "argv", ["cr", "--top", "4"])
    cr.main()
    out = capsys.readouterr().out
    assert "CO-MOVEMENT CROWDING — 12 liquid names" in out and "NOT short-interest" in out
    rows = [l.split() for l in out.splitlines() if l.startswith("  ") and l.split()[0] in ("US", "JP")]
    assert len(rows) == 4 and [float(r[-1]) for r in rows] == sorted((float(r[-1]) for r in rows), reverse=True)
    monkeypatch.setattr(sys, "argv", ["cr", "--market", "NONE"])
    with pytest.raises(SystemExit, match="no data"):
        cr.main()


# ───────────────────────────── darvas_volume ─────────────────────────────
def test_obv_and_ad():
    assert list(dv.obv([1, 2, 2, 1], [10, 20, 30, 40])) == [0, 20, 20, -20]
    h, l, c, v = [10, 10], [0, 0], [10, 0], [5, 5]
    assert list(dv.chaikin_ad(h, l, c, v)) == [5, 0]                              # +1*5 then -1*5
    assert list(dv.chaikin_ad([5], [5], [5], [9])) == [0.0]                       # zero range -> 0


def test_cmf_and_period():
    h, l, c, v = [10, 10, 10], [0, 0, 0], [10, 10, 0], [1, 1, 2]
    assert dv.chaikin_money_flow(h, l, c, v) == pytest.approx((1 + 1 - 2) / 4)
    assert dv.chaikin_money_flow(h, l, c, v, period=2) == pytest.approx((1 - 2) / 3)
    assert np.isnan(dv.chaikin_money_flow(h, l, c, [0, 0, 0]))


def test_up_down_volume_ratio():
    assert dv.up_down_volume_ratio([1, 2, 1], [5, 10, 4]) == pytest.approx(10 / 4)
    assert dv.up_down_volume_ratio([1, 2, 3], [5, 10, 4]) == np.inf
    assert np.isnan(dv.up_down_volume_ratio([1, 1, 1], [5, 10, 4]))


def test_darvas_box_excludes_current_bar_and_confirm():
    high = [10, 11, 12, 11, 10.5, 10.8, 10.2, 11.0, 50.0]                            # last bar spike must be ignored
    low = [9, 9.5, 10, 9.8, 9.6, 9.7, 9.4, 9.9, 49.0]
    b = dv.darvas_box(high, low, lookback=40, confirm=3)
    assert b["top"] == 12 and b["bottom"] == 9.4 and b["held"] is True and b["len"] == 6
    b2 = dv.darvas_box(high, low, lookback=40, confirm=3, exclude_current=False)
    assert b2["top"] == 50.0 and b2["held"] is False
    short = dv.darvas_box([1, 2, 3, 4], [0, 1, 2, 3], confirm=3)                    # 3 usable bars < confirm+2
    assert short["len"] == 0 and np.isnan(short["top"]) and np.isnan(short["bottom"])
    exact = dv.darvas_box([1, 3, 2, 2, 2, 9], [0, 1, 1, 1, 1, 9], confirm=3)       # 5 usable bars = confirm+2 -> ok
    assert exact["top"] == 3 and exact["held"] is True


def test_darvas_box_not_held_when_top_is_recent():
    b = dv.darvas_box([5, 6, 7, 8, 9, 3], [4, 5, 6, 7, 8, 1], confirm=3)           # top is the last usable bar
    assert b["top"] == 9 and b["held"] is False and b["len"] == 1
    b = dv.darvas_box([5, 6, 7, 9, 8, 7, 3], [4, 5, 6, 7, 7, 6, 1], confirm=3)      # only 2 bars after the top
    assert b["held"] is False
    b = dv.darvas_box([5, 6, 9, 8, 7, 6, 3], [4, 5, 6, 7, 6, 5, 1], confirm=3)       # exactly 3 bars after the top
    assert b["held"] is True and b["bottom"] == 5


def test_box_state_classification():
    box = {"top": 110.0, "bottom": 100.0}
    s = dv.box_state(111.0, 112, 109, box, 200.0, 100.0)
    assert s == {"state": "breakout", "position": 1.1, "vol_confirmed": True}
    assert dv.box_state(110.0, 0, 0, box, 1, 1)["state"] == "in_box"                  # equals top -> still in box
    assert dv.box_state(100.0, 0, 0, box, 1, 1)["state"] == "in_box"
    d = dv.box_state(95.0, 0, 0, box, 1, 1)
    assert d["state"] == "breakdown" and d["position"] == 0.0                         # clipped at 0
    assert dv.box_state(105.0, 0, 0, box, 150.0, 100.0)["vol_confirmed"] is True      # exactly 1.5x
    assert dv.box_state(105.0, 0, 0, box, 149.9, 100.0)["vol_confirmed"] is False
    assert dv.box_state(105.0, 0, 0, box, 5.0, 0.0)["vol_confirmed"] is False
    for bad in ({"top": np.nan, "bottom": 1.0}, {"top": 5.0, "bottom": 5.0}, {"top": 4.0, "bottom": 5.0}):
        assert dv.box_state(5.0, 0, 0, bad, 1, 1)["state"] == "no_box"
    assert dv.box_state(200.0, 0, 0, box, 1, 1)["position"] == 1.2                    # clipped at 1.2


def test_accumulation_features_and_score():
    rng = np.random.default_rng(1)
    n = 40
    c = 100 + np.cumsum(rng.normal(0.2, 0.5, n)); h = c + 1; l = c - 1; v = np.linspace(1e5, 2e5, n)
    f = dv.accumulation_features(h, l, c, v)
    assert set(f) == {"obv_trend", "ad_trend", "cmf", "ud_vol_ratio", "vol_trend"} and f["vol_trend"] == pytest.approx(1.0)
    feat = pd.DataFrame({"obv_trend": [0.9, 0.1, -0.5], "ad_trend": [0.8, 0.0, -0.4], "cmf": [0.3, 0.0, -0.2],
                         "ud_vol_ratio": [3.0, np.inf, 0.4], "vol_trend": [0.5, 0.1, -0.3], "eff_ratio": [0.1, 0.5, 0.9]})
    s = dv.accumulation_score(feat)
    assert s.iloc[0] > s.iloc[2] and s.dtype == float
    no_eff = dv.accumulation_score(feat.drop(columns=["eff_ratio"]))
    assert not np.allclose(s.values, no_eff.values, equal_nan=True)


def _market_data(n=120):
    rng = np.random.default_rng(2)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    cols = ["A", "B", "C"]
    close = pd.DataFrame({c: 100 + np.cumsum(rng.normal(0.1, 0.5, n)) for c in cols}, index=idx)
    # A: coils; B: breaks out on huge volume; C: breaks down
    close.iloc[-1, 1] = close.iloc[:-1, 1].max() + 20
    close.iloc[-1, 2] = close.iloc[:-1, 2].min() - 20
    vol = pd.DataFrame(1e6, index=idx, columns=cols)
    vol.iloc[-1, 1] = 5e6
    rows = []
    for c in cols:
        rows.append(pd.DataFrame({"Date": idx, "Symbol": c, "High": close[c].values + 0.5, "Low": close[c].values - 0.5}))
    return close, vol, pd.concat(rows, ignore_index=True)


@pytest.fixture
def dvm(monkeypatch):
    import liquidity_factor as lf
    import pead_factor as pf
    close, vol, px = _market_data()
    monkeypatch.setattr(lf, "_market_wide", lambda m: (None, None) if m == "NONE" else (close, vol))
    monkeypatch.setattr(pf, "_liquid_symbols", lambda c, v: list(c.columns))
    monkeypatch.setattr(dv.pd, "read_parquet", lambda p: px)
    return close, vol, px


def test_darvas_scan_market_states(dvm):
    df = dv.scan_market("US").set_index("ticker")
    assert df.loc["B", "state"] == "breakout" and bool(df.loc["B", "vol_confirmed"]) is True
    assert df.loc["C", "state"] == "breakdown"
    assert "accumulation" in df.columns and df.loc["B", "box_top"] > df.loc["B", "box_bottom"]
    assert dv.scan_market("NONE").empty


def test_darvas_scan_skips_short_history(dvm):
    close, vol, px = dvm
    close.iloc[:-30, 0] = np.nan                                                    # A has < lookback+5 obs
    assert "A" not in set(dv.scan_market("US")["ticker"])


def _main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["dv", *argv])
    dv.main()


def test_darvas_main_states(dvm, monkeypatch, capsys):
    monkeypatch.setattr(marketdata, "market_list", lambda: ["US"])
    _main(monkeypatch, "--state", "breakout")
    out = capsys.readouterr().out
    assert "VOLUME-CONFIRMED BREAKOUTS" in out and "1 names" in out
    _main(monkeypatch, "--state", "breakdown", "--market", "US")
    assert "BREAKDOWNS (box bottom lost)  ·  1 names" in capsys.readouterr().out
    _main(monkeypatch)
    out = capsys.readouterr().out
    assert "COILING IN BOX" in out and "DARVAS × VOLUME-ACQUISITION MONITOR — US" in out


def test_darvas_main_no_data(dvm, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["dv", "--market", "NONE"])
    with pytest.raises(SystemExit, match="no data"):
        dv.main()
