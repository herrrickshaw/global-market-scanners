"""
fundamental_scan() in full_us_market_scan.py: Piotroski F-score, US Coffee Can,
Magic Formula and Bull Cartel on one yfinance Ticker. yf.Ticker is replaced by an
in-memory fake (no network); every expected value below is hand-computed.
"""
import os
import sys
import types

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import full_us_market_scan as us  # noqa: E402

YEARS = ["2024-12-31", "2023-12-31", "2022-12-31"]      # newest first, like yfinance
QTRS = ["2024-12-31", "2024-09-30", "2024-06-30", "2024-03-31", "2023-12-31"]


def _frame(rows, cols):
    return pd.DataFrame({c: [v[i] for v in rows.values()] for i, c in enumerate(cols)},
                        index=list(rows.keys()))


def _fake_ticker(revenue=(1250.0, 1100.0, 1000.0), info_overrides=None):
    t = types.SimpleNamespace()
    t.income_stmt = _frame({
        "Total Revenue": list(revenue),
        "Net Income": [150.0, 120.0, 100.0],
        "Gross Profit": [500.0, 420.0, 380.0],
        "EBIT": [300.0, 250.0, 200.0],
    }, YEARS)
    t.balance_sheet = _frame({
        "Total Assets": [1000.0, 1000.0, 1000.0],
        "Long Term Debt": [100.0, 150.0, 150.0],
        "Current Assets": [400.0, 300.0, 300.0],
        "Current Liabilities": [200.0, 200.0, 200.0],
        "Share Issued": [100.0, 100.0, 100.0],
        "Stockholders Equity": [800.0, 700.0, 600.0],
    }, YEARS)
    t.cash_flow = _frame({
        "Operating Cash Flow": [200.0, 150.0, 120.0],
        "Free Cash Flow": [120.0, 90.0, 70.0],
    }, YEARS)
    t.quarterly_income_stmt = _frame({
        "Total Revenue": [130e6, 120e6, 110e6, 105e6, 100e6],
        "Net Income": [3e6, 2.8e6, 2.5e6, 2.2e6, 2e6],
    }, QTRS)
    t.fast_info = types.SimpleNamespace(market_cap=2e9)
    t.info = {"shortName": "Test Co", "sector": "Industrials", "debtToEquity": 12.5,
              "totalDebt": 100.0, "totalCash": 50.0, "bookValue": 8.0}
    t.info.update(info_overrides or {})
    return t


@pytest.fixture
def scan(monkeypatch):
    def _run(**kw):
        fake = _fake_ticker(**kw)
        monkeypatch.setattr(us.yf, "Ticker", lambda sym: fake)
        return us.fundamental_scan("TEST")
    return _run


def test_piotroski_all_nine_points(scan):
    # roa .15>0; ocf 200>0; roa .15>.12; ocf/a .20>.15; ltd/a .10<.15; cur 2.0>1.5;
    # shares 100<=100; gm .400>.382; turnover 1.25>1.10  -> 9
    out = scan()
    assert out["f_score"] == 9 and out["f_strong"] is True


def test_coffee_can_six_of_six(scan):
    out = scan()
    # revenue CAGR (1250/1000)^(1/2)-1 = 11.80%; ROE avg (18.75+17.14+16.67)/3 = 17.52%;
    # D/E 12.5% -> 0.125x; mcap 2e9; NI all > 0; FCF 120 > 0
    assert out["Revenue_CAGR_%"] == pytest.approx(11.80, abs=0.01)
    assert out["ROE_avg_%"] == pytest.approx(17.52, abs=0.01)
    assert out["cc_score"] == "6/6" and out["qualifies_cc"] is True


def test_coffee_can_fails_on_revenue_cagr_below_ten_percent(scan):
    out = scan(revenue=(1200.0, 1100.0, 1000.0))        # (1.2)^(1/2)-1 = 9.54%, needs > 10
    assert out["Revenue_CAGR_%"] == pytest.approx(9.54, abs=0.01)
    assert out["cc_score"] == "5/6" and out["qualifies_cc"] is False


def test_magic_formula_needs_earnings_yield(scan):
    out = scan()
    # ROIC = EBIT 300 / (assets 1000 - current liabilities 200) = 37.5%;
    # EY = 300 / (2e9 + 100 - 50) ~ 0% -> does not qualify
    assert out["ROIC_%"] == pytest.approx(37.5)
    assert out["qualifies_mf"] is False


def test_bull_cartel_yoy_quarter(scan):
    out = scan()
    # sales (130-100)/100 = 30% > 15; profit (3-2)/2 = 50% > 20; NI $3M > $1M
    assert out["Sales_Growth_YoY_%"] == pytest.approx(30.0)
    assert out["Profit_Growth_YoY_%"] == pytest.approx(50.0)
    assert out["qualifies_bc"] is True


def test_negative_latest_revenue_does_not_crash_the_scan(scan):
    """Prove-it: a negative latest revenue made the CAGR a complex number
    ((-0.05)**0.5), and `cagr > 10` then raised TypeError, so the whole
    symbol fell out of the scan. CAGR is undefined across a sign change."""
    out = scan(revenue=(-50.0, 1100.0, 1000.0))
    assert out["Revenue_CAGR_%"] is None
    assert out["qualifies_cc"] is False
    assert out["f_score"] is not None
