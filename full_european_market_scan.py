# full_european_market_scan.py
# ===========================
# Optimized EURO STOXX 50 universe scanner (50 major Eurozone equity stocks).
# Features:
#   - Static Metadata: Instant Name & Sector mapping (zero API calls for index list).
#   - Restricts slow financials & info scans exclusively to breakout candidates.
#   - Auto-caches Ticker data to prevent duplicate HTTP calls.
#
# Pipeline:
#   Stage 1 & 2 — Bulk download 1-year OHLC for all 50 tickers in one batch.
#   Stage 3 — Compute Darvas Box & 200-day MA trend locally (instant).
#   Stage 4 — Run parallel fundamental scans (Piotroski + Coffee Can + PEGY) on breakouts only.
#   Stage 5 — Save a beautifully styled Excel workbook.
#
# Usage:
#   python full_european_market_scan.py
#   python full_european_market_scan.py --top 10      # Test run first 10
#   python full_european_market_scan.py --no-scans    # Skip fundamental scans
#
# Install:
#   pip install yfinance pandas openpyxl requests

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pandas as pd

from scanner_core.darvas import get_profile
from scanner_core import fundamentals as fund
from scanner_core.pipeline import write_scan_workbook
from stock_utils import first_df as _first_df, row as _row, series as _series  # noqa: F401  (aliases kept for importers)
import yfinance as yf

try:
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    OPENPYXL_OK = True
except ImportError:
    OPENPYXL_OK = False

# ── Configuration & Constants ────────────────────────────────────────────────
DOWNLOAD_DIR = Path("./european_scan")
DOWNLOAD_DIR.mkdir(exist_ok=True)

DARVAS_CONFIRM   = 3
MAX_WORKERS       = 10
PIOTROSKI_STRONG  = 7

# Static Metadata for Euro Stoxx 50 components (removes 50 slow .info API calls)
EURO_STOXX_50_META = {
    "ADS.DE": ("Adidas", "Consumer Cyclical"),
    "ADYEN.AS": ("Adyen", "Financial Services"),
    "AD.AS": ("Ahold Delhaize", "Consumer Defensive"),
    "AI.PA": ("Air Liquide", "Basic Materials"),
    "AIR.PA": ("Airbus", "Industrials"),
    "ALV.DE": ("Allianz", "Financial Services"),
    "ABI.BR": ("Anheuser-Busch InBev", "Consumer Defensive"),
    "ARGX.BR": ("Argenx", "Healthcare"),
    "ASML.AS": ("ASML Holding", "Technology"),
    "CS.PA": ("Axa", "Financial Services"),
    "BAS.DE": ("BASF", "Basic Materials"),
    "BAYN.DE": ("Bayer", "Healthcare"),
    "BBVA.MC": ("BBVA", "Financial Services"),
    "SAN.MC": ("Banco Santander", "Financial Services"),
    "BMW.DE": ("BMW", "Consumer Cyclical"),
    "BNP.PA": ("BNP Paribas", "Financial Services"),
    "BN.PA": ("Danone", "Consumer Defensive"),
    "DBK.DE": ("Deutsche Bank", "Financial Services"),
    "DB1.DE": ("Deutsche Börse", "Financial Services"),
    "DHL.DE": ("Deutsche Post", "Industrials"),
    "DTE.DE": ("Deutsche Telekom", "Communication Services"),
    "ENEL.MI": ("Enel", "Utilities"),
    "ENI.MI": ("Eni", "Energy"),
    "EL.PA": ("EssilorLuxottica", "Healthcare"),
    "RACE.MI": ("Ferrari", "Consumer Cyclical"),
    "RMS.PA": ("Hermès", "Consumer Cyclical"),
    "IBE.MC": ("Iberdrola", "Utilities"),
    "ITX.MC": ("Inditex", "Consumer Cyclical"),
    "IFX.DE": ("Infineon Technologies", "Technology"),
    "INGA.AS": ("ING Group", "Financial Services"),
    "ISP.MI": ("Intesa Sanpaolo", "Financial Services"),
    "OR.PA": ("L'Oréal", "Consumer Defensive"),
    "MC.PA": ("LVMH", "Consumer Cyclical"),
    "MBG.DE": ("Mercedes-Benz Group", "Consumer Cyclical"),
    "MUV2.DE": ("Munich Re", "Financial Services"),
    "NDA-FI.HE": ("Nordea", "Financial Services"),
    "PRX.AS": ("Prosus", "Technology"),
    "RHM.DE": ("Rheinmetall", "Industrials"),
    "SAF.PA": ("Safran", "Industrials"),
    "SGO.PA": ("Saint-Gobain", "Industrials"),
    "SAN.PA": ("Sanofi", "Healthcare"),
    "SAP.DE": ("SAP", "Technology"),
    "SU.PA": ("Schneider Electric", "Industrials"),
    "SIE.DE": ("Siemens", "Industrials"),
    "ENR.DE": ("Siemens Energy", "Utilities"),
    "TTE.PA": ("TotalEnergies", "Energy"),
    "DG.PA": ("Vinci SA", "Industrials"),
    "UCG.MI": ("UniCredit", "Financial Services"),
    "VOW.DE": ("Volkswagen", "Consumer Cyclical"),
    "WKL.AS": ("Wolters Kluwer", "Professional Services")
}

# ── Financials Extractors ─────────────────────────────────────────────────────
# ── Technical Scans ───────────────────────────────────────────────────────────
def compute_darvas_box(df: pd.DataFrame, confirm: int = DARVAS_CONFIRM) -> dict:
    """Darvas box (europe profile); the shared engine excludes the current bar from box formation."""
    return get_profile("europe").compute(df, confirm)

# ── Fundamental Scan ──────────────────────────────────────────────────────────
def _eu_piotroski(res: dict, inc, bal, cf) -> None:
    """Screener 1: Piotroski F-Score (left as None if the statements are malformed)."""
    try:
        res["f_score"] = fund.piotroski_f_score(inc, bal, cf)
    except Exception:  # malformed statement frame
        pass


def _eu_coffee_can(res: dict, ticker, info, inc, bal, cf):
    """Screener 2: Coffee Can (6 criteria, C4 = market cap >= EUR 1B).

    Returns ``(ok, market_cap)``; ``ok`` is False when the screen aborted, in which case the
    valuation block is skipped exactly as before."""
    try:
        de_raw = info.get("debtToEquity")
        de = de_raw / 100 if de_raw is not None and de_raw > 10 else de_raw
        if de is None:
            de = fund.debt_to_equity_from_balance(
                bal, equity_rows=("Stockholders Equity", "Total Stockholder Equity"))
        mcap = info.get("marketCap") or getattr(ticker.fast_info, "market_cap", 0)
        cc = fund.coffee_can_flags(inc, bal, cf, de, ebit_rows=("EBIT", "Operating Income"))
        c = dict(cc.flags)
        c["C4"] = 1 if mcap >= 1e9 else 0  # >= 1 Billion EUR
        qualifies = sum(c.values()) == len(c)
        res.update({
            "cc_qualifies":   "PASS" if qualifies else "FAIL",
            "cc_score":       f"{sum(c.values())}/{len(c)}",
            "cc_rev_cagr":    round(cc.cagr, 2) if cc.cagr else None,
            "cc_roce_avg":    round(cc.avg_roce, 2) if cc.avg_roce else None,
            "cc_debt_equity": round(de, 2) if de else None,
        })
        return True, mcap
    except Exception:  # keep the F-Score even if Coffee Can inputs are malformed
        return False, None


def _eu_valuation(res: dict, info, mcap) -> None:
    """Screener 3: valuation and PEGY (PEG + dividend yield); skipped if inputs are unusable."""
    try:
        pe  = info.get("trailingPE") or info.get("forwardPE") or info.get("regularMarketPrice", 0) / (info.get("trailingEps") or 1)
        peg = info.get("pegRatio")
        dy_raw = info.get("dividendYield")
        dy = (dy_raw if dy_raw >= 0.15 else dy_raw * 100) if dy_raw is not None else 0.0
        pegy = float(peg) + dy if peg is not None else None

        res.update({
            "market_cap_b":    round(mcap / 1e9, 2) if mcap else None,
            "pe_ratio":        round(pe, 2) if pe and pe > 0 else None,
            "pb_ratio":        round(info.get("priceToBook"), 2) if info.get("priceToBook") else None,
            "dividend_yield":  round(dy, 2),
            "pegy_ratio":      round(pegy, 2) if pegy else None,
        })
    except Exception:  # missing/odd valuation fields: leave the valuation keys out
        pass


def fundamental_scan(symbol: str) -> dict:
    """Fetch financials and execute F-Score, Coffee Can, and PEGY calculations in one Ticker call."""
    res = {"symbol": symbol, "f_score": None, "cc_qualifies": "FAIL", "error": None}
    try:
        ticker = yf.Ticker(symbol)
        inc = _first_df(ticker, "income_stmt", "financials")
        bal = _first_df(ticker, "balance_sheet")
        cf  = _first_df(ticker, "cash_flow", "cashflow")

        info = {}
        try:
            info = ticker.info or {}
        except Exception:  # info is a network call; absence is not fatal
            pass

        if inc is None or inc.empty:
            res["error"] = "no_financial_statements"
            return res
    except Exception as e:
        res["error"] = str(e)[:80]
        return res

    _eu_piotroski(res, inc, bal, cf)
    ok, mcap = _eu_coffee_can(res, ticker, info, inc, bal, cf)
    if ok:  # mcap is only known when the Coffee Can block ran to the market-cap step
        _eu_valuation(res, info, mcap)
    return res


# ── Sheet Styling ─────────────────────────────────────────────────────────────
def style_excel_sheet(ws):
    """Format and style an openpyxl sheet to premium standards."""
    if not OPENPYXL_OK:
        return
    
    fill_header = PatternFill(start_color="1A365D", end_color="1A365D", fill_type="solid")
    fill_zebra  = PatternFill(start_color="F7FAFC", end_color="F7FAFC", fill_type="solid")
    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    font_body   = Font(name="Calibri", size=11, bold=False, color="000000")
    
    border_thin = Border(
        left=Side(style='thin', color='E2E8F0'),
        right=Side(style='thin', color='E2E8F0'),
        top=Side(style='thin', color='E2E8F0'),
        bottom=Side(style='thin', color='E2E8F0')
    )

    # Style Header
    for col_idx in range(1, ws.max_column + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border_thin

    # Style Data Rows
    for row_idx in range(2, ws.max_row + 1):
        is_zebra = (row_idx % 2 == 1)
        for col_idx in range(1, ws.max_column + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.font = font_body
            cell.border = border_thin
            if is_zebra:
                cell.fill = fill_zebra

            header = str(ws.cell(row=1, column=col_idx).value or "").upper()
            val = cell.value

            # Alignment
            if any(k in header for k in ["SYMBOL", "SIGNAL", "SCORE", "CLASS", "QUALIFIES", "EXCHANGE"]):
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")

            # Formats
            if isinstance(val, (int, float)):
                cell.alignment = Alignment(horizontal="right", vertical="center")
                if "%" in header or "YIELD" in header or "CAGR" in header or "ROCE" in header:
                    cell.number_format = '0.00"%"'
                elif "CAP" in header or "LTP" in header or "BOX" in header or "PRICE" in header or "200MA" in header:
                    cell.number_format = '#,##0.00'
                elif "PEG" in header or "PE_RATIO" in header or "PB_RATIO" in header or "DEBT_EQUITY" in header:
                    cell.number_format = '0.00'

    ws.row_dimensions[1].height = 28
    for r in range(2, ws.max_row + 1):
        ws.row_dimensions[r].height = 20

    # Auto Column Widths
    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

    ws.views.sheetView[0].showGridLines = True

# ── Main Scanner ──────────────────────────────────────────────────────────────
def _parse_args():
    """CLI arguments (flags unchanged)."""
    parser = argparse.ArgumentParser(description="Optimized Euro Stoxx 50 Scanner")
    parser.add_argument("--top", type=int, default=0, help="Scan only first N tickers")
    parser.add_argument("--no-scans", action="store_true", default=False, help="Skip Stage 4 fundamental scans")
    return parser.parse_args()


def _technical_row(sym: str, df: pd.DataFrame) -> dict:
    """Price / 200-day MA trend / Darvas summary row for one stock (valuation fields blank)."""
    closes  = pd.to_numeric(df["Close"], errors="coerce").dropna()
    ltp     = round(float(closes.iloc[-1]), 2)
    prev    = round(float(closes.iloc[-2]), 2) if len(closes) >= 2 else None
    chg_pct = round((ltp - prev) / prev * 100, 2) if prev else None

    # 200-day Moving Average
    ma_200 = round(closes.rolling(200).mean().iloc[-1], 2) if len(closes) >= 200 else None
    dist_ma = round(((ltp - ma_200) / ma_200 * 100), 2) if ma_200 else None
    trend_sig = "Above 200MA (Uptrend)" if dist_ma and dist_ma > 5 else "Below 200MA (Downtrend)" if dist_ma and dist_ma < -5 else "Near 200MA (Consolidation)" if dist_ma else "Insufficient History"

    # Darvas Box
    darv = compute_darvas_box(df)
    name, sector = EURO_STOXX_50_META.get(sym, (sym, "—"))

    return {
        "Symbol":             sym,
        "Name":               name,
        "Sector":             sector,
        "LTP":                ltp,
        "Change%":            chg_pct,
        "200_Day_MA":         ma_200,
        "Distance_to_200MA%": dist_ma,
        "Trend_Signal":       trend_sig,
        "Darvas_Signal":      darv.get("signal"),
        "Box_Top":            darv.get("box_top"),
        "Box_Bottom":         darv.get("box_bottom"),
        "Upside_to_Top%":     darv.get("upside_pct"),
        "Position_in_Box%":   darv.get("pos_in_box"),
        # Valuation fallbacks
        "Market_Cap_B":       None,
        "P_E_Ratio":          None,
        "P_B_Ratio":          None,
        "Dividend_Yield%":    0.0,
    }


def _screen_symbols(symbols: list, raw_ohlc: pd.DataFrame):
    """Stage 3: local technical scan -> ``(all_stocks_map, breakout_symbols)``."""
    all_stocks_map = {}
    breakout_symbols = []
    for sym in symbols:
        try:
            df = raw_ohlc[sym].dropna() if isinstance(raw_ohlc.columns, pd.MultiIndex) else raw_ohlc.dropna()
            if df.empty or len(df) < 20:
                continue
            row = _technical_row(sym, df)
            all_stocks_map[sym] = row
            if row["Darvas_Signal"] == "BREAKOUT_BUY":
                breakout_symbols.append(sym)
        except Exception as e:
            print(f"  ⚠️ Technical error on {sym}: {e}")
    return all_stocks_map, breakout_symbols


def _fundamental_row(sym: str, res: dict, tech: dict) -> dict:
    """Merge technicals with the fundamental scan result."""
    return {
        "Symbol":             sym,
        "Name":               tech.get("Name"),
        "Sector":             tech.get("Sector"),
        "LTP":                tech.get("LTP"),
        "Change%":            tech.get("Change%"),
        "Darvas_Signal":      tech.get("Darvas_Signal"),
        "200_Day_MA":         tech.get("200_Day_MA"),
        "Distance_to_200MA%": tech.get("Distance_to_200MA%"),
        "P_E_Ratio":          res.get("pe_ratio"),
        "P_B_Ratio":          res.get("pb_ratio"),
        "Dividend_Yield%":    res.get("dividend_yield"),
        "PEGY_Ratio":         res.get("pegy_ratio"),
        "Piotroski_Score":    res.get("f_score"),
        "Piotroski_Class":    res.get("f_interpretation"),
        "CoffeeCan_Score":    res.get("cc_score"),
        "CoffeeCan_Class":    res.get("cc_qualifies"),
        "CAGR_Revenue%":      res.get("cc_rev_cagr"),
        "ROCE_Average%":      res.get("cc_roce_avg"),
        "Debt_Equity":        res.get("cc_debt_equity"),
        "Error":              res.get("error")
    }


def _run_fundamentals(breakout_symbols: list, all_stocks_map: dict):
    """Stage 4: fundamentals on breakout candidates only -> ``(fund_rows, triple_hits)``."""
    fund_rows, triple_hits = [], []
    print(f"\nStage 4 — Fetching financials & scans for {len(breakout_symbols)} breakouts ({MAX_WORKERS} workers) …")
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fundamental_scan, sym): sym for sym in breakout_symbols}
        for future in as_completed(futures):
            sym = futures[future]
            done += 1
            try:
                res = future.result()
                tech = all_stocks_map.get(sym, {})
                fund_row = _fundamental_row(sym, res, tech)
                fund_rows.append(fund_row)

                # Update the master mapping so All_Stocks sheet contains PE/PB/Yield for breakouts
                tech.update({
                    "Market_Cap_B":    res.get("market_cap_b"),
                    "P_E_Ratio":       res.get("pe_ratio"),
                    "P_B_Ratio":       res.get("pb_ratio"),
                    "Dividend_Yield%": res.get("dividend_yield"),
                })

                if res.get("f_score") and res.get("f_score") >= PIOTROSKI_STRONG and res.get("cc_qualifies") == "PASS":
                    triple_hits.append(fund_row.copy())

                if done % 5 == 0 or done == len(breakout_symbols):
                    print(f"    {done}/{len(breakout_symbols)} processed (Triple Hits so far: {len(triple_hits)})")
            except Exception as e:
                print(f"    ❌ Fundamental error on {sym}: {e}")
    return fund_rows, triple_hits


def _save_workbook(all_stocks, fund_rows, triple_hits):
    """Stage 5: write the four-sheet Excel workbook and return its path."""
    darvas_signals = [s for s in all_stocks if s["Darvas_Signal"] in ("BREAKOUT_BUY", "BREAKDOWN_SELL")]
    date_str = datetime.today().strftime("%Y%m%d_%H%M")
    excel_path = DOWNLOAD_DIR / f"european_market_scan_{date_str}.xlsx"
    write_scan_workbook(excel_path, [
        (all_stocks,     "All_Stocks",     "Change%"),
        (darvas_signals, "Darvas_Signals", "Upside_to_Top%"),
        (fund_rows,      "Fundamentals",   "Piotroski_Score"),
        (triple_hits,    "Triple_Hits",    "Piotroski_Score"),
    ], style_fn=style_excel_sheet if OPENPYXL_OK else None)
    return excel_path


def _print_summary(excel_path, all_stocks, breakout_symbols, fund_rows, triple_hits) -> None:
    """Final console report."""
    print(f"\n{'='*60}")
    print(f"  SCAN COMPLETE — {datetime.now().strftime('%d %b %Y  %H:%M:%S')}")
    print(f"  Total European Stocks Scanned: {len(all_stocks)}")
    print(f"  Darvas Breakouts Found:       {len(breakout_symbols)}")
    print(f"  Fundamentals Scanned:         {len(fund_rows)}")
    print(f"  ★ TRIPLE HITS FOUND:          {len(triple_hits)}")
    print(f"{'='*60}")

    if triple_hits:
        print("\n  Triple Hit Stocks:")
        for r in sorted(triple_hits, key=lambda x: x.get("Piotroski_Score") or 0, reverse=True):
            print(f"    • {r['Symbol']:<10} {r['Name']:<30} F-Score: {r['Piotroski_Score']}/9  CC: {r['CoffeeCan_Score']}  LTP: €{r['LTP']}")
    else:
        print("\n  No Triple Hit stocks found today.")

    print(f"\n  📊 Excel report saved → {excel_path}\n")


def main():
    """CLI entry: bulk Euro Stoxx 50 download -> technicals -> fundamentals on breakouts -> Excel."""
    args = _parse_args()

    symbols = list(EURO_STOXX_50_META.keys())
    if args.top:
        symbols = symbols[:args.top]

    print(f"\n{'#'*60}")
    print(f"  OPTIMIZED EURO STOXX 50 SCANNER")
    print(f"  Started: {datetime.now().strftime('%d %b %Y  %H:%M:%S')}")
    print(f"{'#'*60}")

    # Stage 1 & 2: Bulk OHLC download (1 request for all 50 tickers - extremely fast)
    print(f"\nStage 1 & 2 — Bulk downloading 1y history for {len(symbols)} stocks …")
    try:
        raw_ohlc = yf.download(symbols, period="1y", group_by="ticker", auto_adjust=True, threads=True, progress=False)
    except Exception as e:
        sys.exit(f"❌ Failed to download bulk OHLC data: {e}")

    print("\nStage 3 — Computing Darvas Box & 200-day MA Trend locally …")
    all_stocks_map, breakout_symbols = _screen_symbols(symbols, raw_ohlc)
    print(f"  Processed {len(all_stocks_map)} stocks. Found {len(breakout_symbols)} Darvas breakouts.")

    # Stage 4: only breakout candidates (huge speedup)
    fund_rows, triple_hits = [], []
    if not args.no_scans and breakout_symbols:
        fund_rows, triple_hits = _run_fundamentals(breakout_symbols, all_stocks_map)
    else:
        print("\nStage 4 — Skipped (No active breakouts or --no-scans set)")

    print("\nStage 5 — Saving Excel workbook …")
    all_stocks = list(all_stocks_map.values())
    excel_path = _save_workbook(all_stocks, fund_rows, triple_hits)
    _print_summary(excel_path, all_stocks, breakout_symbols, fund_rows, triple_hits)


if __name__ == "__main__":
    main()
