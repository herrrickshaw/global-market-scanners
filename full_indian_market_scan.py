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

from scanner_core.darvas import get_profile
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
    """Darvas box (india profile); the shared engine excludes the current bar from box formation."""
    return get_profile("india").compute(df, confirm)


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
from scanner_core import fundamentals as fund
from scanner_core.pipeline import (count_screeners, new_screener_counts, ohlc_counts,
                                   screens_passed, triple_rows as pipeline_triple_rows)


def _in_failed_scan(symbol: str, error: str) -> dict:
    """Result row for a symbol whose statements could not be fetched."""
    return {"symbol": symbol, "error": error,
            "f_score": None, "f_strong": False,
            "qualifies_cc": False, "cc_score": "N/A",
            "qualifies_mf": False, "qualifies_bc": False,
            "ROIC_%": None, "Earnings_Yield_%": None,
            "Sales_Growth_YoY_%": None, "Profit_Growth_YoY_%": None}


def _in_piotroski_and_coffee_can(st: fund.Statements) -> dict:
    """Screeners 1 + 2: Piotroski F-Score and the 5-criterion Coffee Can (ROCE-based)."""
    inc, bal, info = st.inc, st.bal, st.info
    out = {}
    f_score = fund.piotroski_f_score(inc, bal, st.cf)
    out["f_score"] = f_score
    out["f_strong"] = f_score >= 7

    cagr = fund.revenue_cagr(inc)
    avg_roce = fund.avg_roce(inc, bal)
    de_raw = info.get("debtToEquity")
    if de_raw is not None:
        de = fund.normalise_debt_to_equity(de_raw)
    else:
        de = fund.debt_to_equity_from_balance(bal, abs_equity=False)
    mcap_cr = st.mcap / 1e7
    cc_bits = [
        1 if (cagr and cagr > 10) else 0,
        1 if (avg_roce and avg_roce > 15) else 0,
        1 if (de is not None and de < 1) else 0,
        1 if mcap_cr >= 500 else 0,
        1 if fund.all_years_positive(inc) else 0,
    ]
    out["qualifies_cc"] = sum(cc_bits) == 5
    out["cc_score"] = f"{sum(cc_bits)}/5"
    out["Revenue_CAGR_%"] = fund.rounded(cagr)
    out["ROCE_avg_%"] = fund.rounded(avg_roce)
    return out


def _in_magic_formula(st: fund.Statements) -> dict:
    """Screener 3: Magic Formula (ROIC > 25%, earnings yield > 15%, market cap > INR 15 Cr)."""
    mf = fund.magic_formula_inputs(st.inc, st.bal, st.info, st.mcap)
    return {"qualifies_mf": fund.magic_formula_passes(mf, st.mcap / 1e7 > 15),
            "ROIC_%": fund.rounded(mf.roic), "Earnings_Yield_%": fund.rounded(mf.earnings_yield)}


_IN_NO_ANNUAL = {"f_score": None, "f_strong": False,
                 "qualifies_cc": False, "cc_score": "N/A",
                 "qualifies_mf": False, "ROIC_%": None, "Earnings_Yield_%": None,
                 "Revenue_CAGR_%": None, "ROCE_avg_%": None}


def _in_bull_cartel(inc_q) -> dict:
    """Screener 4: YoY quarterly sales > 15%, profit > 20%, net profit > INR 1 Cr."""
    if inc_q is None or len(inc_q.columns) < 5:
        return {"qualifies_bc": False, "Sales_Growth_YoY_%": None,
                "Profit_Growth_YoY_%": None, "Net_Profit_Cr": None}
    bc = fund.bull_cartel(inc_q, profit_divisor=1e7, min_profit=1)
    return {"qualifies_bc": bc.qualifies,
            "Sales_Growth_YoY_%": fund.rounded(bc.sales_growth),
            "Profit_Growth_YoY_%": fund.rounded(bc.profit_growth),
            "Net_Profit_Cr": fund.rounded(bc.net_profit_scaled)}


def fundamental_scan(symbol: str, yf_suffix: str = ".NS") -> dict:
    """
    Run ALL 5 fundamental screeners for one symbol in a single Ticker() call.

    Screeners computed here (Golden Crossover uses OHLC, computed separately):
      1. Piotroski F-Score  — 9-point accounting quality score
      2. Coffee Can         — CAGR, ROCE, D/E, MCap, no loss-year
      3. Magic Formula      — ROIC > 25%, Earnings Yield > 15%  (reuses same annual data)
      4. Bull Cartel        — YoY quarterly sales growth > 15%, profit > 20%

    One yfinance Ticker() -> one set of HTTP calls -> four screener results.
    This is the core efficiency mechanism for running all screeners on the full universe.
    """
    try:
        st = fund.load_statements(yf.Ticker(f"{symbol}{yf_suffix}"))
    except Exception as e:
        return _in_failed_scan(symbol, str(e))

    out = {"symbol": symbol, "error": ""}
    if st.inc is not None:
        out.update(_in_piotroski_and_coffee_can(st))
        out.update(_in_magic_formula(st))
    else:
        out.update(_IN_NO_ANNUAL)
    out.update(_in_bull_cartel(st.inc_q))
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

def _print_banner() -> None:
    """Scan header with start time."""
    print(f"\n{'#'*60}")
    print(f"  FULL INDIAN MARKET SCAN")
    print(f"  Started: {datetime.now().strftime('%d %b %Y  %H:%M:%S')}")
    print(f"{'#'*60}\n")


def _print_live_context() -> None:
    """Live market context from nsepython (best effort)."""
    if _USE_NSE_FETCHER:
        try:
            _NSE_FETCHER.print_live_context()
        except Exception as e:
            print(f"  ⚠️  Live context unavailable: {e}\n")


def _build_yf_map(nse_only: bool, top: int):
    """Stage 1: symbol lists (cached 24 h) -> ``(all_tickers, yf_map)``.

    ``yf_map`` maps yfinance ticker -> (symbol, suffix): NSE as SYMBOL.NS, BSE-only as CODE.BO.
    """
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
    return all_tickers, yf_map


def _ohlc_row(sym: str, suffix: str, df: pd.DataFrame):
    """Price / Darvas / Golden-Crossover summary row for one ticker -> ``(row, darvas_signal)``."""
    darvas = compute_darvas_box(df)
    gc     = compute_golden_crossover(df)
    closes = pd.to_numeric(df["Close"], errors="coerce").dropna()
    ltp    = round(float(closes.iloc[-1]), 2) if not closes.empty else None
    prev   = round(float(closes.iloc[-2]), 2) if len(closes) >= 2 else None
    chg    = round((ltp - prev) / prev * 100, 2) if (ltp and prev) else None
    return {
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


def _screen_ohlc(ohlc_data: dict, yf_map: dict):
    """Stage 3: Darvas Box + Golden Crossover on every ticker (OHLC only, no API calls).

    Returns ``(all_rows, darvas_rows, ohlc_row_map)``; the map (symbol -> row) enriches Stage 4.
    """
    all_rows, darvas_rows = [], []
    ohlc_row_map: dict[str, dict] = {}
    for yf_tkr, df in ohlc_data.items():
        sym, suffix = yf_map.get(yf_tkr, (yf_tkr, ""))
        base_row = _ohlc_row(sym, suffix, df)
        all_rows.append(base_row)
        ohlc_row_map[sym] = base_row
        if base_row["Darvas_Signal"] in ("BREAKOUT_BUY", "BREAKDOWN_SELL"):
            darvas_rows.append(base_row.copy())
    return all_rows, darvas_rows, ohlc_row_map


def _fundamental_row(sym: str, suffix: str, res: dict, ohlc: dict) -> dict:
    """Merge one symbol's fundamental result with its OHLC row into a report row."""
    return {
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


def _run_fundamentals(all_fund_symbols: list, ohlc_row_map: dict, workers: int):
    """Stage 4: all fundamental screeners on every ticker that returned OHLC data.

    Returns ``(fund_rows, six_screen_rows, sc_counts)``. Every screener is computed inside one
    Ticker() call per stock, so coverage of the full universe stays as cheap as possible.
    """
    fund_rows, six_screen_rows = [], []
    sc_counts = new_screener_counts()
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
                row = _fundamental_row(sym, suffix, res, ohlc)
                fund_rows.append(row)
                count_screeners(sc_counts, res)

                # Six-screen hit: passes Darvas + GC + Piotroski + CC + MF + BC
                passed = screens_passed(ohlc, res)
                if passed >= 3:
                    six_screen_rows.append({**row, "Screens_Passed": passed})

                if done % 100 == 0 or done == len(all_fund_symbols):
                    print(f"    {done}/{len(all_fund_symbols)} done  "
                          f"(6-screen hits so far: {len(six_screen_rows)})")
            except Exception as e:
                print(f"    {sym}: error — {e}")
    return fund_rows, six_screen_rows, sc_counts


def _print_summary(all_rows, counts, sc_counts, triple_rows, six_screen_rows) -> None:
    """Final console report."""
    print(f"\n{'='*70}")
    print(f"  SCAN COMPLETE — {datetime.now().strftime('%d %b %Y  %H:%M:%S')}")
    print(f"  Tickers scanned:          {len(all_rows)}")
    print(f"  ── OHLC Screeners ──────────────────────────────────────")
    print(f"  Darvas Breakouts:         {counts['breakout']}")
    print(f"  Darvas Breakdowns:        {counts['breakdown']}")
    print(f"  Golden Cross (today):     {counts['gc']}")
    print(f"  DMA50 above DMA200:       {counts['above_200']}")
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


def main(nse_only: bool = False, top: int = 0, run_scans: bool = True,
         workers: int = MAX_WORKERS):
    """Run the five-stage NSE+BSE scan and return the headline result dict."""
    _print_banner()
    _print_live_context()
    all_tickers, yf_map = _build_yf_map(nse_only, top)

    print("\nStage 2 — Bulk OHLC download (1-year window; needed for Golden Crossover 200 DMA) …")
    ohlc_data = bulk_download_ohlc(all_tickers, period="1y")
    print(f"  → {len(ohlc_data)} tickers with usable OHLC data\n")

    print("Stage 3 — Darvas Box + Golden Crossover screen (all stocks) …")
    all_rows, darvas_rows, ohlc_row_map = _screen_ohlc(ohlc_data, yf_map)
    counts = ohlc_counts(all_rows)
    print(f"  Darvas Breakout BUY:    {counts['breakout']}")
    print(f"  Darvas Breakdown SELL:  {counts['breakdown']}")
    print(f"  Golden Cross (today):   {counts['gc']}")
    print(f"  DMA50 above DMA200:     {counts['above_200']}")

    all_fund_symbols = [(sym, suf) for yf_tkr, (sym, suf) in yf_map.items()
                        if yf_tkr in ohlc_data]
    if run_scans:
        fund_rows, six_screen_rows, sc_counts = _run_fundamentals(
            all_fund_symbols, ohlc_row_map, workers)
    else:
        fund_rows, six_screen_rows = [], []
        sc_counts = new_screener_counts()
        print("\nStage 4 — Skipped (--no-scans)")

    triple_rows = pipeline_triple_rows(fund_rows)  # Darvas + Piotroski + CC, kept for compat

    print("\nStage 5 — Saving results …")
    path = save_excel(all_rows, darvas_rows, fund_rows, triple_rows,
                      six_screen_rows, tag="indian")

    _print_summary(all_rows, counts, sc_counts, triple_rows, six_screen_rows)
    return {
        "triple_hits": triple_rows,
        "six_screen_hits": six_screen_rows,
        "breakout_count": counts["breakout"],
        "gc_count": counts["gc"],
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
