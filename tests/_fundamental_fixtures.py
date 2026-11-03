"""Synthetic yfinance-like tickers for characterising each market's ``fundamental_scan``."""
from __future__ import annotations

import json
import math

import pandas as pd

COLS = pd.to_datetime(["2024-12-31", "2023-12-31", "2022-12-31", "2021-12-31", "2020-12-31"])
QCOLS = pd.to_datetime(["2025-03-31", "2024-12-31", "2024-09-30", "2024-06-30", "2024-03-31"])


def _df(rows: dict, cols=COLS):
    return pd.DataFrame({k: list(v) + [math.nan] * (len(cols) - len(v)) for k, v in rows.items()},
                        index=cols).T


class Boom:
    """Attribute whose access raises (simulates a yfinance network failure)."""
    def __get__(self, obj, typ=None):
        raise RuntimeError("boom")


class FakeFast:
    def __init__(self, mcap):
        self.market_cap = mcap


class FakeTicker:
    income_stmt = balance_sheet = cash_flow = quarterly_income_stmt = None
    financials = cashflow = quarterly_financials = None

    def __init__(self, inc=None, bal=None, cf=None, inc_q=None, info=None, mcap=0,
                 info_boom=False, fast_boom=False):
        self.income_stmt, self.balance_sheet, self.cash_flow = inc, bal, cf
        self.quarterly_income_stmt = inc_q
        self._info, self._mcap = info, mcap
        self._info_boom, self._fast_boom = info_boom, fast_boom

    @property
    def info(self):
        if self._info_boom:
            raise RuntimeError("boom")
        return self._info

    @property
    def fast_info(self):
        if self._fast_boom:
            raise RuntimeError("boom")
        return FakeFast(self._mcap)


def _strong():
    inc = _df({"Net Income": [120, 100, 80, 60, 50], "Total Revenue": [1500, 1200, 900, 700, 500],
               "Gross Profit": [700, 500, 350, 250, 180], "EBIT": [200, 160, 120, 90, 70]})
    bal = _df({"Total Assets": [1000, 950, 900, 850, 800], "Long Term Debt": [50, 80, 90, 100, 100],
               "Current Assets": [600, 500, 450, 400, 380],
               "Current Liabilities": [250, 260, 270, 280, 290],
               "Share Issued": [100, 100, 100, 100, 100],
               "Stockholders Equity": [700, 650, 600, 550, 500]})
    cf = _df({"Operating Cash Flow": [200, 150, 120, 90, 70], "Free Cash Flow": [150, 110, 80, 60, 40],
              "Capital Expenditure": [-50, -40, -40, -30, -30]})
    inc_q = _df({"Total Revenue": [5e9, 4e9, 3.9e9, 3.8e9, 3e9], "Net Income": [5e8, 4e8, 3e8, 3e8, 2e8]},
                QCOLS)
    return inc, bal, cf, inc_q


def _weak():
    inc = _df({"Net Income": [-10, 5, 8, 9, 10], "Total Revenue": [400, 450, 500, 520, 540],
               "Gross Profit": [100, 140, 170, 180, 190]})
    bal = _df({"Total Assets": [1000, 900, 850, 800, 800], "Long Term Debt": [400, 300, 250, 200, 200],
               "Current Assets": [300, 400, 420, 430, 440],
               "Current Liabilities": [350, 300, 290, 280, 270],
               "Share Issued": [120, 100, 100, 100, 100],
               "Stockholders Equity": [100, 150, 160, 170, 180]})
    cf = _df({"Operating Cash Flow": [-5, 10, 12, 14, 15], "Capital Expenditure": [-30, -20, -20, -20, -20]})
    inc_q = _df({"Total Revenue": [90, 100, 105, 110, 100], "Net Income": [-1, 1, 2, 2, 3]}, QCOLS)
    return inc, bal, cf, inc_q


def variants():
    s_inc, s_bal, s_cf, s_q = _strong()
    w_inc, w_bal, w_cf, w_q = _weak()
    big = {"debtToEquity": 35.0, "totalDebt": 100.0, "totalCash": 300.0, "bookValue": 12.0,
           "shortName": "Strong Co", "sector": "Tech", "trailingPE": 18.5, "pegRatio": 1.4,
           "dividendYield": 0.02, "priceToBook": 3.3, "marketCap": 3e9}
    return {
        "strong": dict(inc=s_inc, bal=s_bal, cf=s_cf, inc_q=s_q, info=dict(big), mcap=3e12),
        "strong_small_mcap": dict(inc=s_inc, bal=s_bal, cf=s_cf, inc_q=s_q,
                                  info={**big, "marketCap": 1e6}, mcap=1e6),
        "strong_no_de_info": dict(inc=s_inc, bal=s_bal, cf=s_cf, inc_q=s_q,
                                  info={"regularMarketPrice": 50.0, "trailingEps": 2.5}, mcap=2e12),
        "strong_de_small": dict(inc=s_inc, bal=s_bal, cf=s_cf, inc_q=s_q,
                                info={**big, "debtToEquity": 0.4, "dividendYield": 4.0}, mcap=5e11),
        "strong_no_fcf_row": dict(inc=s_inc, bal=s_bal, cf=s_cf.drop("Free Cash Flow"), inc_q=s_q,
                                  info=dict(big), mcap=3e12),
        "strong_high_roe": dict(inc=s_inc.mul(pd.Series({"Net Income": 1.5}), axis=0).fillna(s_inc),
                                bal=s_bal, cf=s_cf, inc_q=s_q, info=dict(big), mcap=3e12),
        "weak": dict(inc=w_inc, bal=w_bal, cf=w_cf, inc_q=w_q, info={"debtToEquity": 250.0}, mcap=1e8),
        "inc_only": dict(inc=s_inc, info={}, mcap=0),
        "no_inc": dict(bal=s_bal, cf=s_cf, inc_q=s_q, info=dict(big), mcap=1e9),
        "no_quarterly": dict(inc=s_inc, bal=s_bal, cf=s_cf, info=dict(big), mcap=3e12),
        "short_quarterly": dict(inc=s_inc, bal=s_bal, cf=s_cf, inc_q=s_q.iloc[:, :3], info=dict(big),
                                mcap=3e12),
        "info_boom": dict(inc=s_inc, bal=s_bal, cf=s_cf, inc_q=s_q, mcap=3e12, info_boom=True),
        "fast_boom": dict(inc=s_inc, bal=s_bal, cf=s_cf, inc_q=s_q, info=dict(big), fast_boom=True),
        "info_none": dict(inc=s_inc, bal=s_bal, cf=s_cf, inc_q=s_q, info=None, mcap=3e12),
        "empty_inc": dict(inc=pd.DataFrame(), bal=s_bal, cf=s_cf, info={}, mcap=1),
    }


def build(name):
    return FakeTicker(**variants()[name])


class RaisingTicker:
    def __init__(self, *a, **k):
        raise RuntimeError("ctor failed " + "x" * 120)


def ser(obj) -> str:
    return json.dumps(obj, default=str, allow_nan=True)
