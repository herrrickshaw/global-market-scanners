"""Characterisation of the shared Darvas engine.

``golden_darvas.json`` was produced by the ORIGINAL per-market ``compute_darvas_box``
implementations (pre-refactor, commit on main) over the synthetic panels in ``_panels.py``;
the refactor must reproduce it exactly, including dict key order. No network is used.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _panels import build_panels  # noqa: E402

from scanner_core import darvas  # noqa: E402

GOLDEN = json.loads((Path(__file__).parent / "golden_darvas.json").read_text())
MODULES = {"us": "full_us_market_scan", "india": "full_indian_market_scan",
           "japan": "full_japan_market_scan", "korea": "full_korea_market_scan",
           "europe": "full_european_market_scan"}
GOLDEN_KEYS = {"us": "us", "india": "indian", "japan": "japan", "korea": "korea",
               "europe": "european"}
PANELS = build_panels()


def _ser(obj) -> str:
    return json.dumps(obj, default=float, allow_nan=True)


@pytest.mark.parametrize("market", sorted(MODULES))
def test_profile_reproduces_original_outputs(market):
    for name, df in PANELS.items():
        expected = GOLDEN[GOLDEN_KEYS[market]][name]
        try:
            got = darvas.get_profile(market).compute(df)
        except KeyError:
            got = {"__error__": "KeyError"}
        assert _ser(got) == _ser(expected), f"{market}/{name}"


@pytest.mark.parametrize("market", sorted(MODULES))
def test_scanner_module_wrapper_matches_golden(market):
    mod = importlib.import_module(MODULES[market])
    for name in ("walk_3", "breakout", "short", "zeros_nan"):
        expected = GOLDEN[GOLDEN_KEYS[market]][name]
        assert _ser(mod.compute_darvas_box(PANELS[name])) == _ser(expected)


def test_locate_box_latest_confirmed_swing_high():
    highs = [10, 11, 12, 13, 12, 11, 11.5, 11.2, 11.1, 11.0]
    lows = [h - 1 for h in highs]
    assert darvas.locate_box(highs, lows, 3) == (11.5, 10.0)


def test_last_bar_spike_does_not_move_box():
    import pandas as pd
    hi = [10, 11, 12, 13, 12, 11, 11.5, 11.2, 11.1, 11.0, 11.0, 11.0]
    df = pd.DataFrame({"High": hi, "Low": [h - 1 for h in hi], "Close": [h - 0.5 for h in hi]})
    spiked = df.copy()
    spiked.loc[spiked.index[-1], "High"] = 500.0  # current bar must be excluded from the box
    for market in darvas.available_markets():
        assert (darvas.get_profile(market).compute(df)["box_top"]
                == darvas.get_profile(market).compute(spiked)["box_top"] != 500.0)


def test_registry_covers_five_markets_and_rejects_unknown():
    assert darvas.available_markets() == ["europe", "india", "japan", "korea", "us"]
    with pytest.raises(KeyError):
        darvas.get_profile("mars")


def test_signal_classification():
    assert darvas.classify(14, 13, 10) == "BREAKOUT_BUY"
    assert darvas.classify(9, 13, 10) == "BREAKDOWN_SELL"
    assert darvas.classify(11, 13, 10) == "IN_BOX"


def test_duplicate_statement_helpers_are_the_shared_ones():
    import stock_utils
    for m in ("full_japan_market_scan", "full_korea_market_scan", "full_european_market_scan"):
        mod = importlib.import_module(m)
        assert mod._first_df is stock_utils.first_df
        assert mod._row is stock_utils.row
        assert mod._series is stock_utils.series
