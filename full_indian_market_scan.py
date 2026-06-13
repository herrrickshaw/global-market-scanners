# full_indian_market_scan.py
# ==========================
# Full NSE + BSE universe scanner (~2,600+ unique Indian equity stocks).
#
# Pipeline:
#   Stage 1 — Fetch all NSE EQ symbols (via bhavcopy) + BSE-only symbols
#   Stage 2 — Bulk OHLC download via yfinance (batched, 3-month window)
#   Stage 3 — Darvas Box screen on every stock
#   Stage 4 — Piotroski F-Score + Coffee Can on Darvas BREAKOUT candidates only
#   Stage 5 — Save Excel workbook with ranked results
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

def fetch_nse_symbols() -> list[str]:
    """Get all NSE EQ symbols via nsepython (NSE archives bhavcopy CSV)."""
    today = datetime.today()
    for offset in range(7):
        date_str = (today - timedelta(days=offset)).strftime("%d-%m-%Y")
        try:
            df = _nse_get_bhavcopy(date_str)
            if df is None or df.empty:
                continue
            # Column has a leading space in older CSVs: ' SERIES'
            series_col = next((c for c in df.columns if "SERIES" in c.upper()), None)
            sym_col    = next((c for c in df.columns if "SYMBOL" in c.upper()), None)
            if series_col and sym_col:
                eq = df[df[series_col].astype(str).str.strip() == "EQ"]
                syms = eq[sym_col].dropna().str.strip().tolist()
                if syms:
                    return sorted(syms)
        except Exception:
            continue
    print("⚠️  Could not fetch NSE symbol list from NSE archives")
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


# ── Piotroski F-Score ─────────────────────────────────────────────────────────

def _first_df(ticker, *attrs):
    for attr in attrs:
        df = getattr(ticker, attr, None)
        if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
            return df
    return None


def _row(df, *row_names, col: int = 0):
    if df is None or df.empty:
        return None
    for name in row_names:
        if name in df.index:
            try:
                val = df.loc[name].iloc[col]
                return float(val) if pd.notna(val) else None
            except Exception:
                pass
    return None


def fundamental_scan(symbol: str, yf_suffix: str = ".NS") -> dict:
    """
    Piotroski F-Score + Coffee Can in a single yfinance Ticker call.
    Coffee Can is skipped entirely when F-Score < 7 (saves one API round-trip).
    Market cap is read from fast_info (no heavy ticker.info call needed).
    """
    try:
        ticker = yf.Ticker(f"{symbol}{yf_suffix}")
        inc = _first_df(ticker, "income_stmt", "financials")
        bal = _first_df(ticker, "balance_sheet")
        cf  = _first_df(ticker, "cash_flow", "cashflow")
        if inc is None:
            return {"symbol": symbol, "f_score": None, "qualifies": False, "error": "no_data"}
    except Exception as e:
        return {"symbol": symbol, "f_score": None, "qualifies": False, "error": str(e)}

    # ── Piotroski F-Score ─────────────────────────────────────────────────────
    ni0 = _row(inc, "Net Income", col=0);  a0 = _row(bal, "Total Assets", col=0)
    ni1 = _row(inc, "Net Income", col=1);  a1 = _row(bal, "Total Assets", col=1)
    roa0 = (ni0 / a0) if (ni0 and a0) else None
    roa1 = (ni1 / a1) if (ni1 and a1) else None
    ocf0 = _row(cf, "Operating Cash Flow", "Total Cash From Operating Activities")

    f1 = 1 if (roa0 and roa0 > 0) else 0
    f2 = 1 if (ocf0 and ocf0 > 0) else 0
    f3 = 1 if (roa0 and roa1 and roa0 > roa1) else 0
    f4 = 1 if (ocf0 and a0 and roa0 and (ocf0 / a0) > roa0) else 0

    ltd0 = _row(bal, "Long Term Debt", col=0) or 0
    ltd1 = _row(bal, "Long Term Debt", col=1) or 0
    f5 = 1 if (a0 and a1 and (ltd0/a0) < (ltd1/a1)) else 0

    ca0 = _row(bal, "Current Assets", "Total Current Assets", col=0)
    cl0 = _row(bal, "Current Liabilities", "Total Current Liabilities", col=0)
    ca1 = _row(bal, "Current Assets", "Total Current Assets", col=1)
    cl1 = _row(bal, "Current Liabilities", "Total Current Liabilities", col=1)
    f6 = 1 if (ca0 and cl0 and ca1 and cl1 and (ca0/cl0) > (ca1/cl1)) else 0

    sh0 = _row(bal, "Share Issued", col=0);  sh1 = _row(bal, "Share Issued", col=1)
    f7 = (1 if sh0 <= sh1 else 0) if (sh0 and sh1) else 1

    rev0 = _row(inc, "Total Revenue", col=0);  gp0 = _row(inc, "Gross Profit", col=0)
    rev1 = _row(inc, "Total Revenue", col=1);  gp1 = _row(inc, "Gross Profit", col=1)
    f8 = 1 if (gp0 and rev0 and gp1 and rev1 and (gp0/rev0) > (gp1/rev1)) else 0

    f9 = 1 if (rev0 and a0 and rev1 and a1 and (rev0/a0) > (rev1/a1)) else 0

    f_score = f1 + f2 + f3 + f4 + f5 + f6 + f7 + f8 + f9

    # ── Coffee Can — skip entirely when Piotroski < 7 ─────────────────────────
    if f_score < 7:
        return {"symbol": symbol, "f_score": f_score, "f_strong": False,
                "qualifies": False, "cc_score": "—"}

    def series(df, *rows):
        for name in rows:
            if df is not None and name in df.index:
                return [float(v) for v in df.loc[name].dropna() if pd.notna(v)]
        return []

    c = {}
    revs = series(inc, "Total Revenue")
    if len(revs) >= 2:
        cagr = ((revs[0] / revs[-1]) ** (1 / (len(revs) - 1)) - 1) * 100 if revs[-1] > 0 else None
        c["C1"] = 1 if (cagr and cagr > 10) else 0
    else:
        c["C1"] = 0

    ebit_s = series(inc, "EBIT", "Operating Income", "Ebit")
    ta_s   = series(bal, "Total Assets")
    cl_s   = series(bal, "Current Liabilities", "Total Current Liabilities")
    roce_l = [ebit_s[i] / (ta_s[i] - cl_s[i]) * 100
              for i in range(min(len(ebit_s), len(ta_s), len(cl_s)))
              if (ta_s[i] - cl_s[i]) > 0]
    c["C2"] = 1 if (roce_l and sum(roce_l) / len(roce_l) > 15) else 0

    ltd_s = series(bal, "Long Term Debt")
    eq_s  = series(bal, "Stockholders Equity", "Total Stockholder Equity",
                   "Total Equity Gross Minority Interest")
    c["C3"] = (1 if (ltd_s[0] / eq_s[0]) < 1 else 0) if (ltd_s and eq_s and eq_s[0] != 0) else 0

    try:
        mcap = ticker.fast_info.market_cap or 0
    except Exception:
        mcap = 0
    c["C4"] = 1 if mcap / 1e7 >= 500 else 0   # ≥ ₹500 Cr

    ni_s = series(inc, "Net Income")
    c["C5"] = 1 if (ni_s and all(n > 0 for n in ni_s)) else 0

    cc_total  = sum(c.values())
    qualifies = cc_total == len(c)
    return {
        "symbol":   symbol,
        "f_score":  f_score,
        "f_strong": True,
        "qualifies": qualifies,
        "cc_score": f"{cc_total}/{len(c)}",
    }


# ── Excel export ───────────────────────────────────────────────────────────────

def save_excel(all_rows, darvas_rows, fund_rows, triple_rows, tag="indian"):
    date_str = datetime.today().strftime("%Y%m%d_%H%M")
    path     = DOWNLOAD_DIR / f"{tag}_full_scan_{date_str}.xlsx"

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        def write_sheet(rows, name, sort_col=None):
            if not rows:
                pd.DataFrame().to_excel(writer, sheet_name=name, index=False)
                return
            df = pd.DataFrame(rows)
            if sort_col and sort_col in df.columns:
                df = df.sort_values(sort_col, ascending=False)
            df.to_excel(writer, sheet_name=name, index=False)

        write_sheet(all_rows,    "All_Stocks",     sort_col="Change%")
        write_sheet(darvas_rows, "Darvas_Signals",  sort_col="Upside_to_Top%")
        write_sheet(fund_rows,   "Fundamentals",    sort_col="Piotroski_Score")
        write_sheet(triple_rows, "Triple_Hits",     sort_col="Piotroski_Score")

    print(f"\n  📊  Excel saved → {path}")
    return path


# ── Main ───────────────────────────────────────────────────────────────────────

def main(nse_only: bool = False, top: int = 0, run_scans: bool = True,
         workers: int = MAX_WORKERS):

    print(f"\n{'#'*60}")
    print(f"  FULL INDIAN MARKET SCAN")
    print(f"  Started: {datetime.now().strftime('%d %b %Y  %H:%M:%S')}")
    print(f"{'#'*60}\n")

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
    print("\nStage 2 — Bulk OHLC download (3-month window) …")
    ohlc_data = bulk_download_ohlc(all_tickers, period="3mo")
    print(f"  → {len(ohlc_data)} tickers with usable OHLC data\n")

    # ── Stage 3: Darvas Box screen ───────────────────────────────────────────
    print("Stage 3 — Darvas Box screen …")
    all_rows, darvas_rows, breakout_symbols = [], [], []

    for yf_tkr, df in ohlc_data.items():
        sym, suffix = yf_map.get(yf_tkr, (yf_tkr, ""))
        result = compute_darvas_box(df)
        closes = pd.to_numeric(df["Close"], errors="coerce").dropna()
        ltp    = round(float(closes.iloc[-1]), 2) if not closes.empty else None
        prev   = round(float(closes.iloc[-2]), 2) if len(closes) >= 2 else None
        chg_pct = round((ltp - prev) / prev * 100, 2) if (ltp and prev and prev) else None

        base_row = {
            "Symbol":        sym,
            "Suffix":        suffix,
            "LTP":           ltp,
            "Prev_Close":    prev,
            "Change%":       chg_pct,
            "Darvas_Signal": result.get("signal"),
            "Box_Top":       result.get("box_top"),
            "Box_Bottom":    result.get("box_bottom"),
            "Upside_to_Top%": result.get("upside_to_top_pct"),
            "Position_in_Box%": result.get("position_in_box_pct"),
            "Data_Points":   result.get("data_points"),
        }
        all_rows.append(base_row)

        if result.get("signal") in ("BREAKOUT_BUY", "BREAKDOWN_SELL"):
            darvas_rows.append(base_row.copy())
        if result.get("signal") == "BREAKOUT_BUY":
            breakout_symbols.append((sym, suffix))

    breakouts = [s for s, _ in breakout_symbols]
    breakdowns = [r["Symbol"] for r in darvas_rows if r["Darvas_Signal"] == "BREAKDOWN_SELL"]
    print(f"  Breakout BUY:  {len(breakout_symbols)}")
    print(f"  Breakdown SELL:{len(breakdowns)}")
    print(f"  In Box:        {len(all_rows) - len(darvas_rows)}")

    # ── Stage 4: Fundamental scans on breakout candidates ────────────────────
    fund_rows, triple_rows = [], []

    if run_scans and breakout_symbols:
        # Keep only the N freshest breakouts (those closest to the box top, i.e.
        # smallest absolute upside — stocks that broke out most recently without
        # having run away). This dramatically cuts API calls on large universes.
        def _upside(sym_suf):
            row = next((r for r in darvas_rows if r["Symbol"] == sym_suf[0]), None)
            return abs(row["Upside_to_Top%"]) if row and row.get("Upside_to_Top%") is not None else 999
        if len(breakout_symbols) > MAX_FUND_CANDIDATES:
            breakout_symbols = sorted(breakout_symbols, key=_upside)[:MAX_FUND_CANDIDATES]
            print(f"  (pre-filtered to {MAX_FUND_CANDIDATES} freshest breakouts)")

        print(f"\nStage 4 — Fundamental scans on {len(breakout_symbols)} breakout candidates "
              f"({workers} workers) …")
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(fundamental_scan, sym, suffix): (sym, suffix)
                for sym, suffix in breakout_symbols
            }
            for future in as_completed(futures):
                sym, suffix = futures[future]
                done += 1
                try:
                    res = future.result()
                    darvas_row = next((r for r in darvas_rows if r["Symbol"] == sym), {})
                    fund_row = {
                        "Symbol":          sym,
                        "Suffix":          suffix,
                        "LTP":             darvas_row.get("LTP"),
                        "Change%":         darvas_row.get("Change%"),
                        "Darvas_Signal":   darvas_row.get("Darvas_Signal"),
                        "Upside_to_Top%":  darvas_row.get("Upside_to_Top%"),
                        "Piotroski_Score": res.get("f_score"),
                        "Piotroski_Strong": "YES" if res.get("f_strong") else "NO",
                        "CoffeeCan":       "PASS" if res.get("qualifies") else "FAIL",
                        "CC_Score":        res.get("cc_score"),
                        "Error":           res.get("error", ""),
                    }
                    fund_rows.append(fund_row)

                    if res.get("f_strong") and res.get("qualifies"):
                        triple_rows.append(fund_row.copy())

                    if done % 20 == 0 or done == len(breakout_symbols):
                        print(f"    {done}/{len(breakout_symbols)} done  "
                              f"(triple hits so far: {len(triple_rows)})")
                except Exception as e:
                    print(f"    {sym}: error — {e}")
    else:
        print("\nStage 4 — Skipped (--no-scans)")

    # ── Stage 5: Save results ─────────────────────────────────────────────────
    print("\nStage 5 — Saving results …")
    path = save_excel(all_rows, darvas_rows, fund_rows, triple_rows, tag="indian")

    # Summary
    print(f"\n{'='*60}")
    print(f"  SCAN COMPLETE — {datetime.now().strftime('%d %b %Y  %H:%M:%S')}")
    print(f"  Tickers scanned:     {len(all_rows)}")
    print(f"  Darvas Breakouts:    {len(breakout_symbols)}")
    print(f"  Fundamental scanned: {len(fund_rows)}")
    print(f"  ★ TRIPLE HITS:       {len(triple_rows)}")
    if triple_rows:
        print(f"\n  Triple-hit stocks:")
        for r in sorted(triple_rows, key=lambda x: x.get("Piotroski_Score") or 0, reverse=True):
            print(f"    {r['Symbol']:<20} F={r['Piotroski_Score']}/9  CC={r['CC_Score']}  "
                  f"LTP={r['LTP']}  +{r['Upside_to_Top%']}% to box top")
    print(f"{'='*60}\n")
    return {"triple_hits": triple_rows, "breakouts": len(breakout_symbols),
            "total_scanned": len(all_rows), "excel_path": str(path)}


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
