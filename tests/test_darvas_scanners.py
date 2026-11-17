"""
Darvas box in the five full-market scanners (full_<market>_market_scan.py).

compute_darvas_box() is the signal the scanners export. These tests pin the box
geometry on a hand-built series and prove that a missing latest bar (NaN close,
as yfinance returns for a session it has not published yet or a halted name)
does not turn into a price of 0 and a false BREAKDOWN_SELL.

No network: the India/Korea scanners import nsepython / bseindia / pykrx at
module load, so those are stubbed with empty modules before import.
"""
import importlib
import os
import sys
import types

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MARKETS = ["full_us_market_scan", "full_indian_market_scan", "full_european_market_scan",
           "full_japan_market_scan", "full_korea_market_scan"]


def _stub_market_libs():
    """Empty stand-ins for the market-data libraries imported at module load."""
    nse = types.ModuleType("nsepython")
    nse.get_bhavcopy = lambda *a, **k: None
    bse = types.ModuleType("bseindia")
    bse_lib = types.ModuleType("bseindia.libutil")
    bse_lib.SECURITY_MASTER_URL = "http://invalid.test/"
    bse.libutil = bse_lib
    pykrx = types.ModuleType("pykrx")
    pykrx.stock = types.ModuleType("pykrx.stock")
    for name, mod in {"nsepython": nse, "bseindia": bse, "bseindia.libutil": bse_lib,
                      "pykrx": pykrx, "pykrx.stock": pykrx.stock}.items():
        sys.modules.setdefault(name, mod)


@pytest.fixture(params=MARKETS)
def darvas(request):
    _stub_market_libs()
    return importlib.import_module(request.param).compute_darvas_box


def _box_frame():
    """20 bars: a base at 10, a spike to 15, then a box with highs 14->13, lows 12.
    Box (current bar excluded): top = 14 (index 11, held by the next 3 highs of 13),
    bottom = 12 (no low strictly held above, falls back to min low of the segment).
    Last close 13 -> IN_BOX."""
    highs = [10.0] * 10 + [15.0, 14.0] + [13.0] * 8
    lows = [9.0] * 10 + [12.0] * 10
    closes = [9.5] * 10 + [14.0, 13.5] + [13.0] * 8
    return pd.DataFrame({"Open": closes, "High": highs, "Low": lows, "Close": closes,
                         "Volume": [1000] * 20})


def test_box_geometry_on_clean_series(darvas):
    out = darvas(_box_frame(), confirm=3)
    assert out["signal"] == "IN_BOX"
    assert out["box_top"] == pytest.approx(14.0)
    assert out["box_bottom"] == pytest.approx(12.0)


def test_breakout_and_breakdown_classification(darvas):
    up = _box_frame()
    up.loc[len(up)] = {"Open": 14.5, "High": 14.6, "Low": 14.0, "Close": 14.5, "Volume": 1000}
    assert darvas(up, confirm=3)["signal"] == "BREAKOUT_BUY"
    down = _box_frame()
    down.loc[len(down)] = {"Open": 11.5, "High": 11.8, "Low": 11.0, "Close": 11.5, "Volume": 1000}
    assert darvas(down, confirm=3)["signal"] == "BREAKDOWN_SELL"


def test_too_short_history_is_insufficient(darvas):
    assert darvas(_box_frame().head(5), confirm=3)["signal"] == "INSUFFICIENT_DATA"


def test_missing_latest_bar_is_not_a_breakdown(darvas):
    """Prove-it: a trailing all-NaN bar used to be zero-filled, so current price = 0
    and the stock was flagged BREAKDOWN_SELL. The missing bar must be ignored and the
    last real bar (close 13, inside the 12-14 box) classified instead."""
    df = _box_frame()
    df.loc[len(df)] = {"Open": np.nan, "High": np.nan, "Low": np.nan, "Close": np.nan,
                       "Volume": np.nan}
    out = darvas(df, confirm=3)
    assert out["signal"] == "IN_BOX"
    assert out["box_top"] == pytest.approx(14.0)
    assert out["box_bottom"] == pytest.approx(12.0)


def test_missing_latest_close_only_is_not_a_breakdown(darvas):
    df = _box_frame()
    df.loc[len(df)] = {"Open": 13.0, "High": 13.2, "Low": 12.8, "Close": np.nan, "Volume": 0}
    assert darvas(df, confirm=3)["signal"] == "IN_BOX"
