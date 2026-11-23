"""fundamental_scan() in the India / Japan / Korea / Europe scanners against an in-memory yfinance Ticker."""
import importlib
import os
import sys
import types

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_scanner_darvas import scan  # noqa: F401,E402  (fixture: imports each scanner with optional deps stubbed)
from test_us_fundamental_scan import _fake_ticker  # noqa: E402

CASES = {
    "full_indian_market_scan": lambda m: m.fundamental_scan("TEST", ".NS"),
    "full_japan_market_scan": lambda m: m.fundamental_scan("7203.T"),
    "full_korea_market_scan": lambda m: m.fundamental_scan("005930", ".KS"),
    "full_european_market_scan": lambda m: m.fundamental_scan("ADS.DE"),
}


@pytest.fixture
def target(scan, monkeypatch):
    if scan.__name__ not in CASES:
        pytest.skip("US covered in test_us_fundamental_scan")
    return scan


def _install(monkeypatch, mod, ticker):
    monkeypatch.setattr(mod.yf, "Ticker", lambda *a, **k: ticker)


def test_strong_company_scores_nine(target, monkeypatch):
    _install(monkeypatch, target, _fake_ticker())
    out = CASES[target.__name__](target)
    score = out.get("f_score", out.get("piotroski"))
    assert score == 9, out


def test_weak_company_not_strong(target, monkeypatch):
    t = _fake_ticker()
    t.income_stmt.loc["Net Income"] = [-10.0, 20.0, 30.0]
    t.cash_flow.loc["Operating Cash Flow"] = [-5.0, 150.0, 120.0]
    _install(monkeypatch, target, t)
    out = CASES[target.__name__](target)
    score = out.get("f_score", out.get("piotroski"))
    assert score is not None and score < 7, out


def test_missing_statements_and_ticker_errors(target, monkeypatch):
    empty = types.SimpleNamespace(income_stmt=pd.DataFrame(), financials=pd.DataFrame(), balance_sheet=pd.DataFrame(),
                                  cash_flow=pd.DataFrame(), cashflow=pd.DataFrame(), quarterly_income_stmt=pd.DataFrame(),
                                  quarterly_financials=pd.DataFrame(), fast_info=types.SimpleNamespace(market_cap=0), info={})
    _install(monkeypatch, target, empty)
    out = CASES[target.__name__](target)
    assert out.get("f_score") is None
    monkeypatch.setattr(target.yf, "Ticker", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("blocked")))
    out = CASES[target.__name__](target)
    assert out.get("f_score") is None and "blocked" in str(out.get("error", out.get("Error", "")))
