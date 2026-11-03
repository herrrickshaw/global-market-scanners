"""Pure fundamental-screen building blocks shared by the per-market scanners.

Each function takes already-fetched yfinance statement frames (rows = line items,
columns = periods, newest first) and returns plain numbers, so the per-market
``fundamental_scan`` functions only fetch, call these, and assemble their own result keys.
"""
from __future__ import annotations

from typing import NamedTuple, Optional

import pandas as pd

from stock_utils import first_df, row, series

EQUITY_ROWS = ("Stockholders Equity", "Total Stockholder Equity",
               "Total Equity Gross Minority Interest")
OCF_ROWS = ("Operating Cash Flow", "Total Cash From Operating Activities")


class Statements(NamedTuple):
    """Raw inputs for one symbol: annual/quarterly frames, market cap, yfinance ``info``."""
    inc: Optional[pd.DataFrame]
    bal: Optional[pd.DataFrame]
    cf: Optional[pd.DataFrame]
    inc_q: Optional[pd.DataFrame]
    mcap: float
    info: dict


def load_statements(ticker) -> Statements:
    """Read statements, market cap and info from a ticker; tolerate fast_info/info failures."""
    inc = first_df(ticker, "income_stmt", "financials")
    bal = first_df(ticker, "balance_sheet")
    cf = first_df(ticker, "cash_flow", "cashflow")
    inc_q = first_df(ticker, "quarterly_income_stmt", "quarterly_financials")
    try:
        mcap = ticker.fast_info.market_cap or 0
    except Exception:  # yfinance raises assorted errors when fast_info is unavailable
        mcap = 0
    try:
        info = ticker.info or {}
    except Exception:  # info is a network call; absence is not fatal
        info = {}
    return Statements(inc, bal, cf, inc_q, mcap, info)


def piotroski_f_score(inc, bal, cf) -> int:
    """Nine-point Piotroski F-Score (0-9) from annual income, balance and cash-flow frames."""
    ni0 = row(inc, "Net Income", col=0); a0 = row(bal, "Total Assets", col=0)
    ni1 = row(inc, "Net Income", col=1); a1 = row(bal, "Total Assets", col=1)
    roa0 = (ni0 / a0) if (ni0 and a0) else None
    roa1 = (ni1 / a1) if (ni1 and a1) else None
    ocf0 = row(cf, *OCF_ROWS)
    ltd0 = row(bal, "Long Term Debt", col=0) or 0
    ltd1 = row(bal, "Long Term Debt", col=1) or 0
    ca0 = row(bal, "Current Assets", "Total Current Assets", col=0)
    cl0 = row(bal, "Current Liabilities", "Total Current Liabilities", col=0)
    ca1 = row(bal, "Current Assets", "Total Current Assets", col=1)
    cl1 = row(bal, "Current Liabilities", "Total Current Liabilities", col=1)
    sh0 = row(bal, "Share Issued", col=0); sh1 = row(bal, "Share Issued", col=1)
    rev0 = row(inc, "Total Revenue", col=0); gp0 = row(inc, "Gross Profit", col=0)
    rev1 = row(inc, "Total Revenue", col=1); gp1 = row(inc, "Gross Profit", col=1)
    return (
        (1 if (roa0 and roa0 > 0) else 0) +
        (1 if (ocf0 and ocf0 > 0) else 0) +
        (1 if (roa0 and roa1 and roa0 > roa1) else 0) +
        (1 if (ocf0 and a0 and roa0 and (ocf0 / a0) > roa0) else 0) +
        (1 if (a0 and a1 and (ltd0 / a0) < (ltd1 / a1)) else 0) +
        (1 if (ca0 and cl0 and ca1 and cl1 and (ca0 / cl0) > (ca1 / cl1)) else 0) +
        ((1 if sh0 <= sh1 else 0) if (sh0 and sh1) else 1) +
        (1 if (gp0 and rev0 and gp1 and rev1 and (gp0 / rev0) > (gp1 / rev1)) else 0) +
        (1 if (rev0 and a0 and rev1 and a1 and (rev0 / a0) > (rev1 / a1)) else 0)
    )


def revenue_cagr(inc) -> Optional[float]:
    """Revenue CAGR in percent over all reported years, or None (needs 2+ years, oldest > 0)."""
    revs = series(inc, "Total Revenue")
    if len(revs) >= 2 and revs[-1] > 0:
        return ((revs[0] / revs[-1]) ** (1 / (len(revs) - 1)) - 1) * 100
    return None


def avg_roce(inc, bal, ebit_rows=("EBIT", "Operating Income", "Ebit")) -> Optional[float]:
    """Mean EBIT / (total assets - current liabilities) in percent across reported years."""
    ebit_s = series(inc, *ebit_rows)
    ta_s = series(bal, "Total Assets")
    cl_s = series(bal, "Current Liabilities", "Total Current Liabilities")
    vals = [ebit_s[i] / (ta_s[i] - cl_s[i]) * 100
            for i in range(min(len(ebit_s), len(ta_s), len(cl_s))) if (ta_s[i] - cl_s[i]) > 0]
    return sum(vals) / len(vals) if vals else None


def debt_to_equity_from_balance(bal, equity_rows=EQUITY_ROWS, abs_equity=True) -> Optional[float]:
    """Latest long-term debt / equity from the balance sheet, or None."""
    ltd_s = series(bal, "Long Term Debt")
    eq_s = series(bal, *equity_rows)
    if ltd_s and eq_s and eq_s[0] != 0:
        return ltd_s[0] / (abs(eq_s[0]) if abs_equity else eq_s[0])
    return None


def normalise_debt_to_equity(de_raw):
    """yfinance reports D/E in percent for most tickers; convert values above 10 to a ratio."""
    return de_raw / 100 if de_raw > 10 else de_raw


def all_years_positive(inc) -> bool:
    """True when net income is positive in every reported year (and at least one exists)."""
    ni_s = series(inc, "Net Income")
    return bool(ni_s and all(n > 0 for n in ni_s))


def fcf_positive(cf, ocf_rows=("Operating Cash Flow",),
                 capex_rows=("Capital Expenditure",)) -> bool:
    """Latest free cash flow > 0; falls back to operating cash flow minus |capex|."""
    fcf_s = series(cf, "Free Cash Flow")
    if fcf_s:
        return fcf_s[0] > 0
    ocf_s = series(cf, *ocf_rows)
    capex_s = series(cf, *capex_rows)
    return bool(ocf_s and capex_s and (ocf_s[0] - abs(capex_s[0])) > 0)


class CoffeeCan(NamedTuple):
    """Coffee Can flags C1, C2, C3, C5, C6 (C4 = market-cap floor is added by the caller)."""
    flags: dict
    cagr: Optional[float]
    avg_roce: Optional[float]


def coffee_can_flags(inc, bal, cf, de, ebit_rows=("EBIT", "Operating Income", "Ebit")) -> CoffeeCan:
    """C1 revenue CAGR > 10%, C2 mean ROCE > 15%, C3 D/E < 1, C5 no loss year, C6 FCF > 0."""
    cagr = revenue_cagr(inc)
    roce = avg_roce(inc, bal, ebit_rows)
    flags = {
        "C1": 1 if (cagr and cagr > 10) else 0,
        "C2": 1 if (roce and roce > 15) else 0,
        "C3": 1 if (de is not None and de < 1) else 0,
        "C5": 1 if all_years_positive(inc) else 0,
        "C6": 1 if fcf_positive(cf) else 0,
    }
    return CoffeeCan(flags, cagr, roce)


class MagicFormula(NamedTuple):
    roic: Optional[float]
    earnings_yield: Optional[float]
    book_value: Optional[float]


def magic_formula_inputs(inc, bal, info, mcap) -> MagicFormula:
    """ROIC and earnings yield (percent) from EBIT, capital employed and enterprise value."""
    ebit = row(inc, "EBIT", "Operating Income", "Ebit")
    a0 = row(bal, "Total Assets", col=0)
    cl0 = row(bal, "Current Liabilities", "Total Current Liabilities", col=0)
    cap_emp = (a0 - (cl0 or 0)) if a0 else None
    total_debt = info.get("totalDebt", 0) or 0
    cash_val = info.get("totalCash", 0) or 0
    ev = (mcap + total_debt - cash_val) if mcap else None
    roic = (ebit / cap_emp * 100) if (ebit and cap_emp and cap_emp > 0) else None
    ey = (ebit / ev * 100) if (ebit and ev and ev > 0) else None
    return MagicFormula(roic, ey, info.get("bookValue"))


def magic_formula_passes(mf: MagicFormula, mcap_ok: bool) -> bool:
    """ROIC > 25%, earnings yield > 15%, positive book value and the market's cap floor."""
    return bool(mf.roic and mf.roic > 25 and mf.earnings_yield and mf.earnings_yield > 15
                and mf.book_value and mf.book_value > 0 and mcap_ok)


class BullCartel(NamedTuple):
    sales_growth: Optional[float]
    profit_growth: Optional[float]
    net_profit_scaled: Optional[float]
    qualifies: bool


def bull_cartel(inc_q, profit_divisor: float, min_profit: float) -> BullCartel:
    """YoY quarterly sales > 15% and profit > 20% with net profit above ``min_profit``
    (in units of ``profit_divisor`` currency, e.g. 1e7 for crore, 1e6 for USD millions)."""
    rev_q0 = row(inc_q, "Total Revenue", col=0)
    rev_q4 = row(inc_q, "Total Revenue", col=4)
    ni_q0 = row(inc_q, "Net Income", col=0)
    ni_q4 = row(inc_q, "Net Income", col=4)
    sales_g = ((rev_q0 - rev_q4) / abs(rev_q4) * 100) if (rev_q0 and rev_q4 and rev_q4 != 0) else None
    profit_g = ((ni_q0 - ni_q4) / abs(ni_q4) * 100) if (ni_q0 and ni_q4 and ni_q4 != 0) else None
    scaled = ni_q0 / profit_divisor if ni_q0 else None
    ok = bool(sales_g and sales_g > 15 and profit_g and profit_g > 20 and scaled and scaled > min_profit)
    return BullCartel(sales_g, profit_g, scaled, ok)


def rounded(value, ndigits=2):
    """round() that passes None through."""
    return round(value, ndigits) if value is not None else None
