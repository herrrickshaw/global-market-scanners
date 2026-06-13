# full_us_market_scan.py
# =======================
# Full NASDAQ + NYSE universe scanner (~8,000+ US equity listings).
#
# Pipeline:
#   Stage 1 — Fetch full NASDAQ + NYSE symbol lists (NASDAQ FTP / fallback)
#   Stage 2 — Bulk OHLC download via yfinance (batched, 3-month window)
#   Stage 3 — Darvas Box screen on every stock
#   Stage 4 — Piotroski F-Score + Coffee Can on Darvas BREAKOUT candidates only
#   Stage 5 — Save Excel workbook with ranked results
#
# Output sheets:
#   All_Stocks      — price summary for every stock scanned
#   Darvas_Signals  — breakout / breakdown alerts ranked by upside
#   Fundamentals    — Piotroski + Coffee Can for breakout candidates
#   Triple_Hits     — BREAKOUT_BUY + Piotroski ≥ 7 + Coffee Can PASS
#
# Usage:
#   python full_us_market_scan.py                   # full NASDAQ + NYSE
#   python full_us_market_scan.py --nasdaq-only     # NASDAQ listings only
#   python full_us_market_scan.py --top 500         # limit to first 500 symbols
#   python full_us_market_scan.py --no-scans        # Darvas only (fast mode)
#   python full_us_market_scan.py --workers 8       # parallel fundamental scans
#   python full_us_market_scan.py --min-price 5     # exclude penny stocks (< $5)
#
# Install:
#   pip install yfinance pandas openpyxl requests
#   pip install "git+https://github.com/Nasdaq/NasdaqCloudDataService-SDK-Python.git"
#
# Nasdaq Cloud Data Service (NCDS) SDK — optional, requires paid Nasdaq credentials.
# Place credentials at ~/.nasdaq/ncds_auth.json:
#   {
#     "oauth": {
#       "endpoint": "https://<auth_host>/auth/realms/pro-realm/protocol/openid-connect/token",
#       "client_id": "<client_id>",
#       "client_secret": "<client_secret>"
#     },
#     "kafka": { "bootstrap_servers": "<streams_host>:9094" },
#     "topic": "NLSPLUS-CTA-V4"
#   }
# Without credentials, symbols fall back to NASDAQ FTP → SEC EDGAR.

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

try:
    import yfinance as yf
except ImportError:
    sys.exit("❌  pip install yfinance")

try:
    from ncdssdk import NCDSClient as _NCDSClient
    _NCDS_AVAILABLE = True
except ImportError:
    _NCDS_AVAILABLE = False

_NCDS_CREDS_PATH = Path.home() / ".nasdaq" / "ncds_auth.json"

# ── Constants ──────────────────────────────────────────────────────────────────
DOWNLOAD_DIR   = Path("./us_full_scan")
DOWNLOAD_DIR.mkdir(exist_ok=True)

DARVAS_CONFIRM      = 3
BATCH_SIZE          = 300
SLEEP_BETWEEN       = 1.0
MAX_WORKERS         = 12
MAX_FUND_CANDIDATES = 300  # cap Stage 4 to N freshest breakouts
SYMBOL_CACHE_TTL    = 86400

NASDAQ_FTP_BASE      = "https://ftp.nasdaqtrader.com/dynamic/SymDir"
NASDAQ_FTP_BASE_HTTP = "http://ftp.nasdaqtrader.com/dynamic/SymDir"

_SYMBOL_CACHE = DOWNLOAD_DIR / ".symbols_cache.json"


def _load_symbol_cache():
    try:
        if _SYMBOL_CACHE.exists():
            data = json.loads(_SYMBOL_CACHE.read_text())
            if time.time() - data.get("ts", 0) < SYMBOL_CACHE_TTL:
                return data.get("nasdaq", []), data.get("nyse", [])
    except Exception:
        pass
    return None, None


def _save_symbol_cache(nasdaq_syms, nyse_syms):
    try:
        _SYMBOL_CACHE.write_text(json.dumps({"ts": time.time(), "nasdaq": nasdaq_syms, "nyse": nyse_syms}))
    except Exception:
        pass


# ── Symbol fetch ───────────────────────────────────────────────────────────────

def _load_ncds_creds():
    """Load Nasdaq NCDS credentials from ~/.nasdaq/ncds_auth.json, or None."""
    if not _NCDS_AVAILABLE or not _NCDS_CREDS_PATH.exists():
        return None
    try:
        return json.loads(_NCDS_CREDS_PATH.read_text())
    except Exception:
        return None


def fetch_symbols_from_ncds() -> tuple:
    """
    Fetch NASDAQ + NYSE symbol lists via Nasdaq Cloud Data Service SDK.
    Consumes SeqDirectoryMessage events from the configured Kafka topic.
    Returns (nasdaq_list, nyse_list) or ([], []) if unavailable.
    """
    creds = _load_ncds_creds()
    if not creds:
        return [], []

    oauth = creds.get("oauth", {})
    kafka = creds.get("kafka", {})
    topic = creds.get("topic", "NLSPLUS-CTA-V4")

    security_cfg = {
        "oauth.token.endpoint.uri": oauth.get("endpoint", ""),
        "oauth.client.id":          oauth.get("client_id", ""),
        "oauth.client.secret":      oauth.get("client_secret", ""),
    }
    kafka_cfg = {
        "bootstrap.servers": kafka.get("bootstrap_servers", ""),
        "auto.offset.reset": "earliest",
    }

    try:
        client   = _NCDSClient(security_cfg, kafka_cfg)
        consumer = client.ncds_kafka_consumer(topic)
        nasdaq, nyse = [], []
        empty_polls  = 0
        while empty_polls < 5:
            msgs = consumer.consume(num_messages=500, timeout=3)
            if not msgs:
                empty_polls += 1
                continue
            empty_polls = 0
            for msg in msgs:
                val = msg.value() if callable(msg.value) else msg.value
                if not isinstance(val, dict):
                    continue
                if val.get("schema_name") != "SeqDirectoryMessage":
                    continue
                sym   = str(val.get("symbol", "")).strip().upper()
                mkt   = str(val.get("marketClass", "")).strip().upper()
                if not sym or any(c in sym for c in ("^", "/", "$", ".")):
                    continue
                if mkt in ("Q", "G", "S", ""):    # NASDAQ market tiers
                    nasdaq.append(sym)
                else:
                    nyse.append(sym)
        consumer.close()
        return list(dict.fromkeys(nasdaq)), list(dict.fromkeys(nyse))
    except Exception as e:
        print(f"  ⚠️  NCDS SDK error: {e}")
        return [], []


def _parse_nasdaq_file(text: str, exchange_filter=None) -> list[str]:
    """
    Parse a NASDAQ FTP symbol file (pipe-delimited).
    nasdaqlisted.txt columns: Symbol|Security Name|Market Category|Test Issue|...
    otherlisted.txt columns:  ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|...
    """
    symbols = []
    lines   = text.strip().splitlines()
    if not lines:
        return symbols

    header = [h.strip().lower() for h in lines[0].split("|")]
    # Find relevant column indices
    sym_idx  = next((i for i, h in enumerate(header) if h in ("symbol", "act symbol")), 0)
    test_idx = next((i for i, h in enumerate(header) if "test" in h), None)
    etf_idx  = next((i for i, h in enumerate(header) if h == "etf"), None)
    exch_idx = next((i for i, h in enumerate(header) if h == "exchange"), None)

    for line in lines[1:]:
        if line.startswith("File Creation Time"):
            continue
        parts = line.split("|")
        if len(parts) <= sym_idx:
            continue
        sym = parts[sym_idx].strip()
        if not sym or len(sym) > 5:          # skip blanks and long symbols (warrants etc.)
            continue
        if any(c in sym for c in ("^", "/", "$", ".")):  # skip special securities
            continue
        if test_idx and test_idx < len(parts) and parts[test_idx].strip().upper() == "Y":
            continue
        if etf_idx and etf_idx < len(parts) and parts[etf_idx].strip().upper() == "Y":
            continue
        if exchange_filter and exch_idx and exch_idx < len(parts):
            if parts[exch_idx].strip().upper() != exchange_filter.upper():
                continue
        symbols.append(sym)
    return symbols


def _fetch_nasdaq_file(filename: str) -> str:
    """Try HTTPS first, fall back to HTTP for NASDAQ FTP files."""
    headers = {"User-Agent": "Mozilla/5.0 StockScanner"}
    for base in (NASDAQ_FTP_BASE, NASDAQ_FTP_BASE_HTTP):
        try:
            resp = requests.get(f"{base}/{filename}", timeout=20, headers=headers)
            resp.raise_for_status()
            if resp.text:
                return resp.text
        except Exception:
            continue
    return ""


def fetch_all_us_symbols_from_sec() -> tuple:
    """Fetch NASDAQ + NYSE symbols from SEC EDGAR company_tickers_exchange.json."""
    url = "https://www.sec.gov/files/company_tickers_exchange.json"
    headers = {"User-Agent": "StockScanner umashankartd1991@gmail.com"}
    try:
        resp = requests.get(url, timeout=30, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("data", [])
        # fields: [cik, name, ticker, exchange]
        nasdaq, nyse = [], []
        for row in rows:
            ticker   = str(row[2]).strip().upper() if len(row) > 2 else ""
            exchange = str(row[3]).strip().lower() if len(row) > 3 else ""
            if not ticker or any(c in ticker for c in [" ", ".", "^"]):
                continue
            if "nasdaq" in exchange:
                nasdaq.append(ticker)
            elif "nyse" in exchange or "amex" in exchange or "arca" in exchange:
                nyse.append(ticker)
        return nasdaq, nyse
    except Exception as e:
        print(f"  ⚠️  SEC EDGAR fetch failed: {e}")
        return [], []


_ncds_symbol_cache: dict = {}   # populated once; shared by both fetch functions


def fetch_nasdaq_symbols() -> list[str]:
    """
    Fetch NASDAQ-listed equity symbols.
    Priority: Nasdaq NCDS SDK → NASDAQ FTP → SEC EDGAR.
    """
    if _NCDS_AVAILABLE and _load_ncds_creds() and not _ncds_symbol_cache:
        print("  Trying Nasdaq Cloud Data Service SDK …")
        nasdaq, nyse = fetch_symbols_from_ncds()
        _ncds_symbol_cache["nasdaq"] = nasdaq
        _ncds_symbol_cache["nyse"]   = nyse
        if nasdaq:
            print(f"  NCDS: {len(nasdaq)} NASDAQ symbols")
            return nasdaq
        print("  ⚠️  NCDS returned no NASDAQ symbols — trying FTP")

    text = _fetch_nasdaq_file("nasdaqlisted.txt")
    if text:
        syms = _parse_nasdaq_file(text)
        if syms:
            return syms
    print("  ⚠️  NASDAQ FTP unavailable — using SEC EDGAR fallback")
    nasdaq, _ = fetch_all_us_symbols_from_sec()
    return nasdaq


def fetch_nyse_symbols() -> list[str]:
    """
    Fetch NYSE/AMEX symbols.
    Priority: Nasdaq NCDS SDK (cached) → NASDAQ FTP → SEC EDGAR.
    """
    if _ncds_symbol_cache.get("nyse"):
        return _ncds_symbol_cache["nyse"]

    text = _fetch_nasdaq_file("otherlisted.txt")
    if text:
        syms = _parse_nasdaq_file(text)
        if syms:
            return syms
    print("  ⚠️  NYSE FTP unavailable — using SEC EDGAR fallback")
    _, nyse = fetch_all_us_symbols_from_sec()
    return nyse


# ── Bulk OHLC download ─────────────────────────────────────────────────────────

def bulk_download_ohlc(tickers: list[str], period: str = "3mo") -> dict[str, pd.DataFrame]:
    """
    Download OHLC in batches via yfinance.download().
    Returns dict: ticker → DataFrame (columns High/Low/Close).
    """
    result: dict[str, pd.DataFrame] = {}
    batches = [tickers[i:i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]
    print(f"  Downloading OHLC for {len(tickers)} tickers in {len(batches)} batches …")

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

            if isinstance(raw.columns, pd.MultiIndex):
                for tkr in batch:
                    try:
                        df = raw.xs(tkr, axis=1, level=1).dropna(how="all")
                        if not df.empty and len(df) >= DARVAS_CONFIRM + 5:
                            result[tkr] = df
                    except KeyError:
                        pass
            else:
                tkr = batch[0]
                if not raw.empty:
                    result[tkr] = raw

            print(f"OK ({sum(1 for t in batch if t in result)} usable)")
        except Exception as e:
            print(f"ERROR — {e}")

        if idx < len(batches):
            time.sleep(SLEEP_BETWEEN)

    return result


# ── Darvas Box ─────────────────────────────────────────────────────────────────

def compute_darvas_box(df: pd.DataFrame, confirm: int = DARVAS_CONFIRM) -> dict:
    """Darvas Box detection. Current bar excluded from box formation."""
    def find_col(df, candidates):
        for c in candidates:
            m = next((col for col in df.columns if c.upper() in col.upper()), None)
            if m:
                return m
        return None

    h_col = find_col(df, ["High"])
    l_col = find_col(df, ["Low"])
    c_col = find_col(df, ["Close"])

    if not all([h_col, l_col, c_col]) or len(df) < confirm + 5:
        return {"signal": "INSUFFICIENT_DATA", "box_top": None, "box_bottom": None}

    all_highs  = pd.to_numeric(df[h_col], errors="coerce").fillna(0).tolist()
    all_lows   = pd.to_numeric(df[l_col], errors="coerce").fillna(0).tolist()
    all_closes = pd.to_numeric(df[c_col], errors="coerce").fillna(0).tolist()

    current = all_closes[-1]
    highs   = all_highs[:-1]
    lows    = all_lows[:-1]
    n       = len(highs)

    box_top_idx, box_top = None, None
    for i in range(n - confirm - 1, -1, -1):
        candidate = highs[i]
        if candidate == 0:
            continue
        window = highs[i + 1: i + 1 + confirm]
        if len(window) == confirm and all(h < candidate for h in window):
            box_top_idx, box_top = i, candidate
            break

    if box_top is None:
        return {"signal": "NO_BOX", "box_top": None, "box_bottom": None,
                "current_price": round(current, 2)}

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
    upside_to_top = ((box_top - current) / current * 100) if current else 0
    pos_in_box    = ((current - box_bottom) / box_range * 100) if box_range else 0

    return {
        "signal":              signal,
        "box_top":             round(box_top,    2),
        "box_bottom":          round(box_bottom, 2),
        "current_price":       round(current,    2),
        "box_range":           round(box_range,  2),
        "upside_to_top_pct":   round(upside_to_top, 2),
        "position_in_box_pct": round(pos_in_box,    1),
        "data_points":         len(all_closes),
    }


# ── Fundamental scan ───────────────────────────────────────────────────────────

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


def fundamental_scan(symbol: str) -> dict:
    """
    Piotroski F-Score + US Coffee Can in a single yfinance Ticker call.
    Coffee Can is skipped when F-Score < 7. Market cap from fast_info only.
    """
    try:
        ticker = yf.Ticker(symbol)
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
    f4 = 1 if (ocf0 and a0 and roa0 and (ocf0/a0) > roa0) else 0

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

    # ── US Coffee Can — skip when Piotroski < 7 ───────────────────────────────
    if f_score < 7:
        return {"symbol": symbol, "f_score": f_score, "f_strong": False,
                "qualifies": False, "cc_score": "—", "name": "", "sector": ""}

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

    ni_s = series(inc, "Net Income")
    eq_s = series(bal, "Stockholders Equity", "Total Stockholder Equity",
                  "Total Equity Gross Minority Interest")
    roe_l = [ni_s[i] / eq_s[i] * 100 for i in range(min(len(ni_s), len(eq_s))) if eq_s[i] > 0]
    c["C2"] = 1 if (roe_l and sum(roe_l) / len(roe_l) > 15) else 0

    ltd_s = series(bal, "Long Term Debt")
    c["C3"] = (1 if (ltd_s[0] / abs(eq_s[0])) < 1 else 0) if (ltd_s and eq_s and eq_s[0] != 0) else 0

    try:
        mcap = ticker.fast_info.market_cap or 0
    except Exception:
        mcap = 0
    c["C4"] = 1 if mcap >= 1e9 else 0   # ≥ $1B

    c["C5"] = 1 if (ni_s and all(n > 0 for n in ni_s)) else 0

    fcf_s = series(cf, "Free Cash Flow")
    if fcf_s:
        c["C6"] = 1 if fcf_s[0] > 0 else 0
    else:
        ocf_s   = series(cf, "Operating Cash Flow", "Total Cash From Operating Activities")
        capex_s = series(cf, "Capital Expenditure", "Capital Expenditures")
        c["C6"] = 1 if (ocf_s and capex_s and (ocf_s[0] - abs(capex_s[0])) > 0) else 0

    cc_total  = sum(c.values())
    qualifies = cc_total == len(c)

    name = sector = ""
    try:
        fi = ticker.fast_info
        name = getattr(fi, "long_name", None) or getattr(fi, "short_name", "")
    except Exception:
        pass

    return {
        "symbol":   symbol,
        "f_score":  f_score,
        "f_strong": True,
        "qualifies": qualifies,
        "cc_score": f"{cc_total}/{len(c)}",
        "name":     name,
        "sector":   sector,
    }


# ── Excel export ───────────────────────────────────────────────────────────────

def save_excel(all_rows, darvas_rows, fund_rows, triple_rows, tag="us"):
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

        write_sheet(all_rows,    "All_Stocks",    sort_col="Change%")
        write_sheet(darvas_rows, "Darvas_Signals", sort_col="Upside_to_Top%")
        write_sheet(fund_rows,   "Fundamentals",   sort_col="Piotroski_Score")
        write_sheet(triple_rows, "Triple_Hits",    sort_col="Piotroski_Score")

    print(f"\n  📊  Excel saved → {path}")
    return path


# ── Main ───────────────────────────────────────────────────────────────────────

def main(nasdaq_only: bool = False, top: int = 0, run_scans: bool = True,
         workers: int = MAX_WORKERS, min_price: float = 1.0):

    print(f"\n{'#'*60}")
    print(f"  FULL US MARKET SCAN")
    print(f"  Started: {datetime.now().strftime('%d %b %Y  %H:%M:%S')}")
    print(f"{'#'*60}\n")

    # ── Stage 1: symbol lists (cached for 24 h) ──────────────────────────────
    print("Stage 1 — Fetching symbol lists …")
    nasdaq_syms, nyse_syms = _load_symbol_cache()
    if nasdaq_syms is None:
        nasdaq_syms = fetch_nasdaq_symbols()
        print(f"  NASDAQ:       {len(nasdaq_syms)} symbols")
        nyse_syms = []
        if not nasdaq_only:
            nyse_syms = fetch_nyse_symbols()
            print(f"  NYSE/Other:   {len(nyse_syms)} symbols")
        _save_symbol_cache(nasdaq_syms, nyse_syms)
    else:
        print(f"  Symbol cache hit: {len(nasdaq_syms)} NASDAQ + {len(nyse_syms)} NYSE/other")
        if nasdaq_only:
            nyse_syms = []

    seen = set()
    all_symbols = []
    for s in nasdaq_syms + nyse_syms:
        if s not in seen:
            seen.add(s)
            all_symbols.append(s)

    if top:
        all_symbols = all_symbols[:top]
        print(f"  (limited to first {top} symbols)")

    print(f"  Total unique symbols: {len(all_symbols)}\n")

    # ── Stage 2: bulk OHLC download ──────────────────────────────────────────
    print("Stage 2 — Bulk OHLC download (3-month window) …")
    ohlc_data = bulk_download_ohlc(all_symbols, period="3mo")
    print(f"  → {len(ohlc_data)} tickers with usable data\n")

    # ── Stage 3: Darvas Box + price filter ───────────────────────────────────
    print("Stage 3 — Darvas Box screen …")
    all_rows, darvas_rows, breakout_symbols = [], [], []

    for ticker, df in ohlc_data.items():
        closes = pd.to_numeric(df["Close"], errors="coerce").dropna()
        if closes.empty:
            continue
        ltp  = round(float(closes.iloc[-1]), 2)
        if ltp < min_price:
            continue
        prev    = round(float(closes.iloc[-2]), 2) if len(closes) >= 2 else None
        chg_pct = round((ltp - prev) / prev * 100, 2) if (prev and prev) else None

        result  = compute_darvas_box(df)
        base_row = {
            "Symbol":           ticker,
            "LTP":              ltp,
            "Prev_Close":       prev,
            "Change%":          chg_pct,
            "Darvas_Signal":    result.get("signal"),
            "Box_Top":          result.get("box_top"),
            "Box_Bottom":       result.get("box_bottom"),
            "Upside_to_Top%":   result.get("upside_to_top_pct"),
            "Position_in_Box%": result.get("position_in_box_pct"),
            "Data_Points":      result.get("data_points"),
        }
        all_rows.append(base_row)

        if result.get("signal") in ("BREAKOUT_BUY", "BREAKDOWN_SELL"):
            darvas_rows.append(base_row.copy())
        if result.get("signal") == "BREAKOUT_BUY":
            breakout_symbols.append(ticker)

    breakdowns = sum(1 for r in darvas_rows if r["Darvas_Signal"] == "BREAKDOWN_SELL")
    print(f"  Breakout BUY:  {len(breakout_symbols)}")
    print(f"  Breakdown SELL:{breakdowns}")
    print(f"  In Box:        {len(all_rows) - len(darvas_rows)}")

    # ── Stage 4: Fundamental scans on breakout candidates ────────────────────
    fund_rows, triple_rows = [], []

    if run_scans and breakout_symbols:
        if len(breakout_symbols) > MAX_FUND_CANDIDATES:
            darvas_idx = {r["Symbol"]: r.get("Upside_to_Top%") for r in darvas_rows}
            breakout_symbols = sorted(
                breakout_symbols,
                key=lambda s: abs(darvas_idx.get(s) or 999)
            )[:MAX_FUND_CANDIDATES]
            print(f"  (pre-filtered to {MAX_FUND_CANDIDATES} freshest breakouts)")

        print(f"\nStage 4 — Fundamental scans on {len(breakout_symbols)} breakout candidates "
              f"({workers} workers) …")
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(fundamental_scan, sym): sym for sym in breakout_symbols}
            for future in as_completed(futures):
                sym  = futures[future]
                done += 1
                try:
                    res = future.result()
                    dr  = next((r for r in darvas_rows if r["Symbol"] == sym), {})
                    fund_row = {
                        "Symbol":          sym,
                        "Name":            res.get("name", ""),
                        "Sector":          res.get("sector", ""),
                        "LTP":             dr.get("LTP"),
                        "Change%":         dr.get("Change%"),
                        "Darvas_Signal":   dr.get("Darvas_Signal"),
                        "Upside_to_Top%":  dr.get("Upside_to_Top%"),
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
        print("\nStage 4 — Skipped (--no-scans or no breakouts)")

    # ── Stage 5: Save results ─────────────────────────────────────────────────
    print("\nStage 5 — Saving results …")
    path = save_excel(all_rows, darvas_rows, fund_rows, triple_rows, tag="us")

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
            print(f"    {r['Symbol']:<12} {r.get('Name',''):<35} "
                  f"F={r['Piotroski_Score']}/9  CC={r['CC_Score']}  "
                  f"LTP=${r['LTP']}  +{r['Upside_to_Top%']}% to box top")
    print(f"{'='*60}\n")
    return {"triple_hits": triple_rows, "breakouts": len(breakout_symbols),
            "total_scanned": len(all_rows), "excel_path": str(path)}


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Full NASDAQ+NYSE universe Darvas+Piotroski+CoffeeCan scanner."
    )
    parser.add_argument("--nasdaq-only", action="store_true", default=False,
                        help="Scan NASDAQ-listed stocks only (skip NYSE/AMEX)")
    parser.add_argument("--top",         type=int,   default=0,
                        help="Limit scan to first N symbols (0 = all)")
    parser.add_argument("--no-scans",    action="store_true", default=False,
                        help="Skip Piotroski + Coffee Can (Darvas only, much faster)")
    parser.add_argument("--workers",     type=int,   default=MAX_WORKERS,
                        help=f"Parallel threads for fundamental scans (default {MAX_WORKERS})")
    parser.add_argument("--min-price",   type=float, default=1.0,
                        help="Exclude stocks priced below this (default $1 — filters penny stocks)")
    args = parser.parse_args()
    main(nasdaq_only=args.nasdaq_only, top=args.top,
         run_scans=not args.no_scans, workers=args.workers,
         min_price=args.min_price)
