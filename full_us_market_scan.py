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

# Persistent cache (avoids re-downloading on subsequent runs)
try:
    from market_data_cache import MarketCache as _MarketCache
    _US_CACHE = _MarketCache(verbose=True)
    _CACHE_OK  = True
except ImportError:
    _US_CACHE = None
    _CACHE_OK  = False

# ML signal engine (AlQahtani et al. 2025 — Ridge regression)
try:
    from ml_signal_engine import MLSignalEngine as _MLEngine
    _ML_ENGINE = _MLEngine(model_type="ridge")
    _ML_OK     = True
except ImportError:
    _ML_ENGINE = None
    _ML_OK     = False

# NSE data fetcher for live context
try:
    from nse_data_fetcher import NSEDataFetcher as _NSEFetcher
    _US_FETCHER = _NSEFetcher()
    _NSE_OK     = True
except ImportError:
    _US_FETCHER = None
    _NSE_OK     = False

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


# ── Bulk OHLC download (cache-aware) ──────────────────────────────────────────

def bulk_download_ohlc(tickers: list[str], period: str = "1y") -> dict[str, pd.DataFrame]:
    """
    Download OHLC for US stocks with 3-tier caching (memory → disk → network).

    US tickers have no suffix (AAPL, MSFT etc.) — stored as bare ticker in cache.
    Cache stores 5 years as recommended by AlQahtani et al. (2025).

    Tier 1 — Memory cache: 0.3 ms/stock (same session, already fetched)
    Tier 2 — Parquet cache: ~94 ms/stock (previously downloaded, on disk)
    Tier 3 — yfinance network: ~3s per batch of 100 (cold download)

    For 7,000+ US stocks:
      First run:       ~15-20 min (network)
      Subsequent runs: ~4 min (disk Parquet) | <1s (memory within session)
    """
    if _CACHE_OK:
        return _US_CACHE.get_ohlc_bulk(tickers, force=False)

    # Fallback: direct yfinance (no cache)
    result: dict[str, pd.DataFrame] = {}
    batches = [tickers[i:i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]
    print(f"  Downloading OHLC for {len(tickers)} tickers in {len(batches)} batches …")
    for idx, batch in enumerate(batches, 1):
        print(f"    Batch {idx}/{len(batches)} ({len(batch)}) …", end=" ", flush=True)
        for attempt in range(3):
            try:
                raw = yf.download(batch, period=period, auto_adjust=True,
                                  threads=True, progress=False)
                if raw.empty:
                    print("empty"); break
                if isinstance(raw.columns, pd.MultiIndex):
                    for tkr in batch:
                        try:
                            df = raw.xs(tkr, axis=1, level=1).dropna(how="all")
                            if not df.empty and len(df) >= DARVAS_CONFIRM + 5:
                                result[tkr] = df
                        except KeyError:
                            pass
                else:
                    if not raw.empty:
                        result[batch[0]] = raw
                print(f"OK ({sum(1 for t in batch if t in result)} usable)")
                break
            except Exception as e:
                if "Rate" in str(e) or "429" in str(e):
                    time.sleep(30 * (attempt + 1))
                else:
                    print(f"ERROR — {e}"); break
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


# ── Golden Crossover ──────────────────────────────────────────────────────────

def compute_golden_crossover(df: pd.DataFrame) -> dict:
    """50 DMA crossed above 200 DMA today. Uses bulk OHLC — zero extra API calls."""
    if df is None or df.empty:
        return {"gc_signal": False, "dma50_above_200": False, "dma50": None, "dma200": None}
    closes = pd.to_numeric(df["Close"], errors="coerce").dropna()
    if len(closes) < 201:
        return {"gc_signal": False, "dma50_above_200": False, "dma50": None, "dma200": None}
    dma50  = closes.rolling(50).mean()
    dma200 = closes.rolling(200).mean()
    d50_t, d200_t = float(dma50.iloc[-1]), float(dma200.iloc[-1])
    d50_p, d200_p = float(dma50.iloc[-2]), float(dma200.iloc[-2])
    gap_pct = round((d50_t - d200_t) / d200_t * 100, 2) if d200_t else 0
    return {
        "gc_signal":       (d50_p < d200_p) and (d50_t > d200_t),
        "dma50_above_200": d50_t > d200_t,
        "dma50":           round(d50_t, 2),
        "dma200":          round(d200_t, 2),
        "dma_gap_%":       gap_pct,
    }


# ── Fundamental scan ───────────────────────────────────────────────────────────

# Shared helpers (see stock_utils.py) — aliased to keep existing call sites.
from stock_utils import first_df as _first_df, row as _row


def fundamental_scan(symbol: str) -> dict:
    """
    Run ALL 5 fundamental screeners for one US symbol in a single Ticker() call:
      1. Piotroski F-Score  — 9-point accounting quality (≥7 = strong)
      2. US Coffee Can      — Rev CAGR>10%, ROE>15%, D/E<1, MCap≥$1B, no loss, FCF>0
      3. Magic Formula      — ROIC>25%, Earnings Yield>15%, MCap>$50M  (US-adapted)
      4. Bull Cartel        — YoY quarterly sales growth>15%, profit growth>20%
    """
    try:
        ticker = yf.Ticker(symbol)
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
        name   = info.get("shortName", "") or info.get("longName", "")
        sector = info.get("sector", "")
    except Exception as e:
        return {"symbol": symbol, "name": "", "sector": "", "error": str(e),
                "f_score": None, "f_strong": False,
                "qualifies_cc": False, "cc_score": "N/A",
                "qualifies_mf": False, "qualifies_bc": False}

    out = {"symbol": symbol, "name": name, "sector": sector, "error": ""}

    # ─────────────────────────────────────────────────────────────────────────
    # SCREENER 1 + 2: Piotroski + US Coffee Can
    # ─────────────────────────────────────────────────────────────────────────
    if inc is not None:
        ni0 = _row(inc, "Net Income", col=0);  a0 = _row(bal, "Total Assets", col=0)
        ni1 = _row(inc, "Net Income", col=1);  a1 = _row(bal, "Total Assets", col=1)
        roa0 = (ni0/a0) if (ni0 and a0) else None
        roa1 = (ni1/a1) if (ni1 and a1) else None
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

        def series(df, *rows):
            for name_ in rows:
                if df is not None and name_ in df.index:
                    return [float(v) for v in df.loc[name_].dropna() if pd.notna(v)]
            return []

        # US Coffee Can (6 criteria)
        revs = series(inc, "Total Revenue")
        cagr = ((revs[0]/revs[-1])**(1/(len(revs)-1))-1)*100 if len(revs)>=2 and revs[-1]>0 else None
        ni_s = series(inc, "Net Income")
        eq_s = series(bal, "Stockholders Equity", "Total Stockholder Equity",
                      "Total Equity Gross Minority Interest")
        roe_l = [ni_s[i]/eq_s[i]*100 for i in range(min(len(ni_s),len(eq_s))) if eq_s[i]>0]
        avg_roe = sum(roe_l)/len(roe_l) if roe_l else None
        de_raw = info.get("debtToEquity")
        if de_raw is not None:
            de = de_raw/100 if de_raw > 10 else de_raw
        else:
            ltd_s = series(bal, "Long Term Debt")
            de = (ltd_s[0]/abs(eq_s[0])) if (ltd_s and eq_s and eq_s[0]!=0) else None
        fcf_s   = series(cf, "Free Cash Flow")
        ocf_s   = series(cf, "Operating Cash Flow", "Total Cash From Operating Activities")
        capex_s = series(cf, "Capital Expenditure", "Capital Expenditures")
        fcf_pos = (fcf_s[0]>0) if fcf_s else ((ocf_s[0]-abs(capex_s[0]))>0 if (ocf_s and capex_s) else False)
        cc_bits = [
            1 if (cagr    and cagr    > 10)  else 0,
            1 if (avg_roe and avg_roe > 15)  else 0,
            1 if (de is not None and de < 1) else 0,
            1 if mcap >= 1e9                 else 0,   # ≥ $1B
            1 if (ni_s and all(n>0 for n in ni_s)) else 0,
            1 if fcf_pos                     else 0,
        ]
        out["qualifies_cc"] = sum(cc_bits) == 6
        out["cc_score"]     = f"{sum(cc_bits)}/6"
        out["Revenue_CAGR_%"] = round(cagr,    2) if cagr    is not None else None
        out["ROE_avg_%"]      = round(avg_roe,  2) if avg_roe is not None else None

        # Magic Formula — US adapted (MCap threshold: >$50M instead of ₹15Cr)
        ebit     = _row(inc, "EBIT", "Operating Income", "Ebit")
        cap_emp  = (a0 - (cl0 or 0)) if a0 else None
        tot_dbt  = info.get("totalDebt", 0) or 0
        cash_val = info.get("totalCash", 0) or 0
        ev       = (mcap + tot_dbt - cash_val) if mcap else None
        roic     = (ebit/cap_emp*100) if (ebit and cap_emp and cap_emp>0) else None
        ey       = (ebit/ev*100)       if (ebit and ev      and ev>0)     else None
        bv       = info.get("bookValue")
        out["qualifies_mf"]     = bool(roic and roic>25 and ey and ey>15
                                       and bv and bv>0 and mcap>50e6)
        out["ROIC_%"]           = round(roic, 2) if roic is not None else None
        out["Earnings_Yield_%"] = round(ey,   2) if ey   is not None else None
    else:
        out.update({"f_score": None, "f_strong": False,
                    "qualifies_cc": False, "cc_score": "N/A",
                    "qualifies_mf": False, "ROIC_%": None, "Earnings_Yield_%": None,
                    "Revenue_CAGR_%": None, "ROE_avg_%": None})

    # ─────────────────────────────────────────────────────────────────────────
    # SCREENER 4: Bull Cartel (quarterly)
    # ─────────────────────────────────────────────────────────────────────────
    if inc_q is not None and len(inc_q.columns) >= 5:
        rev_q0 = _row(inc_q, "Total Revenue", col=0)
        rev_q4 = _row(inc_q, "Total Revenue", col=4)
        ni_q0  = _row(inc_q, "Net Income",    col=0)
        ni_q4  = _row(inc_q, "Net Income",    col=4)
        sales_g  = ((rev_q0-rev_q4)/abs(rev_q4)*100) if (rev_q0 and rev_q4 and rev_q4!=0) else None
        profit_g = ((ni_q0-ni_q4)/abs(ni_q4)*100)    if (ni_q0  and ni_q4  and ni_q4!=0)  else None
        ni_usd_m = ni_q0/1e6 if ni_q0 else None
        out["qualifies_bc"]        = bool(sales_g and sales_g>15
                                          and profit_g and profit_g>20
                                          and ni_usd_m and ni_usd_m>1)   # >$1M net profit
        out["Sales_Growth_YoY_%"]  = round(sales_g,  2) if sales_g  is not None else None
        out["Profit_Growth_YoY_%"] = round(profit_g, 2) if profit_g is not None else None
        out["Net_Profit_$M"]       = round(ni_usd_m, 2) if ni_usd_m is not None else None
    else:
        out.update({"qualifies_bc": False, "Sales_Growth_YoY_%": None,
                    "Profit_Growth_YoY_%": None, "Net_Profit_$M": None})

    return out


# ── Excel export ───────────────────────────────────────────────────────────────

def save_excel(all_rows, darvas_rows, fund_rows, triple_rows,
               six_screen_rows=None, tag="us", ml_signal_map=None):
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

        write_sheet(all_rows,    "All_Stocks",       sort_col="Change%")
        write_sheet(darvas_rows, "Darvas_Signals",   sort_col="Upside_to_Top%")
        write_sheet(fund_rows,   "All_Fundamentals", sort_col="Piotroski_Score")
        write_sheet([r for r in fund_rows if r.get("Piotroski_Strong") == "YES"],
                    "Piotroski_Strong", sort_col="Piotroski_Score")
        write_sheet([r for r in fund_rows if r.get("CoffeeCan") == "PASS"],
                    "Coffee_Can",       sort_col="Revenue_CAGR_%")
        write_sheet([r for r in fund_rows if r.get("MagicFormula") == "PASS"],
                    "Magic_Formula",    sort_col="ROIC_%")
        write_sheet([r for r in fund_rows if r.get("BullCartel") == "PASS"],
                    "Bull_Cartel",      sort_col="Profit_Growth_YoY_%")
        write_sheet([r for r in all_rows if r.get("GC_Signal") == "GOLDEN_CROSS"],
                    "Golden_Crossover", sort_col="DMA_Gap%")
        write_sheet(triple_rows, "Triple_Hits",      sort_col="Piotroski_Score")
        write_sheet(six_screen_rows or [], "Multi_Screen_Hits", sort_col="Screens_Passed")
        # ML signal sheet — all stocks with ML_Direction, sorted by predicted return
        if ml_signal_map:
            ml_rows = [{"Symbol": s, **v} for s, v in ml_signal_map.items()]
            write_sheet(
                [r for r in ml_rows if r.get("ML_Direction")=="BULLISH"],
                "ML_Bullish",  sort_col="ML_Pred_Ret%"
            )
            write_sheet(
                [r for r in ml_rows if r.get("ML_Direction")=="BEARISH"],
                "ML_Bearish",  sort_col="ML_Pred_Ret%"
            )

    print(f"\n  📊  Excel saved → {path}")
    return path


# ── Main ───────────────────────────────────────────────────────────────────────

def main(nasdaq_only: bool = False, top: int = 0, run_scans: bool = True,
         workers: int = MAX_WORKERS, min_price: float = 1.0):

    print(f"\n{'#'*60}")
    print(f"  FULL US MARKET SCAN — NASDAQ + NYSE")
    print(f"  Started: {datetime.now().strftime('%d %b %Y  %H:%M:%S')}")
    print(f"  Cache: {'✅ active (Parquet)' if _CACHE_OK else '⚠️  disabled'}")
    print(f"  ML signal: {'✅ Ridge regression (AlQahtani et al. 2025)' if _ML_OK else '⚠️  disabled'}")
    print(f"{'#'*60}\n")

    # ── Live market context (S&P 500 regime) ─────────────────────────────────
    if _NSE_OK:
        try:
            import yfinance as yf
            sp500 = yf.download("^GSPC", period="1y", auto_adjust=True, progress=False)
            if isinstance(sp500.columns, pd.MultiIndex):
                sp500 = sp500.xs("^GSPC", axis=1, level=1)
            if not sp500.empty:
                sp_last  = float(sp500["Close"].iloc[-1])
                sp_dma200= float(sp500["Close"].rolling(200).mean().iloc[-1])
                sp_regime= "BULL" if sp_last > sp_dma200 else "BEAR"
                sp_pct   = (sp_last - sp_dma200) / sp_dma200 * 100
                print(f"  S&P 500: {sp_last:,.0f}  |  200 DMA: {sp_dma200:,.0f}  "
                      f"({sp_pct:+.2f}%)  |  Regime: {sp_regime}")
                # VIX from nsepython not applicable for US — use yfinance VIX
                try:
                    vix = yf.download("^VIX", period="5d", progress=False)
                    if not vix.empty:
                        vix_val = float(vix["Close"].iloc[-1])
                        vix_lvl = ("NORMAL" if vix_val<18 else "ELEVATED" if vix_val<25 else "PANIC")
                        print(f"  CBOE VIX: {vix_val:.1f}  [{vix_lvl}]")
                except Exception:
                    pass
        except Exception:
            pass
    print()

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
    print("Stage 2 — Bulk OHLC download (1-year window; needed for Golden Crossover 200 DMA) …")
    ohlc_data = bulk_download_ohlc(all_symbols, period="1y")
    print(f"  → {len(ohlc_data)} tickers with usable data\n")

    # Stage 3: Darvas Box + Golden Crossover on every ticker (no extra API calls)
    print("Stage 3 — Darvas Box + Golden Crossover screen (all stocks) …")
    all_rows, darvas_rows = [], []
    ohlc_row_map: dict[str, dict] = {}

    for ticker, df in ohlc_data.items():
        closes = pd.to_numeric(df["Close"], errors="coerce").dropna()
        if closes.empty:
            continue
        ltp = round(float(closes.iloc[-1]), 2)
        if ltp < min_price:
            continue
        prev    = round(float(closes.iloc[-2]), 2) if len(closes) >= 2 else None
        chg_pct = round((ltp - prev) / prev * 100, 2) if (prev and prev) else None

        darvas = compute_darvas_box(df)
        gc     = compute_golden_crossover(df)
        base_row = {
            "Symbol":           ticker,
            "LTP":              ltp,
            "Prev_Close":       prev,
            "Change%":          chg_pct,
            "Darvas_Signal":    darvas.get("signal"),
            "Box_Top":          darvas.get("box_top"),
            "Box_Bottom":       darvas.get("box_bottom"),
            "Upside_to_Top%":   darvas.get("upside_to_top_pct"),
            "Position_in_Box%": darvas.get("position_in_box_pct"),
            "Data_Points":      darvas.get("data_points"),
            "GC_Signal":        "GOLDEN_CROSS" if gc.get("gc_signal") else (
                                    "ABOVE_200DMA" if gc.get("dma50_above_200") else "BELOW_200DMA"),
            "DMA50":            gc.get("dma50"),
            "DMA200":           gc.get("dma200"),
            "DMA_Gap%":         gc.get("dma_gap_%"),
        }
        all_rows.append(base_row)
        ohlc_row_map[ticker] = base_row

        if darvas.get("signal") in ("BREAKOUT_BUY", "BREAKDOWN_SELL"):
            darvas_rows.append(base_row.copy())

    breakout_count  = sum(1 for r in all_rows if r["Darvas_Signal"] == "BREAKOUT_BUY")
    breakdown_count = sum(1 for r in all_rows if r["Darvas_Signal"] == "BREAKDOWN_SELL")
    gc_count        = sum(1 for r in all_rows if r["GC_Signal"] == "GOLDEN_CROSS")
    above_200       = sum(1 for r in all_rows if r["GC_Signal"] in ("GOLDEN_CROSS","ABOVE_200DMA"))
    print(f"  Darvas Breakout BUY:    {breakout_count}")
    print(f"  Darvas Breakdown SELL:  {breakdown_count}")
    print(f"  Golden Cross (today):   {gc_count}")
    print(f"  DMA50 above DMA200:     {above_200}")

    # ── Stage 4: ALL 5 fundamental screeners on EVERY stock ──────────────────
    fund_rows, six_screen_rows = [], []
    sc_counts = {"piotroski": 0, "cc": 0, "mf": 0, "bc": 0}

    if run_scans:
        all_syms_for_fund = list(ohlc_row_map.keys())
        print(f"\nStage 4 — All 5 fundamental screeners on {len(all_syms_for_fund)} stocks "
              f"({workers} workers) …")
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(fundamental_scan, sym): sym for sym in all_syms_for_fund}
            for future in as_completed(futures):
                sym  = futures[future]
                done += 1
                try:
                    res  = future.result()
                    ohlc = ohlc_row_map.get(sym, {})
                    row  = {
                        "Symbol":              sym,
                        "Name":                res.get("name", ""),
                        "Sector":              res.get("sector", ""),
                        "LTP":                 ohlc.get("LTP"),
                        "Change%":             ohlc.get("Change%"),
                        "Darvas_Signal":       ohlc.get("Darvas_Signal"),
                        "Box_Top":             ohlc.get("Box_Top"),
                        "Box_Bottom":          ohlc.get("Box_Bottom"),
                        "Upside_to_Top%":      ohlc.get("Upside_to_Top%"),
                        "GC_Signal":           ohlc.get("GC_Signal"),
                        "DMA50":               ohlc.get("DMA50"),
                        "DMA200":              ohlc.get("DMA200"),
                        "Piotroski_Score":     res.get("f_score"),
                        "Piotroski_Strong":    "YES" if res.get("f_strong") else "NO",
                        "CoffeeCan":           "PASS" if res.get("qualifies_cc") else "FAIL",
                        "CC_Score":            res.get("cc_score"),
                        "Revenue_CAGR_%":      res.get("Revenue_CAGR_%"),
                        "ROE_avg_%":           res.get("ROE_avg_%"),
                        "MagicFormula":        "PASS" if res.get("qualifies_mf") else "FAIL",
                        "ROIC_%":              res.get("ROIC_%"),
                        "Earnings_Yield_%":    res.get("Earnings_Yield_%"),
                        "BullCartel":          "PASS" if res.get("qualifies_bc") else "FAIL",
                        "Sales_Growth_YoY_%":  res.get("Sales_Growth_YoY_%"),
                        "Profit_Growth_YoY_%": res.get("Profit_Growth_YoY_%"),
                        "Net_Profit_$M":       res.get("Net_Profit_$M"),
                        "Error":               res.get("error", ""),
                    }
                    fund_rows.append(row)

                    if res.get("f_strong"):     sc_counts["piotroski"] += 1
                    if res.get("qualifies_cc"): sc_counts["cc"]        += 1
                    if res.get("qualifies_mf"): sc_counts["mf"]        += 1
                    if res.get("qualifies_bc"): sc_counts["bc"]        += 1

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

                    if done % 200 == 0 or done == len(all_syms_for_fund):
                        print(f"    {done}/{len(all_syms_for_fund)} done  "
                              f"(multi-screen hits: {len(six_screen_rows)})")
                except Exception as e:
                    print(f"    {sym}: error — {e}")
    else:
        print("\nStage 4 — Skipped (--no-scans)")

    # Backward-compat triple_rows
    triple_rows = [r for r in fund_rows
                   if r.get("Darvas_Signal") == "BREAKOUT_BUY"
                   and r.get("Piotroski_Strong") == "YES"
                   and r.get("CoffeeCan") == "PASS"]

    # ── Stage 4b: ML signal on every stock with OHLC (AlQahtani et al. 2025) ─
    ml_signal_map: dict = {}
    if _ML_OK and ohlc_data:
        print(f"\nStage 4b — ML signal (Ridge regression) on {len(ohlc_data)} stocks …")
        try:
            ml_df = _ML_ENGINE.predict_batch(ohlc_data, max_workers=workers)
            for _, row in ml_df.iterrows():
                ml_signal_map[row["symbol"]] = {
                    "ML_Direction":    row.get("direction", "NEUTRAL"),
                    "ML_Pred_Ret%":    row.get("predicted_ret%", 0),
                    "ML_Confidence":   row.get("confidence", 0),
                    "ML_TrainRMSE":    row.get("train_rmse"),
                }
        except Exception as e:
            print(f"  ML signal error: {e}")

        # Attach ML signals to all_rows and fund_rows
        for r in all_rows:
            ml = ml_signal_map.get(r.get("Symbol",""), {})
            r.update(ml)
        for r in fund_rows:
            ml = ml_signal_map.get(r.get("Symbol",""), {})
            r.update(ml)

        # Count signals
        n_bull = sum(1 for v in ml_signal_map.values() if v.get("ML_Direction")=="BULLISH")
        n_bear = sum(1 for v in ml_signal_map.values() if v.get("ML_Direction")=="BEARISH")
        print(f"  ML: BULLISH={n_bull} | BEARISH={n_bear} | "
              f"NEUTRAL={len(ml_signal_map)-n_bull-n_bear}")

        # High-conviction: fundamental screener signal + ML BULLISH
        for r in six_screen_rows:
            ml = ml_signal_map.get(r.get("Symbol",""), {})
            r.update(ml)
            r["ML_Confirmed"] = "YES" if ml.get("ML_Direction")=="BULLISH" else "NO"

    # ── Stage 5: Save results ─────────────────────────────────────────────────
    print("\nStage 5 — Saving results …")
    path = save_excel(all_rows, darvas_rows, fund_rows, triple_rows,
                      six_screen_rows, tag="us", ml_signal_map=ml_signal_map)

    print(f"\n{'='*70}")
    print(f"  SCAN COMPLETE — {datetime.now().strftime('%d %b %Y  %H:%M:%S')}")
    print(f"  Tickers scanned:          {len(all_rows)}")
    print(f"  ── OHLC Screeners ──────────────────────────────────────")
    print(f"  Darvas Breakouts:         {breakout_count}")
    print(f"  Darvas Breakdowns:        {breakdown_count}")
    print(f"  Golden Cross (today):     {gc_count}")
    print(f"  DMA50 above DMA200:       {above_200}")
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
            print(f"    {r['Symbol']:<10} {r.get('Name',''):<30} {r['Screens_Passed']}/6 "
                  f"F={r.get('Piotroski_Score') or '-'}/9  CC={r.get('CC_Score','-')}  "
                  f"MF={r.get('MagicFormula','-')}  LTP=${r.get('LTP','?')}")
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
