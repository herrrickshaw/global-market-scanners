"""Pure pieces of pit_backtest.py: the 60-day breakout proxy and the monthly
return metrics (hand-computed). The main() backtest needs Cassandra/yfinance and
is not exercised here."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pit_backtest import BREAKOUT_LB, darvas_breakout, metrics  # noqa: E402


def _closes(last):
    idx = pd.bdate_range("2024-01-01", periods=BREAKOUT_LB + 1)
    vals = list(np.linspace(90, 100, BREAKOUT_LB)) + [last]
    return pd.Series(vals, index=idx)


def test_breakout_is_new_high_vs_prior_window_only():
    s = _closes(101.0)
    assert darvas_breakout(s, s.index[-1]) is True
    assert darvas_breakout(_closes(100.0), s.index[-1]) is True     # ties count (>=)
    assert darvas_breakout(_closes(99.9), s.index[-1]) is False


def test_breakout_needs_full_lookback():
    s = _closes(200.0)
    assert darvas_breakout(s, s.index[BREAKOUT_LB - 1]) is False


def test_metrics_hand_computed():
    m = metrics([10.0, -10.0])
    # equity 1.1 * 0.9 = 0.99; annualised 0.99**6 - 1 = -5.85%; drawdown -10%
    assert m["n_months"] == 2
    assert m["avg_mth%"] == pytest.approx(0.0)
    assert m["ann_return%"] == pytest.approx((0.99 ** 6 - 1) * 100, abs=0.01)
    assert m["hit_rate%"] == pytest.approx(50.0)
    assert m["max_dd%"] == pytest.approx(-10.0)
    assert metrics([]) == {"n_months": 0}
    assert metrics([1.0, 1.0])["sharpe"] == 0                      # zero variance guard
