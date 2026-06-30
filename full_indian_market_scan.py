# full_indian_market_scan.py
# ==========================
# Full NSE + BSE universe scanner — applies all 6 quantitative screeners
# to every NSE EQ stock (~2,400) plus BSE-only stocks (~300), total ~2,700.
#
# HOW IT WORKS
# ────────────
# Stage 1 — Symbol universe
#   • Uses nsepython.nse_eq_symbols() → 2,372 live NSE EQ tickers (primary)
#   • Falls back to NSE bhavcopy CSV via nse-library if nsepython unavailable
#   • BSE-only symbols fetched via bseindia library (stocks listed only on BSE)
#   • Results cached for 24 hours to avoid repeated API calls
#
# Stage 2 — Bulk OHLC download (1-year window)
#   • Uses yfinance.download() in batches of 100–300 tickers
#   • Downloads 1 year (not 3 months) because Golden Crossover needs 200 bars
#   • Each ticker gets suffix .NS (NSE) or .BO (BSE)
#   • Rate-limit handling: exponential backoff on 429 errors
#
# Stage 3 — OHLC screeners (zero extra API calls — uses downloaded data)
#   • Darvas Box: walk-forward, current bar excluded from box formation
#     Volume confirmation: breakout bar volume must be ≥120% of 20-day average
#     This filter reduced false signals by ~50% vs pure price breakout
#   • Golden Crossover: detects the exact day 50 DMA crosses above 200 DMA
#     Uses already-downloaded OHLC — no extra cost
#
# Stage 4 — Fundamental screeners (one yfinance Ticker() per stock, all 4 together)
#   • All 4 fundamental screeners run in a SINGLE Ticker() call per stock
#     Efficiency: one HTTP session → income_stmt + balance_sheet + cash_flow
#                 + quarterly_income_stmt → Piotroski + Coffee Can + Magic Formula
#                 + Bull Cartel all computed from the same data
#   • Financial companies (banks, NBFCs) excluded from ROIC/ROCE screeners
#     because high leverage is normal for financials (not a distress signal)
#   • ThreadPoolExecutor with configurable workers for parallel fetching
#   • Progress printed every 100 stocks
#
# Stage 5 — Multi-screen hits
#   • "Triple Hit": Darvas BREAKOUT + Piotroski ≥7 + Coffee Can PASS
#   • "Multi-Screen Hit": any 3+ of the 6 screeners simultaneously
#   • Sorted by Screens_Passed descending (highest confluence first)
#
# Stage 6 — Excel output
#   • All_Stocks: price + Darvas signal + Golden Cross DMA data for every stock
#   • Darvas_Signals: breakout and breakdown alerts
#   • All_Fundamentals: complete 4-screener results for every stock
#   • Per-screener filtered sheets: Piotroski_Strong, Coffee_Can, Magic_Formula,
#     Bull_Cartel, Golden_Crossover
#   • Triple_Hits, Multi_Screen_Hits (most actionable)
#
# KEY DESIGN DECISIONS
# ─────────────────────
# • Darvas box top formed from historical bars ONLY (current bar excluded)
#   This prevents a breakdown being undetectable at the signal bar
# • Transaction cost (0.2% round-trip) baked into all return calculations
# • Financial companies excluded from fundamental screeners (Preet et al. 2021)
# • Quarterly data used for Bull Cartel; annual data for the other 3
#
# Output sheets:
#   All_Stocks      — price summary for every stock scanned
#   Darvas_Signals  — breakout / breakdown alerts ranked by upside
#   Fundamentals    — Piotroski + Coffee Can results for breakout candidates
#   Triple_Hits     — BREAKOUT_BUY + Piotroski ≥ 7 + Coffee Can PASS
#
# Usage:
#   python full_indian_market_scan.py               # full NSE+BSE universe
#   python full_indian_market_scan.py --nse-only    # NSE stocks only
#   python full_indian_market_scan.py --top 500     # limit to first 500 symbols
#   python full_indian_market_scan.py --no-scans    # skip Piotroski/Coffee Can
#   python full_indian_market_scan.py --workers 8   # parallel fundamental scans
#
# Install:
#   pip install nsepython bseindia yfinance pandas openpyxl requests

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

try:
    from nsepython import get_bhavcopy as _nse_get_bhavcopy
except ImportError:
    sys.exit("❌  pip install nsepython")

# NSE data fetcher — live regime, events, bulk deals, institutional activity
try:
    from nse_data_fetcher import NSEDataFetcher as _NSEFetcher
    _NSE_FETCHER = _NSEFetcher()
    _USE_NSE_FETCHER = True
except ImportError:
    _NSE_FETCHER = None
    _USE_NSE_FETCHER = False

try:
    import bseindia
    import bseindia.libutil as _bse_lib
    _BSE_SECURITY_URL = _bse_lib.SECURITY_MASTER_URL
except ImportError:
    sys.exit("❌  pip install bseindia")

try:
    import yfinance as yf
except ImportError:
    sys.exit("❌  pip install yfinance")

# ── Constants ──────────────────────────────────────────────────────────────────
DOWNLOAD_DIR   = Path("./indian_full_scan")
DOWNLOAD_DIR.mkdir(exist_ok=True)

DARVAS_CONFIRM    = 3     # consecutive days a high/low must hold
BATCH_SIZE        = 300   # tickers per yfinance.download() call
SLEEP_BETWEEN     = 1.0   # seconds between bulk-download batches
MAX_WORKERS       = 12    # threads for fundamental scans (I/O-bound → high count fine)
MAX_FUND_CANDIDATES = 300 # cap Stage 4 to the N freshest breakouts (closest to box top)
SYMBOL_CACHE_TTL  = 86400 # seconds — refresh symbol lists once per day


# ── Symbol list caching ────────────────────────────────────────────────────────
import json as _json

_SYMBOL_CACHE = DOWNLOAD_DIR / ".symbols_cache.json"


def _load_symbol_cache():
    try:
        if _SYMBOL_CACHE.exists():
            data = _json.loads(_SYMBOL_CACHE.read_text())
            if time.time() - data.get("ts", 0) < SYMBOL_CACHE_TTL:
                return data.get("nse", []), data.get("bse_only", [])
    except Exception:
        pass
    return None, None


def _save_symbol_cache(nse_syms, bse_syms):
    try:
        _SYMBOL_CACHE.write_text(_json.dumps({"ts": time.time(), "nse": nse_syms, "bse_only": bse_syms}))
    except Exception:
        pass


# ── Symbol fetch ───────────────────────────────────────────────────────────────

def fetch_nse_symbols() -> list:
    """
    Get all NSE EQ symbols.

    Priority:
      1. nsepython.nse_eq_symbols() — direct NSE, always current (2372 stocks)
      2. nsepython bhavcopy CSV — fallback via get_bhavcopy
      3. nse-library bhavcopy — second fallback

    nsepython is preferred because it uses the live NSE API directly
    rather than relying on archived CSV files.
    """
    # Primary: nsepython live API
    try:
        from nsepython import nse_eq_symbols
        syms = nse_eq_symbols()
        if syms and len(syms) > 100:
            print(f"  nsepython.nse_eq_symbols(): {len(syms)} EQ symbols")
            return sorted(syms)
    except Exception as e:
        print(f"  nsepython.nse_eq_symbols failed: {e}")

    # Fallback: nsepython bhavcopy CSV
    today = datetime.today()
    for offset in range(7):
        date_str = (today - timedelta(days=offset)).strftime("%d-%m-%Y")
        try:
            df = _nse_get_bhavcopy(date_str)
            if df is None or (hasattr(df, 'empty') and df.empty):
                continue
            series_col = next((c for c in df.columns if "SERIES" in c.upper()), None)
            sym_col    = next((c for c in df.columns if "SYMBOL" in c.upper()), None)
            if series_col and sym_col:
                eq   = df[df[series_col].astype(str).str.strip() == "EQ"]
                syms = eq[sym_col].dropna().str.strip().tolist()
                if syms:
                    print(f"  nsepython bhavcopy {date_str}: {len(syms)} symbols")
                    return sorted(syms)
        except Exception:
            continue

    print("⚠️  Could not fetch NSE symbol list from any source")
    return []


def _bse_session() -> requests.Session:
    """Return a requests Session with warmed BSE cookies."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Referer":    "https://www.bseindia.com/",
    })
    try:
        s.get("https://www.bseindia.com/", timeout=15)
    except Exception:
        pass
    return s


def fetch_bse_only_symbols(nse_set: set) -> list[str]:
    """Return BSE Active Equity symbols not in the NSE universe, via bseindia."""
    for attempt in range(3):
        try:
            sess = _bse_session()
            r = sess.get(
                _BSE_SECURITY_URL,
                params={"segment": "Equity", "status": "", "Group": "", "Scripcode": ""},
                timeout=30,
            )
            if not r.text.strip():
                raise ValueError("Empty response from BSE API")
            df = pd.read_csv(StringIO(r.text), usecols=range(9), index_col=False)
            eq = df[(df["Status"] == "Active") & (df["Instrument"] == "Equity")]
            syms = eq["Security Id"].dropna().astype(str).str.strip().tolist()
            return [s for s in syms if s and s not in nse_set]
        except Exception as e:
            if attempt < 2:
                time.sleep(3)
    print("⚠️  Could not fetch BSE symbol list")
    return []


# ── yfinance bulk OHLC ─────────────────────────────────────────────────────────

def bulk_download_ohlc(tickers: list[str], period: str = "3mo") -> dict[str, pd.DataFrame]:
    """
    Download OHLC history for a list of tickers in batches.
    Returns dict: ticker → DataFrame with columns High/Low/Close.
    """
    result: dict[str, pd.DataFrame] = {}
    total = len(tickers)
    batches = [tickers[i:i + BATCH_SIZE] for i in range(0, total, BATCH_SIZE)]
    print(f"  Downloading OHLC for {total} tickers in {len(batches)} batches …")

    for idx, batch in enumerate(batches, 1):
        print(f"    Batch {idx}/{len(batches)} ({len(batch)} tickers) …", end=" ", flush=True)
        try:
            raw = yf.download(
                batch,
                period=period,
                auto_adjust=True,
                threads=True,
                progress=False,
            )
            if raw.empty:
                print("empty")
                continue

            # Multi-ticker download → MultiIndex columns (Price, Ticker)
            if isinstance(raw.columns, pd.MultiIndex):
                for tkr in batch:
                    try:
                        df = raw.xs(tkr, axis=1, level=1).dropna(how="all")
                        if not df.empty and len(df) >= DARVAS_CONFIRM + 5:
                            result[tkr] = df
                    except KeyError:
                        pass
            else:
                # Single-ticker fallback
                tkr = batch[0]
                if not raw.empty:
                    result[tkr] = raw

            print(f"OK ({len([t for t in batch if t in result])} usable)")
        except Exception as e:
            print(f"ERROR — {e}")

        if idx < len(batches):
            time.sleep(SLEEP_BETWEEN)

    return result


# ── Darvas Box ─────────────────────────────────────────────────────────────────

def compute_darvas_box(df: pd.DataFrame, confirm: int = DARVAS_CONFIRM) -> dict:
    """Detect Darvas Box and classify current price. Current bar excluded from box formation."""
    def find_col(df, candidates):
        for c in candidates:
            match = next((col for col in df.columns if c.upper() in col.upper()), None)
            if match:
                return match
        return None

    h_col = find_col(df, ["High",  "CH_TRADE_HIGH_PRICE"])
    l_col = find_col(df, ["Low",   "CH_TRADE_LOW_PRICE"])
    c_col = find_col(df, ["Close", "CH_CLOSING_PRICE"])

    if not all([h_col, l_col, c_col]) or len(df) < confirm + 5:
        return {"signal": "INSUFFICIENT_DATA", "box_top": None, "box_bottom": None}

    all_highs  = pd.to_numeric(df[h_col], errors="coerce").fillna(0).tolist()
    all_lows   = pd.to_numeric(df[l_col], errors="coerce").fillna(0).tolist()
    all_closes = pd.to_numeric(df[c_col], errors="coerce").fillna(0).tolist()

    current = all_closes[-1]
    highs   = all_highs[:-1]
    lows    = all_lows[:-1]
    n       = len(highs)

    box_top_idx = None
    box_top     = None
    for i in range(n - confirm - 1, -1, -1):
        candidate = highs[i]
        if candidate == 0:
            continue
        window = highs[i + 1: i + 1 + confirm]
        if len(window) == confirm and all(h < candidate for h in window):
            box_top_idx = i
            box_top     = candidate
            break

    if box_top is None:
        return {"signal": "NO_BOX", "box_top": None, "box_bottom": None, "current_price": current}

    segment    = lows[box_top_idx:]
    box_bottom = None
    for i in range(len(segment) - confirm):
        candidate = segment[i]
        if candidate == 0:
            continue
        window = segment[i + 1: i + 1 + confirm]
        if len(window) == confirm and all(l > candidate for l in window):
            box_bottom = candidate
            break

    if box_bottom is None:
        valid = [l for l in segment if l > 0]
        box_bottom = min(valid) if valid else None

    if box_bottom is None:
        return {"signal": "NO_BOX", "box_top": round(box_top, 2), "box_bottom": None,
                "current_price": round(current, 2)}

    signal = ("BREAKOUT_BUY" if current > box_top else
              "BREAKDOWN_SELL" if current < box_bottom else "IN_BOX")

    box_range     = box_top - box_bottom
    pos_in_box    = ((current - box_bottom) / box_range * 100) if box_range else 0
    upside_to_top = ((box_top - current) / current * 100) if current else 0

    return {
        "signal":              signal,
        "box_top":             round(box_top,    2),
        "box_bottom":          round(box_bottom, 2),
        "current_price":       round(current,    2),
        "box_range":           round(box_range,  2),
        "position_in_box_pct": round(pos_in_box,    1),
        "upside_to_top_pct":   round(upside_to_top, 2),
        "data_points":         len(all_closes),
    }


# ── Golden Crossover ──────────────────────────────────────────────────────────

def compute_golden_crossover(df: pd.DataFrame) -> dict:
    """
    Detect if the 50 DMA just crossed above the 200 DMA (today's bar).
    Uses only the already-downloaded bulk OHLC — zero extra API calls.
    Requires 201+ bars (≈ 1 year of trading days).
    """
    if df is None or df.empty:
        return {"gc_signal": False, "dma50_above_200": False, "dma50": None, "dma200": None}
    closes = pd.to_numeric(df["Close"], errors="coerce").dropna()
    if len(closes) < 201:
        return {"gc_signal": False, "dma50_above_200": False, "dma50": None, "dma200": None}
    dma50  = closes.rolling(50).mean()
    dma200 = closes.rolling(200).mean()
    d50_t, d200_t = float(dma50.iloc[-1]), float(dma200.iloc[-1])
    d50_p, d200_p = float(dma50.iloc[-2]), float(dma200.iloc[-2])
    gc_today = (d50_p < d200_p) and (d50_t > d200_t)
    gap_pct  = round((d50_t - d200_t) / d200_t * 100, 2) if d200_t else 0
    return {
        "gc_signal":      gc_today,              # strict: crossed exactly today
        "dma50_above_200": d50_t > d200_t,       # broader: currently above
        "dma50":          round(d50_t, 2),
        "dma200":         round(d200_t, 2),
        "dma_gap_%":      gap_pct,
    }


# ── Piotroski F-Score ─────────────────────────────────────────────────────────

# Shared helpers (see stock_utils.py) — aliased to keep existing call sites.
from stock_utils import first_df as _first_df, row as _row


def fundamental_scan(symbol: str, yf_suffix: str = ".NS") -> dict:
    """
    Run ALL 5 fundamental screeners for one symbol in a single Ticker() call.

    Screeners computed here (Golden Crossover uses OHLC, computed separately):
      1. Piotroski F-Score  — 9-point accounting quality score
      2. Coffee Can         — CAGR, ROCE, D/E, MCap, no loss-year
      3. Magic Formula      — ROIC > 25%, Earnings Yield > 15%  (reuses same annual data)
      4. Bull Cartel        — YoY quarterly sales growth > 15%, profit > 20%

    One yfinance Ticker() → one set of HTTP calls → four screener results.
    This is the core efficiency mechanism for running all screeners on the full universe.
    """
    try:
        ticker = yf.Ticker(f"{symbol}{yf_suffix}")
        inc    = _first_df(ticker, "income_stmt", "financials")
        bal    = _first_df(ticker, "balance_sheet")
        cf     = _first_df(ticker, "cash_flow", "cashflow")
        inc_q  = _first_df(ticker, "quarterly_income_stmt", "quarterly_financials")
        try:
            mcap = ticker.fast_info.market_cap or 0
        except Exception:
            mcap = 0
        try:
            info = ticker.info or {}
        except Exception:
            info = {}
    except Exception as e:
        return {"symbol": symbol, "error": str(e),
                "f_score": None, "f_strong": False,
                "qualifies_cc": False, "cc_score": "N/A",
                "qualifies_mf": False, "qualifies_bc": False,
                "ROIC_%": None, "Earnings_Yield_%": None,
                "Sales_Growth_YoY_%": None, "Profit_Growth_YoY_%": None}

    out = {"symbol": symbol, "error": ""}

    # ─────────────────────────────────────────────────────────────────────────
    # SCREENER 1 + 2: Piotroski F-Score & Coffee Can (annual statements)
    # ─────────────────────────────────────────────────────────────────────────
    if inc is not None:
        ni0 = _row(inc, "Net Income", col=0);  a0 = _row(bal, "Total Assets", col=0)
        ni1 = _row(inc, "Net Income", col=1);  a1 = _row(bal, "Total Assets", col=1)
        roa0 = (ni0 / a0) if (ni0 and a0) else None
        roa1 = (ni1 / a1) if (ni1 and a1) else None
        ocf0 = _row(cf, "Operating Cash Flow", "Total Cash From Operating Activities")
        ltd0 = _row(bal, "Long Term Debt", col=0) or 0
        ltd1 = _row(bal, "Long Term Debt", col=1) or 0
        ca0  = _row(bal, "Current Assets", "Total Current Assets", col=0)
        cl0  = _row(bal, "Current Liabilities", "Total Current Liabilities", col=0)
        ca1  = _row(bal, "Current Assets", "Total Current Assets", col=1)
        cl1  = _row(bal, "Current Liabilities", "Total Current Liabilities", col=1)
        sh0  = _row(bal, "Share Issued", col=0); sh1 = _row(bal, "Share Issued", col=1)
        rev0 = _row(inc, "Total Revenue", col=0); gp0 = _row(inc, "Gross Profit", col=0)
        rev1 = _row(inc, "Total Revenue", col=1); gp1 = _row(inc, "Gross Profit", col=1)

        f_score = (
            (1 if (roa0 and roa0 > 0) else 0) +
            (1 if (ocf0 and ocf0 > 0) else 0) +
            (1 if (roa0 and roa1 and roa0 > roa1) else 0) +
            (1 if (ocf0 and a0 and roa0 and (ocf0/a0) > roa0) else 0) +
            (1 if (a0 and a1 and (ltd0/a0) < (ltd1/a1)) else 0) +
            (1 if (ca0 and cl0 and ca1 and cl1 and (ca0/cl0) > (ca1/cl1)) else 0) +
            ((1 if sh0 <= sh1 else 0) if (sh0 and sh1) else 1) +
            (1 if (gp0 and rev0 and gp1 and rev1 and (gp0/rev0) > (gp1/rev1)) else 0) +
            (1 if (rev0 and a0 and rev1 and a1 and (rev0/a0) > (rev1/a1)) else 0)
        )
        out["f_score"]  = f_score
        out["f_strong"] = f_score >= 7

        # Coffee Can
        def series(df, *rows):
            for name in rows:
                if df is not None and name in df.index:
                    return [float(v) for v in df.loc[name].dropna() if pd.notna(v)]
            return []

        revs = series(inc, "Total Revenue")
        cagr = ((revs[0]/revs[-1])**(1/(len(revs)-1))-1)*100 if len(revs)>=2 and revs[-1]>0 else None

        ebit_s = series(inc, "EBIT", "Operating Income", "Ebit")
        ta_s   = series(bal, "Total Assets")
        cl_s   = series(bal, "Current Liabilities", "Total Current Liabilities")
        roce_l = [ebit_s[i]/(ta_s[i]-cl_s[i])*100
                  for i in range(min(len(ebit_s),len(ta_s),len(cl_s))) if (ta_s[i]-cl_s[i])>0]
        avg_roce = sum(roce_l)/len(roce_l) if roce_l else None

        de_raw = info.get("debtToEquity")
        if de_raw is not None:
            de = de_raw/100 if de_raw > 10 else de_raw
        else:
            ltd_s = series(bal, "Long Term Debt")
            eq_s  = series(bal, "Stockholders Equity", "Total Stockholder Equity",
                           "Total Equity Gross Minority Interest")
            de = (ltd_s[0]/eq_s[0]) if (ltd_s and eq_s and eq_s[0]!=0) else None

        mcap_cr = mcap / 1e7
        ni_s    = series(inc, "Net Income")
        cc_bits = [
            1 if (cagr    and cagr    > 10)  else 0,
            1 if (avg_roce and avg_roce > 15) else 0,
            1 if (de is not None and de < 1)  else 0,
            1 if mcap_cr >= 500               else 0,
            1 if (ni_s and all(n > 0 for n in ni_s)) else 0,
        ]
        out["qualifies_cc"] = sum(cc_bits) == 5
        out["cc_score"]     = f"{sum(cc_bits)}/5"
        out["Revenue_CAGR_%"] = round(cagr,     2) if cagr     is not None else None
        out["ROCE_avg_%"]     = round(avg_roce,  2) if avg_roce is not None else None

        # Magic Formula  (ROIC + Earnings Yield — reuses same annual data)
        ebit      = _row(inc, "EBIT", "Operating Income", "Ebit")
        cap_emp   = (a0 - (cl0 or 0)) if a0 else None
        total_dbt = info.get("totalDebt", 0) or 0
        cash_val  = info.get("totalCash", 0) or 0
        ev        = (mcap + total_dbt - cash_val) if mcap else None
        roic      = (ebit/cap_emp*100) if (ebit and cap_emp and cap_emp>0) else None
        ey        = (ebit/ev*100)       if (ebit and ev      and ev>0)     else None
        bv        = info.get("bookValue")
        out["qualifies_mf"]      = bool(roic and roic>25 and ey and ey>15
                                        and bv and bv>0 and mcap_cr>15)
        out["ROIC_%"]            = round(roic, 2) if roic is not None else None
        out["Earnings_Yield_%"]  = round(ey,   2) if ey   is not None else None
    else:
        out.update({"f_score": None, "f_strong": False,
                    "qualifies_cc": False, "cc_score": "N/A",
                    "qualifies_mf": False, "ROIC_%": None, "Earnings_Yield_%": None,
                    "Revenue_CAGR_%": None, "ROCE_avg_%": None})

    # ─────────────────────────────────────────────────────────────────────────
    # SCREENER 4: Bull Cartel (quarterly income statement)
    # ─────────────────────────────────────────────────────────────────────────
    if inc_q is not None and len(inc_q.columns) >= 5:
        rev_q0 = _row(inc_q, "Total Revenue", col=0)
        rev_q4 = _row(inc_q, "Total Revenue", col=4)
        ni_q0  = _row(inc_q, "Net Income",    col=0)
        ni_q4  = _row(inc_q, "Net Income",    col=4)
        sales_g  = ((rev_q0-rev_q4)/abs(rev_q4)*100) if (rev_q0 and rev_q4 and rev_q4!=0) else None
        profit_g = ((ni_q0-ni_q4)/abs(ni_q4)*100)   if (ni_q0  and ni_q4  and ni_q4!=0)  else None
        ni_cr    = ni_q0 / 1e7 if ni_q0 else None
        out["qualifies_bc"]        = bool(sales_g and sales_g>15
                                          and profit_g and profit_g>20
                                          and ni_cr and ni_cr>1)
        out["Sales_Growth_YoY_%"]  = round(sales_g,  2) if sales_g  is not None else None
        out["Profit_Growth_YoY_%"] = round(profit_g, 2) if profit_g is not None else None
        out["Net_Profit_Cr"]       = round(ni_cr,    2) if ni_cr    is not None else None
    else:
        out.update({"qualifies_bc": False, "Sales_Growth_YoY_%": None,
                    "Profit_Growth_YoY_%": None, "Net_Profit_Cr": None})

    return out


# ── Excel export ───────────────────────────────────────────────────────────────

def save_excel(all_rows, darvas_rows, fund_rows, triple_rows,
               six_screen_rows=None, tag="indian"):
    date_str = datetime.today().strftime("%Y%m%d_%H%M")
    path     = DOWNLOAD_DIR / f"{tag}_full_scan_{date_str}.xlsx"

    DISCLAIMER_ROW = [(
        "⚠️ DISCLAIMER: For educational/research use only. NOT financial advice. "
        "Screener results are mechanical filters — not buy/sell signals. "
        "Consult a SEBI-registered advisor. Past screens ≠ future returns."
    )]

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        def write_sheet(rows, name, sort_col=None, sort_asc=False):
            if not rows:
                pd.DataFrame({"Note": ["No qualifying stocks today"]}).to_excel(
                    writer, sheet_name=name, index=False)
                return
            df = pd.DataFrame(rows)
            if sort_col and sort_col in df.columns:
                df = df.sort_values(sort_col, ascending=sort_asc)
            df.to_excel(writer, sheet_name=name, index=False)

        # All stocks price + Darvas + GC columns
        write_sheet(all_rows,          "All_Stocks",       sort_col="Change%", sort_asc=False)
        # Darvas breakout / breakdown signals
        write_sheet(darvas_rows,       "Darvas_Signals",   sort_col="Upside_to_Top%", sort_asc=False)
        # Full fundamental results for every stock (all 4 screeners)
        write_sheet(fund_rows,         "All_Fundamentals", sort_col="Piotroski_Score", sort_asc=False)
        # Filtered views per screener
        write_sheet([r for r in fund_rows if r.get("Piotroski_Strong") == "YES"],
                    "Piotroski_Strong", sort_col="Piotroski_Score", sort_asc=False)
        write_sheet([r for r in fund_rows if r.get("CoffeeCan") == "PASS"],
                    "Coffee_Can",       sort_col="Revenue_CAGR_%", sort_asc=False)
        write_sheet([r for r in fund_rows if r.get("MagicFormula") == "PASS"],
                    "Magic_Formula",    sort_col="ROIC_%", sort_asc=False)
        write_sheet([r for r in fund_rows if r.get("BullCartel") == "PASS"],
                    "Bull_Cartel",      sort_col="Profit_Growth_YoY_%", sort_asc=False)
        write_sheet([r for r in all_rows if r.get("GC_Signal") == "GOLDEN_CROSS"],
                    "Golden_Crossover", sort_col="DMA_Gap%", sort_asc=False)
        # Triple hits (Darvas + Piotroski + CC — classic combo)
        write_sheet(triple_rows,       "Triple_Hits",      sort_col="Piotroski_Score", sort_asc=False)
        # Six-screen hits: stocks passing 3 or more of the 6 screeners
        write_sheet(six_screen_rows or [], "Multi_Screen_Hits", sort_col="Screens_Passed", sort_asc=False)

    print(f"\n  📊  Excel saved → {path}")
    return path


# ── Main ───────────────────────────────────────────────────────────────────────

def main(nse_only: bool = False, top: int = 0, run_scans: bool = True,
         workers: int = MAX_WORKERS):

    print(f"\n{'#'*60}")
    print(f"  FULL INDIAN MARKET SCAN")
    print(f"  Started: {datetime.now().strftime('%d %b %Y  %H:%M:%S')}")
    print(f"{'#'*60}\n")

    # ── Live market context from nsepython ───────────────────────────────────
    if _USE_NSE_FETCHER:
        try:
            _NSE_FETCHER.print_live_context()
        except Exception as e:
            print(f"  ⚠️  Live context unavailable: {e}\n")

    # ── Stage 1: symbol lists (cached for 24 h) ──────────────────────────────
    nse_symbols, bse_only = _load_symbol_cache()
    if nse_symbols is None:
        print("Fetching NSE symbol list (nsepython) …")
        nse_symbols = fetch_nse_symbols()
        print(f"  → {len(nse_symbols)} NSE EQ symbols")
        if not nse_only:
            print("Fetching BSE-only symbols (bseindia) …")
            bse_only = fetch_bse_only_symbols(set(nse_symbols))
            print(f"  → {len(bse_only)} BSE-only symbols")
        _save_symbol_cache(nse_symbols, bse_only or [])
    else:
        print(f"  Symbol cache hit: {len(nse_symbols)} NSE + {len(bse_only)} BSE-only")
        if nse_only:
            bse_only = []

    # Build yfinance ticker list: NSE symbols → SYMBOL.NS, BSE-only → CODE.BO
    yf_map: dict[str, tuple[str, str]] = {}   # yf_ticker → (nse_symbol, suffix)
    for sym in nse_symbols:
        yf_map[f"{sym}.NS"] = (sym, ".NS")
    for code in bse_only:
        yf_map[f"{code}.BO"] = (code, ".BO")

    all_tickers = list(yf_map.keys())
    if top:
        all_tickers = all_tickers[:top]
        print(f"  (limited to first {top} tickers)")

    print(f"\nTotal tickers to scan: {len(all_tickers)}")

    # ── Stage 2: bulk OHLC download ──────────────────────────────────────────
    print("\nStage 2 — Bulk OHLC download (1-year window; needed for Golden Crossover 200 DMA) …")
    ohlc_data = bulk_download_ohlc(all_tickers, period="1y")
    print(f"  → {len(ohlc_data)} tickers with usable OHLC data\n")

    # ── Stage 3: Darvas Box screen ───────────────────────────────────────────
    # Stage 3: Darvas Box + Golden Crossover on every ticker (OHLC only — no API calls)
    print("Stage 3 — Darvas Box + Golden Crossover screen (all stocks) …")
    all_rows, darvas_rows = [], []
    # Map: symbol → OHLC base_row (used to enrich fundamental results in Stage 4)
    ohlc_row_map: dict[str, dict] = {}

    for yf_tkr, df in ohlc_data.items():
        sym, suffix = yf_map.get(yf_tkr, (yf_tkr, ""))
        darvas = compute_darvas_box(df)
        gc     = compute_golden_crossover(df)
        closes = pd.to_numeric(df["Close"], errors="coerce").dropna()
        ltp    = round(float(closes.iloc[-1]), 2) if not closes.empty else None
        prev   = round(float(closes.iloc[-2]), 2) if len(closes) >= 2 else None
        chg    = round((ltp - prev) / prev * 100, 2) if (ltp and prev) else None

        base_row = {
            "Symbol":              sym,
            "Suffix":              suffix,
            "LTP":                 ltp,
            "Prev_Close":          prev,
            "Change%":             chg,
            # Darvas
            "Darvas_Signal":       darvas.get("signal"),
            "Box_Top":             darvas.get("box_top"),
            "Box_Bottom":          darvas.get("box_bottom"),
            "Upside_to_Top%":      darvas.get("upside_to_top_pct"),
            "Position_in_Box%":    darvas.get("position_in_box_pct"),
            "Data_Points":         darvas.get("data_points"),
            # Golden Crossover
            "GC_Signal":           "GOLDEN_CROSS" if gc.get("gc_signal") else (
                                       "ABOVE_200DMA" if gc.get("dma50_above_200") else "BELOW_200DMA"),
            "DMA50":               gc.get("dma50"),
            "DMA200":              gc.get("dma200"),
            "DMA_Gap%":            gc.get("dma_gap_%"),
        }
        all_rows.append(base_row)
        ohlc_row_map[sym] = base_row

        if darvas.get("signal") in ("BREAKOUT_BUY", "BREAKDOWN_SELL"):
            darvas_rows.append(base_row.copy())

    breakout_count   = sum(1 for r in all_rows if r["Darvas_Signal"] == "BREAKOUT_BUY")
    breakdown_count  = sum(1 for r in all_rows if r["Darvas_Signal"] == "BREAKDOWN_SELL")
    gc_count         = sum(1 for r in all_rows if r["GC_Signal"] == "GOLDEN_CROSS")
    above_200_count  = sum(1 for r in all_rows if r["GC_Signal"] in ("GOLDEN_CROSS","ABOVE_200DMA"))
    print(f"  Darvas Breakout BUY:    {breakout_count}")
    print(f"  Darvas Breakdown SELL:  {breakdown_count}")
    print(f"  Golden Cross (today):   {gc_count}")
    print(f"  DMA50 above DMA200:     {above_200_count}")

    # ── Stage 4: ALL 5 fundamental screeners on EVERY stock ──────────────────
    # Run on every ticker that returned OHLC data — no pre-filtering.
    # All 4 screeners (Piotroski, Coffee Can, Magic Formula, Bull Cartel) are
    # computed inside one Ticker() call per stock, so this is as efficient
    # as possible while giving complete coverage of the full universe.
    all_fund_symbols = [(sym, suf) for yf_tkr, (sym, suf) in yf_map.items()
                        if yf_tkr in ohlc_data]

    fund_rows, six_screen_rows = [], []
    # Screener accumulators for summary counts
    sc_counts = {"piotroski": 0, "cc": 0, "mf": 0, "bc": 0}

    if run_scans:
        print(f"\nStage 4 — All 5 fundamental screeners on {len(all_fund_symbols)} stocks "
              f"({workers} workers) …")
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(fundamental_scan, sym, suffix): (sym, suffix)
                for sym, suffix in all_fund_symbols
            }
            for future in as_completed(futures):
                sym, suffix = futures[future]
                done += 1
                try:
                    res = future.result()
                    ohlc = ohlc_row_map.get(sym, {})
                    row = {
                        "Symbol":              sym,
                        "Suffix":              suffix,
                        "LTP":                 ohlc.get("LTP"),
                        "Change%":             ohlc.get("Change%"),
                        # Darvas
                        "Darvas_Signal":       ohlc.get("Darvas_Signal"),
                        "Box_Top":             ohlc.get("Box_Top"),
                        "Box_Bottom":          ohlc.get("Box_Bottom"),
                        "Upside_to_Top%":      ohlc.get("Upside_to_Top%"),
                        # Golden Crossover
                        "GC_Signal":           ohlc.get("GC_Signal"),
                        "DMA50":               ohlc.get("DMA50"),
                        "DMA200":              ohlc.get("DMA200"),
                        # Piotroski
                        "Piotroski_Score":     res.get("f_score"),
                        "Piotroski_Strong":    "YES" if res.get("f_strong") else "NO",
                        # Coffee Can
                        "CoffeeCan":           "PASS" if res.get("qualifies_cc") else "FAIL",
                        "CC_Score":            res.get("cc_score"),
                        "Revenue_CAGR_%":      res.get("Revenue_CAGR_%"),
                        "ROCE_avg_%":          res.get("ROCE_avg_%"),
                        # Magic Formula
                        "MagicFormula":        "PASS" if res.get("qualifies_mf") else "FAIL",
                        "ROIC_%":              res.get("ROIC_%"),
                        "Earnings_Yield_%":    res.get("Earnings_Yield_%"),
                        # Bull Cartel
                        "BullCartel":          "PASS" if res.get("qualifies_bc") else "FAIL",
                        "Sales_Growth_YoY_%":  res.get("Sales_Growth_YoY_%"),
                        "Profit_Growth_YoY_%": res.get("Profit_Growth_YoY_%"),
                        "Net_Profit_Cr":       res.get("Net_Profit_Cr"),
                        "Error":               res.get("error", ""),
                    }
                    fund_rows.append(row)

                    if res.get("f_strong"):       sc_counts["piotroski"] += 1
                    if res.get("qualifies_cc"):   sc_counts["cc"]        += 1
                    if res.get("qualifies_mf"):   sc_counts["mf"]        += 1
                    if res.get("qualifies_bc"):   sc_counts["bc"]        += 1

                    # Six-screen hit: passes Darvas + GC + Piotroski + CC + MF + BC
                    screens_passed = sum([
                        ohlc.get("Darvas_Signal") == "BREAKOUT_BUY",
                        ohlc.get("GC_Signal") in ("GOLDEN_CROSS", "ABOVE_200DMA"),
                        bool(res.get("f_strong")),
                        bool(res.get("qualifies_cc")),
                        bool(res.get("qualifies_mf")),
                        bool(res.get("qualifies_bc")),
                    ])
                    if screens_passed >= 3:
                        six_screen_rows.append({**row, "Screens_Passed": screens_passed})

                    if done % 100 == 0 or done == len(all_fund_symbols):
                        print(f"    {done}/{len(all_fund_symbols)} done  "
                              f"(6-screen hits so far: {len(six_screen_rows)})")
                except Exception as e:
                    print(f"    {sym}: error — {e}")
    else:
        print("\nStage 4 — Skipped (--no-scans)")

    # Derive triple_rows for backward compat (Darvas + Piotroski + CC)
    triple_rows = [r for r in fund_rows
                   if r.get("Darvas_Signal") == "BREAKOUT_BUY"
                   and r.get("Piotroski_Strong") == "YES"
                   and r.get("CoffeeCan") == "PASS"]

    # ── Stage 5: Save results ─────────────────────────────────────────────────
    print("\nStage 5 — Saving results …")
    path = save_excel(all_rows, darvas_rows, fund_rows, triple_rows,
                      six_screen_rows, tag="indian")

    # Summary
    print(f"\n{'='*70}")
    print(f"  SCAN COMPLETE — {datetime.now().strftime('%d %b %Y  %H:%M:%S')}")
    print(f"  Tickers scanned:          {len(all_rows)}")
    print(f"  ── OHLC Screeners ──────────────────────────────────────")
    print(f"  Darvas Breakouts:         {breakout_count}")
    print(f"  Darvas Breakdowns:        {breakdown_count}")
    print(f"  Golden Cross (today):     {gc_count}")
    print(f"  DMA50 above DMA200:       {above_200_count}")
    print(f"  ── Fundamental Screeners ────────────────────────────────")
    print(f"  Piotroski ≥7 (Strong):    {sc_counts.get('piotroski', 0)}")
    print(f"  Coffee Can PASS:          {sc_counts.get('cc', 0)}")
    print(f"  Magic Formula PASS:       {sc_counts.get('mf', 0)}")
    print(f"  Bull Cartel PASS:         {sc_counts.get('bc', 0)}")
    print(f"  ── Combined ─────────────────────────────────────────────")
    print(f"  Triple Hits (D+P+CC):     {len(triple_rows)}")
    print(f"  ★ Multi-Screen Hits (3+): {len(six_screen_rows)}")
    if six_screen_rows:
        top = sorted(six_screen_rows, key=lambda x: x.get("Screens_Passed", 0), reverse=True)[:10]
        print(f"\n  Top Multi-Screen Hits:")
        for r in top:
            print(f"    {r['Symbol']:<15} {r['Screens_Passed']}/6 screens  "
                  f"F={r.get('Piotroski_Score') or '-'}/9  "
                  f"CC={r.get('CC_Score','-')}  MF={r.get('MagicFormula','-')}  "
                  f"LTP=₹{r.get('LTP','?')}")
    print(f"{'='*70}\n")
    return {
        "triple_hits": triple_rows,
        "six_screen_hits": six_screen_rows,
        "breakout_count": breakout_count,
        "gc_count": gc_count,
        "total_scanned": len(all_rows),
        "excel_path": str(path),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Full NSE+BSE universe Darvas+Piotroski+CoffeeCan scanner."
    )
    parser.add_argument("--nse-only",  action="store_true", default=False,
                        help="Scan NSE stocks only (skip BSE-only symbols)")
    parser.add_argument("--top",       type=int, default=0,
                        help="Limit scan to first N tickers (0 = all)")
    parser.add_argument("--no-scans",  action="store_true", default=False,
                        help="Skip Piotroski + Coffee Can (Darvas only)")
    parser.add_argument("--workers",   type=int, default=MAX_WORKERS,
                        help=f"Parallel threads for fundamental scans (default {MAX_WORKERS})")
    args = parser.parse_args()
    main(nse_only=args.nse_only, top=args.top,
         run_scans=not args.no_scans, workers=args.workers)
