"""Pure numeric / extraction helpers in stock_utils.py (hand-computed values)."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stock_utils import cagr, fin_metric, normalise_debt_to_equity, pct_change, row, series  # noqa: E402


def test_pct_change_uses_abs_base_and_guards_zero():
    assert pct_change(110, 100) == pytest.approx(10.0)
    assert pct_change(-50, -100) == pytest.approx(50.0)      # loss narrowed = improvement
    assert pct_change(5, 0) is None and pct_change(None, 3) is None


def test_cagr_hand_computed():
    assert cagr(121, 100, 2) == pytest.approx(10.0)
    assert cagr(100, 0, 3) is None and cagr(100, 50, 0) is None


def test_cagr_negative_latest_is_undefined_not_complex():
    """Prove-it: (-0.5)**(1/3) is a complex number in Python; cagr() returned
    (-60.3+68.7j) for a value that turned negative."""
    out = cagr(-50, 100, 3)
    assert out is None


def test_normalise_debt_to_equity_threshold():
    assert normalise_debt_to_equity(45.2) == pytest.approx(0.452)
    assert normalise_debt_to_equity(0.8) == pytest.approx(0.8)
    assert normalise_debt_to_equity(None) is None


def test_row_and_series_extraction():
    df = pd.DataFrame({"2024": [10.0, np.nan], "2023": [8.0, 5.0], "2022": [np.nan, 4.0]},
                      index=["Net Income", "Total Revenue"])
    assert row(df, "Missing", "Net Income") == 10.0
    assert row(df, "Total Revenue") is None                  # NaN at col 0
    assert row(df, "Total Revenue", col=1) == 5.0
    assert series(df, "Net Income") == [10.0, 8.0]           # NaN dropped
    assert row(None, "x") is None and series(pd.DataFrame(), "x") == []
    assert fin_metric(df, ("Nope",), default=-1) == -1
