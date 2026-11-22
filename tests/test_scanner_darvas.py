"""compute_darvas_box / compute_golden_crossover in the five full_*_market_scan modules (optional deps faked)."""
import importlib
import os
import sys
import types

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MODS = ["full_us_market_scan", "full_indian_market_scan", "full_japan_market_scan",
        "full_korea_market_scan", "full_european_market_scan"]


@pytest.fixture(params=MODS)
def scan(request, monkeypatch):
    for name in ("pykrx", "nsepython", "bseindia", "bseindia.libutil"):
        if name not in sys.modules:
            m = types.ModuleType(name)
            m.stock = types.SimpleNamespace()
            m.get_bhavcopy = lambda *a, **k: None
            m.SECURITY_MASTER_URL = "http://x"
            monkeypatch.setitem(sys.modules, name, m)
    sys.modules["bseindia"].libutil = sys.modules["bseindia.libutil"]
    sys.modules["pykrx"].stock = types.SimpleNamespace()
    monkeypatch.delitem(sys.modules, request.param, raising=False)
    mod = importlib.import_module(request.param)
    yield mod
    sys.modules.pop(request.param, None)


def _df(highs, lows, closes):
    return pd.DataFrame({"High": highs, "Low": lows, "Close": closes})


def _box_df(last_close):
    # idx0 spike high 20 held lower for 3+ bars; lows: first low 10 held higher for 3 bars
    highs = [15, 20, 19, 19, 19, 19, 19, 19, 19]
    lows = [12, 12, 10, 11, 11, 11, 11, 11, 11]
    closes = [13, 14, 12, 12, 12, 12, 12, 12, last_close]
    return _df(highs, lows, closes)


def test_darvas_insufficient_data(scan):
    assert scan.compute_darvas_box(_df([1, 2], [1, 1], [1, 2]))["signal"] == "INSUFFICIENT_DATA"
    nan = _df([np.nan] * 10, [np.nan] * 10, [np.nan] * 10)
    assert scan.compute_darvas_box(nan)["signal"] == "INSUFFICIENT_DATA"


def test_darvas_states(scan):
    inbox = scan.compute_darvas_box(_box_df(15))
    assert inbox["signal"] == "IN_BOX" and inbox["box_top"] == 20 and inbox["box_bottom"] == 10
    pos = inbox.get("position_in_box_pct", inbox.get("pos_in_box"))
    up = inbox.get("upside_to_top_pct", inbox.get("upside_pct"))
    assert pos == 50.0 and up == pytest.approx(33.33)
    assert scan.compute_darvas_box(_box_df(21))["signal"] == "BREAKOUT_BUY"
    assert scan.compute_darvas_box(_box_df(9))["signal"] == "BREAKDOWN_SELL"
    assert scan.compute_darvas_box(_box_df(20))["signal"] == "IN_BOX"          # equal to top is not a breakout
    assert scan.compute_darvas_box(_box_df(10))["signal"] == "IN_BOX"          # equal to bottom is not a breakdown


def test_darvas_excludes_current_bar_and_no_box(scan):
    df = _box_df(15)
    df.loc[df.index[-1], "High"] = 99                                          # today's spike must not become the box top
    assert scan.compute_darvas_box(df)["box_top"] == 20
    flat = _df(list(range(1, 11)), list(range(0, 10)), list(range(1, 11)))     # rising highs: nothing holds
    assert scan.compute_darvas_box(flat)["signal"] == "NO_BOX"


def test_golden_crossover(scan):
    if not hasattr(scan, "compute_golden_crossover"):
        pytest.skip("module has no golden-crossover helper")
    assert scan.compute_golden_crossover(None)["gc_signal"] is False
    assert scan.compute_golden_crossover(pd.DataFrame({"Close": range(200)}))["dma50"] is None      # needs 201 bars
    n = 400
    c = np.r_[np.linspace(200, 100, 300), np.linspace(100, 400, 100)]
    hits = []
    for cut in range(330, n + 1):
        r = scan.compute_golden_crossover(pd.DataFrame({"Close": c[:cut]}))
        hits.append(r["gc_signal"])
        assert r["dma50_above_200"] == (r["dma50"] > r["dma200"])
    assert sum(hits) == 1                                                       # a single cross event
    up = scan.compute_golden_crossover(pd.DataFrame({"Close": np.linspace(1, 100, 300)}))
    assert up["gc_signal"] is False and up["dma50_above_200"] is True and up["dma_gap_%"] > 0
