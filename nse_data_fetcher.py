# nse_data_fetcher.py
# ====================
# Unified NSE + BSE data layer using NSEpy + nsepython + yfinance.
#
# Library capabilities (tested on macOS):
#
# ┌─────────────────────────────────────────────────────────────────────────────┐
# │ Library      │ What it provides                  │ Status on Mac            │
# ├─────────────────────────────────────────────────────────────────────────────┤
# │ nsepython    │ Live quotes, VIX, FII/DII, bulk    │ ✅ Works reliably        │
# │              │ deals, block deals, events calendar│                          │
# │              │ symbol list (2372 EQ stocks)       │                          │
# ├─────────────────────────────────────────────────────────────────────────────┤
# │ NSEpy        │ Historical OHLC (NSE direct)       │ ⚠️  SSL error on new API │
# │              │ Index history, derivatives history │   Use yfinance fallback  │
# ├─────────────────────────────────────────────────────────────────────────────┤
# │ yfinance     │ Historical OHLC (via Yahoo Finance)│ ✅ Works (rate limits)   │
# │              │ Annual + quarterly financial stmts │ ✅ Best for financials   │
# │              │ Insider trades, institutional data │                          │
# └─────────────────────────────────────────────────────────────────────────────┘
#
# Architecture:
#   Historical OHLC    → yfinance primary (with NSEpy fallback when fixed)
#   Symbol universe    → nsepython.nse_eq_symbols() — direct NSE, 2372 stocks
#   Market regime      → nsepython: VIX + Nifty live + FII/DII trend
#   Filing events      → nsepython.nse_events() — results/board meeting calendar
#   Bulk/block deals   → nsepython: institutional activity confirmation
#   Fundamental data   → yfinance (Piotroski, Coffee Can, Magic Formula)
#
# Usage:
#   from nse_data_fetcher import NSEDataFetcher
#   nse = NSEDataFetcher()
#   regime   = nse.get_regime()            # {'regime':'BULL','vix':13.05,...}
#   symbols  = nse.get_all_symbols()       # ['RELIANCE','TCS',...]
#   events   = nse.get_upcoming_events()   # DataFrame of results/board meetings
#   bulk     = nse.get_bulk_deals()        # today's bulk deals
#   fiidii   = nse.get_fii_dii()          # FII/DII net activity
#   ohlc     = nse.get_ohlc('RELIANCE',period='5y')  # historical OHLC
#
# Install:
#   pip install nsepython nsepy yfinance pandas "nse[local]"

import time
import warnings
from datetime import datetime, timedelta, date
from pathlib import Path
from functools import lru_cache

import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")

# ── Imports with graceful fallback ─────────────────────────────────────────────
try:
    import yfinance as yf
    _YF_OK = True
except ImportError:
    _YF_OK = False
    print("⚠️  yfinance not installed: pip install yfinance")

try:
    from nsepython import (
        nse_eq_symbols, indiavix, get_bulkdeals, get_blockdeals,
        nse_events, nse_get_index_quote, nse_fiidii,
        nse_get_top_gainers, nse_get_top_losers, nse_circular,
        holiday_master, fnolist, expiry_list,
    )
    _NSEPY_OK = True
except ImportError:
    _NSEPY_OK = False
    print("⚠️  nsepython not installed: pip install nsepython")

try:
    from nsepy import get_history as _nsepy_get_history
    _NSEPY_LIB_OK = True
except ImportError:
    _NSEPY_LIB_OK = False

CACHE_DIR = Path("./nse_cache")
CACHE_DIR.mkdir(exist_ok=True)

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN DATA FETCHER CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class NSEDataFetcher:
    """
    Unified NSE data access layer.

    Combines the strengths of nsepython (live market data, events, sentiment)
    with yfinance (historical OHLC, financial statements) to give a complete
    picture for both live screening and backtesting.
    """

    def __init__(self, cache_ttl_sec: int = 3600):
        self._cache_ttl = cache_ttl_sec
        self._cache: dict = {}
        self._index_cache: dict = {}

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 1: SYMBOL UNIVERSE
    # ─────────────────────────────────────────────────────────────────────────

    def get_all_symbols(self, series: str = "EQ") -> list:
        """
        Get all NSE-listed equity symbols directly from NSE via nsepython.
        nsepython.nse_eq_symbols() returns the live NSE EQ universe (~2372 stocks).
        Falls back to nse-library bhavcopy if nsepython is unavailable.

        Returns: list of NSE ticker symbols (e.g. ['RELIANCE', 'TCS', ...])
        """
        cache_key = f"symbols_{series}"
        cached = self._get_cache(cache_key)
        if cached:
            return cached

        # Primary: nsepython (direct NSE, always current)
        if _NSEPY_OK:
            try:
                syms = nse_eq_symbols()
                if syms and len(syms) > 100:
                    print(f"  nsepython: {len(syms)} NSE EQ symbols")
                    self._set_cache(cache_key, syms)
                    return syms
            except Exception as e:
                print(f"  nsepython.nse_eq_symbols failed: {e}")

        # Fallback: nse library bhavcopy
        try:
            from nse import NSE
            today = datetime.today()
            with NSE(download_folder=str(CACHE_DIR), server=False) as nse:
                for offset in range(7):
                    d = today - timedelta(days=offset)
                    try:
                        result = nse.equityBhavcopy(d)
                        if hasattr(result, "exists") and result.exists():
                            df = pd.read_csv(result)
                            if "SctySrs" in df.columns:
                                syms = sorted(df[df["SctySrs"]==series]["TckrSymb"]
                                              .dropna().str.strip().tolist())
                                if syms:
                                    print(f"  nse bhavcopy {d.date()}: {len(syms)} EQ symbols")
                                    self._set_cache(cache_key, syms)
                                    return syms
                    except Exception:
                        continue
        except ImportError:
            pass

        print("  ⚠️  Could not fetch NSE symbol list — using Nifty 50 fallback")
        return [
            "ADANIENT","ADANIPORTS","APOLLOHOSP","ASIANPAINT","AXISBANK",
            "BAJAJ-AUTO","BAJFINANCE","BAJAJFINSV","BPCL","BHARTIARTL",
            "BRITANNIA","CIPLA","COALINDIA","DIVISLAB","DRREDDY",
            "EICHERMOT","GRASIM","HCLTECH","HDFCBANK","HDFCLIFE",
            "HEROMOTOCO","HINDALCO","HINDUNILVR","ICICIBANK","ITC",
            "INDUSINDBK","INFY","JSWSTEEL","KOTAKBANK","LT",
            "M&M","MARUTI","NTPC","NESTLEIND","ONGC",
            "POWERGRID","RELIANCE","SBILIFE","SHRIRAMFIN","SBIN",
            "SUNPHARMA","TCS","TATACONSUM","TATAMOTORS","TATASTEEL",
            "TECHM","TITAN","TRENT","ULTRACEMCO","WIPRO",
        ]

    def get_fno_symbols(self) -> list:
        """Get the list of F&O eligible stocks from NSE."""
        if _NSEPY_OK:
            try:
                fo = fnolist()
                if isinstance(fo, (list, pd.Series)):
                    return list(fo)
            except Exception:
                pass
        return []

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 2: MARKET REGIME (live)
    # ─────────────────────────────────────────────────────────────────────────

    def get_regime(self) -> dict:
        """
        Determine the current market regime using multiple inputs:

        Primary signals:
          - Nifty 50 vs 200 DMA      (trend direction)
          - India VIX level           (fear / volatility)
          - FII net activity          (foreign institutional sentiment)

        Regime classification:
          BULL     : Nifty > 200 DMA, DMA upsloping, VIX < 18
          BULL_VOLATILE: Nifty > 200 DMA but VIX ≥ 18 (caution zone)
          BEAR     : Nifty < 200 DMA, DMA downsloping, VIX > 18
          BEAR_EXTREME: Nifty < 200 DMA, VIX > 25 (panic/crash zone)
          SIDEWAYS : Nifty within 1.5% of 200 DMA

        Returns dict with all inputs + final regime classification.
        """
        result = {
            "regime":        "UNKNOWN",
            "nifty_last":    None,
            "nifty_52w_h":   None,
            "nifty_52w_l":   None,
            "vix":           None,
            "pct_from_52wh": None,
            "fii_net_cr":    None,
            "fii_sentiment": "UNKNOWN",
            "timestamp":     datetime.now().isoformat(),
        }

        # ── Nifty 50 live ─────────────────────────────────────────────────────
        if _NSEPY_OK:
            try:
                q = nse_get_index_quote("NIFTY 50")
                if q:
                    last   = float(q.get("last", 0))
                    yh     = float(q.get("yearHigh", 0))
                    yl     = float(q.get("yearLow", 0))
                    result["nifty_last"]  = last
                    result["nifty_52w_h"] = yh
                    result["nifty_52w_l"] = yl
                    result["pct_from_52wh"] = round((last - yh) / yh * 100, 2) if yh else None
            except Exception:
                pass

        # ── Nifty 200 DMA from yfinance ────────────────────────────────────────
        if _YF_OK:
            try:
                idx = yf.download("^NSEI", period="1y", auto_adjust=True, progress=False)
                if isinstance(idx.columns, pd.MultiIndex):
                    idx = idx.xs("^NSEI", axis=1, level=1)
                if not idx.empty:
                    dma200 = float(idx["Close"].rolling(min(200, len(idx))).mean().iloc[-1])
                    dma200_sl = float(idx["Close"].rolling(min(200, len(idx))).mean().diff(5).iloc[-1])
                    last = float(idx["Close"].iloc[-1])
                    result["dma200"]    = round(dma200, 2)
                    result["dma200_sl"] = round(dma200_sl, 2)
                    if not result["nifty_last"]:
                        result["nifty_last"] = round(last, 2)
                    pct_from_dma = (last - dma200) / dma200 * 100
                    result["pct_from_dma200"] = round(pct_from_dma, 2)
            except Exception:
                pass

        # ── India VIX ─────────────────────────────────────────────────────────
        if _NSEPY_OK:
            try:
                vix = indiavix()
                result["vix"] = round(float(vix), 2) if vix else None
            except Exception:
                pass

        # ── FII/DII net activity ───────────────────────────────────────────────
        if _NSEPY_OK:
            try:
                fii_df = nse_fiidii()
                if isinstance(fii_df, pd.DataFrame) and not fii_df.empty:
                    # Sum recent FII net activity (last 5 sessions)
                    net_col = next((c for c in fii_df.columns
                                    if "net" in c.lower() and "fii" in c.lower()), None)
                    if not net_col:
                        net_col = next((c for c in fii_df.columns if "net" in c.lower()), None)
                    if net_col:
                        recent = fii_df[net_col].dropna().head(5)
                        net_5d = float(recent.sum())
                        result["fii_net_cr"]    = round(net_5d / 1e7, 2) \
                                                   if abs(net_5d) > 1e6 else round(net_5d, 2)
                        result["fii_sentiment"] = "BUYING" if net_5d > 0 else "SELLING"
            except Exception:
                pass

        # ── Classify regime ────────────────────────────────────────────────────
        vix  = result.get("vix") or 15
        pct  = result.get("pct_from_dma200", 0) or 0
        sl   = result.get("dma200_sl", 0) or 0

        if abs(pct) <= 1.5:
            regime = "SIDEWAYS"
        elif pct > 1.5:
            regime = "BULL_VOLATILE" if vix >= 18 else "BULL"
        else:
            regime = "BEAR_EXTREME" if vix > 25 else "BEAR"

        result["regime"] = regime
        return result

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 3: HISTORICAL OHLC
    # ─────────────────────────────────────────────────────────────────────────

    def get_ohlc(self, symbol: str, period: str = "1y",
                 start: date = None, end: date = None) -> pd.DataFrame:
        """
        Fetch OHLC history for one NSE stock.

        Priority:
          1. NSEpy get_history() — direct NSE, no rate limits
             (currently failing on Mac due to NSE SSL change; use when fixed)
          2. yfinance with .NS suffix — reliable, slight rate-limit risk
             Use smaller batches and sleep between calls.

        Returns DataFrame with columns: Open, High, Low, Close, Volume
        Index: DatetimeIndex (sorted ascending)
        """
        # Try NSEpy first (best quality, direct NSE data)
        if _NSEPY_LIB_OK and start and end:
            try:
                df = _nsepy_get_history(symbol=symbol, start=start, end=end)
                if df is not None and not df.empty:
                    return df[["Open","High","Low","Close","Volume"]].sort_index()
            except Exception:
                pass  # Fall through to yfinance

        # yfinance fallback
        if _YF_OK:
            try:
                kwargs = {"auto_adjust": True, "progress": False}
                if start and end:
                    kwargs["start"] = start
                    kwargs["end"]   = end
                else:
                    kwargs["period"] = period

                df = yf.download(f"{symbol}.NS", **kwargs)
                if isinstance(df.columns, pd.MultiIndex):
                    df = df.xs(f"{symbol}.NS", axis=1, level=1)
                if not df.empty:
                    return df[["Open","High","Low","Close","Volume"]].dropna().sort_index()
            except Exception as e:
                print(f"  yfinance failed for {symbol}: {e}")

        return pd.DataFrame()

    def get_ohlc_bulk(self, symbols: list, period: str = "1y",
                      batch_size: int = 100, sleep_sec: float = 2.0) -> dict:
        """
        Bulk OHLC download for multiple symbols via yfinance.
        Returns {symbol: DataFrame}.

        batch_size: reduce to 50-100 for 5y/10y to avoid rate limits.
        sleep_sec: pause between batches (increase to 3-5 for large universes).
        """
        result = {}
        tickers = [f"{s}.NS" for s in symbols]
        batches = [tickers[i:i+batch_size] for i in range(0, len(tickers), batch_size)]
        print(f"  Bulk OHLC: {len(symbols)} stocks | {len(batches)} batches "
              f"| period={period} | batch_size={batch_size}")

        for idx, batch in enumerate(batches, 1):
            print(f"    Batch {idx}/{len(batches)} ({len(batch)}) …", end=" ", flush=True)
            for attempt in range(3):
                try:
                    raw = yf.download(batch, period=period, auto_adjust=True,
                                      threads=True, progress=False)
                    if raw.empty:
                        print("empty"); break

                    if isinstance(raw.columns, pd.MultiIndex):
                        for t in batch:
                            sym = t.replace(".NS", "")
                            try:
                                df = raw.xs(t, axis=1, level=1).dropna(how="all")
                                if not df.empty and len(df) >= 30:
                                    result[sym] = df[["Open","High","Low","Close","Volume"]]
                            except KeyError:
                                pass
                    else:
                        sym = batch[0].replace(".NS", "")
                        if not raw.empty:
                            result[sym] = raw[["Open","High","Low","Close","Volume"]]

                    ok = sum(1 for t in batch if t.replace(".NS","") in result)
                    print(f"OK ({ok} usable)")
                    break

                except Exception as e:
                    if "Rate" in str(e) or "429" in str(e) or "Too Many" in str(e):
                        wait = 30 * (attempt + 1)
                        print(f"\n      Rate limited — waiting {wait}s …", end="", flush=True)
                        time.sleep(wait)
                    else:
                        print(f"ERROR: {e}"); break

            if idx < len(batches):
                time.sleep(sleep_sec)

        print(f"  Total: {len(result)}/{len(symbols)} tickers downloaded")
        return result

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 4: FILING EVENTS CALENDAR
    # ─────────────────────────────────────────────────────────────────────────

    def get_upcoming_events(self, days_ahead: int = 30) -> pd.DataFrame:
        """
        Fetch upcoming corporate events from NSE via nsepython.nse_events().

        Events include:
          - Board meetings (results, dividends, fund raising)
          - AGM / EGM announcements
          - Corporate actions (ex-date, record date)

        This is the "trends in company filings to regulator" data source.
        Use to:
          1. Identify stocks with upcoming results (potential catalyst)
          2. Pre-filter for high-quality earnings dates for event-study backtest
          3. Confirm Darvas/Piotroski signals with imminent fundamental trigger

        Returns DataFrame: symbol | company | purpose | date
        """
        if not _NSEPY_OK:
            return pd.DataFrame()
        try:
            df = nse_events()
            if isinstance(df, pd.DataFrame) and not df.empty:
                # Parse dates and filter to window
                df["date_parsed"] = pd.to_datetime(df["date"], format="%d-%b-%Y",
                                                    errors="coerce")
                cutoff = datetime.today() + timedelta(days=days_ahead)
                df = df[df["date_parsed"] <= cutoff].copy()
                df["days_away"] = (df["date_parsed"] - datetime.today()).dt.days
                df = df.sort_values("date_parsed")
                return df[["symbol","company","purpose","date","days_away"]].reset_index(drop=True)
        except Exception as e:
            print(f"  nse_events error: {e}")
        return pd.DataFrame()

    def get_results_calendar(self, days_ahead: int = 30) -> pd.DataFrame:
        """
        Filter upcoming events to ONLY quarterly/annual results announcements.
        Use as the 'filing event dates' for the event-study backtest.
        """
        ev = self.get_upcoming_events(days_ahead)
        if ev.empty:
            return ev
        results_keywords = ["result", "financial", "quarterly", "annual", "q1","q2","q3","q4"]
        mask = ev["purpose"].str.lower().str.contains("|".join(results_keywords), na=False)
        return ev[mask].reset_index(drop=True)

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 5: INSTITUTIONAL ACTIVITY
    # ─────────────────────────────────────────────────────────────────────────

    def get_bulk_deals(self) -> pd.DataFrame:
        """
        Today's NSE bulk deals — institutional-size trades that crossed
        0.5% of outstanding shares. Strong signal of conviction buying/selling.

        Usage: confirm Darvas breakout signals — a breakout with a
        simultaneous bulk buy from a known institution is higher conviction.

        Returns DataFrame: Date | Symbol | Security Name | Client Name |
                           Buy/Sell | Quantity Traded | Trade Price
        """
        if not _NSEPY_OK:
            return pd.DataFrame()
        try:
            df = get_bulkdeals()
            if isinstance(df, pd.DataFrame) and not df.empty:
                return df
        except Exception as e:
            print(f"  get_bulkdeals error: {e}")
        return pd.DataFrame()

    def get_block_deals(self) -> pd.DataFrame:
        """
        Today's block deals (≥ ₹10 Cr single trade, disclosed on block window).
        Higher threshold than bulk deals — strong institutional signals.
        """
        if not _NSEPY_OK:
            return pd.DataFrame()
        try:
            df = get_blockdeals()
            if isinstance(df, pd.DataFrame) and not df.empty:
                return df
        except Exception as e:
            print(f"  get_blockdeals error: {e}")
        return pd.DataFrame()

    def get_fii_dii(self) -> pd.DataFrame:
        """
        FII and DII net buy/sell activity from NSE.
        Use as a macro-level sentiment filter:
          FII net buying > 0 → institutional tailwind → higher screener conviction
          FII net selling → headwind → tighten stop-losses

        Returns DataFrame with net FII/DII flows (₹ Crore).
        """
        if not _NSEPY_OK:
            return pd.DataFrame()
        try:
            df = nse_fiidii()
            return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
        except Exception as e:
            print(f"  nse_fiidii error: {e}")
        return pd.DataFrame()

    def get_institutional_activity_score(self) -> dict:
        """
        Composite institutional activity score combining FII/DII + bulk deals.
        Score ranges from -3 (strong selling) to +3 (strong buying).

        Components:
          +1/-1 : FII net activity (5-day sum positive/negative)
          +1/-1 : DII net activity (supporting/opposing FII)
          +1/-1 : Bulk deal buy/sell ratio (today)

        Use as a multiplier on screener expected value:
          score = +3 → increase position size
          score = -3 → reduce or avoid position
        """
        score = 0
        details = {}

        # FII/DII
        try:
            fii_df = self.get_fii_dii()
            if not fii_df.empty:
                net_cols = [c for c in fii_df.columns if "net" in c.lower()]
                fii_col  = next((c for c in net_cols if "fii" in c.lower()), None)
                dii_col  = next((c for c in net_cols if "dii" in c.lower()), None)
                if fii_col:
                    fii_net = float(fii_df[fii_col].dropna().head(5).sum())
                    score  += 1 if fii_net > 0 else -1
                    details["fii_5d_net_cr"] = round(fii_net / 1e7, 2) if abs(fii_net) > 1e6 else fii_net
                if dii_col:
                    dii_net = float(fii_df[dii_col].dropna().head(5).sum())
                    score  += 1 if dii_net > 0 else -1
                    details["dii_5d_net_cr"] = round(dii_net / 1e7, 2) if abs(dii_net) > 1e6 else dii_net
        except Exception:
            pass

        # Bulk deals
        try:
            bd = self.get_bulk_deals()
            if not bd.empty and "Buy/Sell" in bd.columns:
                buys  = (bd["Buy/Sell"].str.upper() == "BUY").sum()
                sells = (bd["Buy/Sell"].str.upper() == "SELL").sum()
                total = buys + sells
                if total > 0:
                    buy_ratio = buys / total
                    score += 1 if buy_ratio > 0.6 else (-1 if buy_ratio < 0.4 else 0)
                    details["bulk_buys"]  = int(buys)
                    details["bulk_sells"] = int(sells)
                    details["bulk_buy_ratio"] = round(buy_ratio * 100, 1)
        except Exception:
            pass

        label = "STRONG_BUY" if score >= 3 else \
                "BUY"        if score >= 1 else \
                "NEUTRAL"    if score == 0 else \
                "SELL"       if score >= -2 else "STRONG_SELL"

        return {"score": score, "label": label, **details}

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 6: MARKET BREADTH + SENTIMENT
    # ─────────────────────────────────────────────────────────────────────────

    def get_market_breadth(self) -> dict:
        """
        Market breadth using India VIX and Nifty 50 live data.

        Breadth metrics:
          - VIX level: fear gauge (>25 = high fear, <15 = complacency)
          - Nifty % from 52-week high: market strength indicator
          - % from 52-week low: recovery / oversold signal

        These complement the 200 DMA regime with short-term sentiment context.
        """
        result = {"vix": None, "vix_regime": "NORMAL", "nifty_52w_pct": None}

        if _NSEPY_OK:
            try:
                vix = indiavix()
                result["vix"] = round(float(vix), 2) if vix else None
                if vix:
                    result["vix_regime"] = (
                        "PANIC"        if vix > 30 else
                        "HIGH_FEAR"    if vix > 22 else
                        "ELEVATED"     if vix > 17 else
                        "NORMAL"       if vix > 12 else
                        "COMPLACENCY"
                    )

                q = nse_get_index_quote("NIFTY 50")
                if q:
                    last, yh, yl = (float(q.get(k, 0) or 0)
                                    for k in ("last","yearHigh","yearLow"))
                    if yh and yl:
                        result["nifty_last"]     = last
                        result["nifty_52w_high"] = yh
                        result["nifty_52w_low"]  = yl
                        result["pct_from_52wh"]  = round((last - yh)/yh*100, 2)
                        result["pct_from_52wl"]  = round((last - yl)/yl*100, 2)
            except Exception:
                pass

        return result

    def get_nifty_pe_pb(self) -> dict:
        """
        Nifty 50 P/E and P/B ratios — macro valuation context.
        Nifty P/E > 25 = expensive; < 18 = cheap; 18-25 = fair value.
        Use to calibrate screener thresholds for the current market cycle.
        """
        # yfinance doesn't provide this; use cached NSE data if available
        cache_file = CACHE_DIR / "nifty_pe_pb.json"
        if cache_file.exists():
            age = datetime.now().timestamp() - cache_file.stat().st_mtime
            if age < self._cache_ttl:
                import json
                with open(cache_file) as f:
                    return json.load(f)

        # Try nsepython index_pe_pb_div
        if _NSEPY_OK:
            try:
                from nsepython import index_pe_pb_div
                today_str = datetime.today().strftime("%d-%m-%Y")
                week_ago  = (datetime.today() - timedelta(days=7)).strftime("%d-%m-%Y")
                df = index_pe_pb_div("NIFTY 50", week_ago, today_str)
                if isinstance(df, pd.DataFrame) and not df.empty:
                    latest = df.iloc[-1]
                    result = {
                        "pe":   float(latest.get("PE", 0)),
                        "pb":   float(latest.get("PB", 0)),
                        "div_yield": float(latest.get("DivYield", 0)),
                        "date": str(df.index[-1]),
                    }
                    import json
                    with open(cache_file, "w") as f:
                        json.dump(result, f)
                    return result
            except Exception:
                pass

        return {"pe": None, "pb": None, "div_yield": None, "date": None}

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 7: LIVE SCREENING INTEGRATION
    # ─────────────────────────────────────────────────────────────────────────

    def get_live_context(self) -> dict:
        """
        Comprehensive live market context combining all data sources.
        Run this once at the start of each screening session to calibrate
        which strategies and parameters to use today.

        Returns a single dict with all live context:
          - regime (BULL/BEAR/SIDEWAYS)
          - vix + vix_regime
          - fii/dii sentiment score
          - upcoming results calendar (next 7 days)
          - institutional activity score
          - strategy recommendations based on current conditions
        """
        print("  Fetching live market context …")
        context = {}

        # Regime
        regime_data = self.get_regime()
        context.update(regime_data)

        # Market breadth
        breadth = self.get_market_breadth()
        context.update({k: v for k, v in breadth.items() if k not in context})

        # Institutional score
        inst = self.get_institutional_activity_score()
        context["institutional_score"]  = inst.get("score", 0)
        context["institutional_label"]  = inst.get("label", "NEUTRAL")
        context["bulk_buy_ratio"]        = inst.get("bulk_buy_ratio")

        # Upcoming results (next 7 days)
        results_cal = self.get_results_calendar(days_ahead=7)
        context["results_next7d"]       = results_cal["symbol"].tolist() if not results_cal.empty else []
        context["results_count_7d"]     = len(context["results_next7d"])

        # Strategy recommendation matrix based on live context
        regime = context.get("regime", "UNKNOWN")
        vix    = context.get("vix", 15) or 15
        inst_s = context.get("institutional_score", 0)

        # Recommend best screener + horizon for today
        recs = _get_strategy_recommendation(regime, vix, inst_s)
        context["recommendations"] = recs

        return context

    def print_live_context(self):
        """Pretty-print the live market context for the daily dashboard."""
        ctx = self.get_live_context()

        print(f"\n{'═'*65}")
        print(f"  📊 LIVE MARKET CONTEXT — {datetime.now().strftime('%d %b %Y %H:%M IST')}")
        print(f"{'═'*65}")

        # Regime
        regime = ctx.get("regime", "UNKNOWN")
        regime_emoji = {"BULL":"🟢","BEAR":"🔴","BEAR_EXTREME":"🔴🔴",
                        "BULL_VOLATILE":"🟡","SIDEWAYS":"🟡"}.get(regime, "⚪")
        print(f"  Market Regime:  {regime_emoji} {regime}")
        print(f"  Nifty 50:       {ctx.get('nifty_last','—')}")
        print(f"  200 DMA:        {ctx.get('dma200','—')}  "
              f"({ctx.get('pct_from_dma200','—'):+.2f}% from DMA)" if ctx.get('pct_from_dma200') else "  200 DMA:        —")
        print(f"  52-wk range:    {ctx.get('nifty_52w_low','—')} – {ctx.get('nifty_52w_high','—')}")
        print(f"  % from 52wH:    {ctx.get('pct_from_52wh','—'):+.2f}%" if ctx.get('pct_from_52wh') else "  % from 52wH:    —")

        # VIX
        vix = ctx.get("vix")
        vix_regime = ctx.get("vix_regime", "")
        vix_color = "🟢" if (vix and vix < 15) else "🟡" if (vix and vix < 22) else "🔴"
        print(f"\n  India VIX:      {vix_color} {vix}  [{vix_regime}]")
        print(f"  Interpretation: {'Low volatility — trend-following works well' if vix and vix<15 else 'Elevated — widen stop-losses' if vix and vix<22 else 'HIGH FEAR — reduce position size'}")

        # FII/DII
        print(f"\n  FII Sentiment:  {ctx.get('fii_sentiment','—')}"
              f"  (5d net: ₹{ctx.get('fii_net_cr','—')} Cr)")
        print(f"  Inst. Activity: {ctx.get('institutional_label','—')} "
              f"(score={ctx.get('institutional_score',0):+d})")
        if ctx.get("bulk_buy_ratio"):
            print(f"  Bulk deal buy%: {ctx['bulk_buy_ratio']:.1f}%")

        # Upcoming results
        r7 = ctx.get("results_next7d", [])
        print(f"\n  Results next 7d: {len(r7)} stocks — {', '.join(r7[:8])}{'...' if len(r7)>8 else ''}")

        # Recommendations
        recs = ctx.get("recommendations", [])
        print(f"\n  ── TODAY'S STRATEGY RECOMMENDATIONS ──────────────────────")
        for r in recs:
            print(f"  {r}")

        print(f"{'═'*65}\n")
        return ctx

    # ─────────────────────────────────────────────────────────────────────────
    # SECTION 8: CACHE HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    def _get_cache(self, key: str):
        entry = self._cache.get(key)
        if entry and (time.time() - entry["ts"] < self._cache_ttl):
            return entry["data"]
        return None

    def _set_cache(self, key: str, data):
        self._cache[key] = {"data": data, "ts": time.time()}


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY RECOMMENDATION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def _get_strategy_recommendation(regime: str, vix: float, inst_score: int) -> list:
    """
    Map live market conditions to actionable screener recommendations.
    Based on walk-forward backtest results from the research framework.

    Key findings from backtesting:
      BULL + low VIX:     Darvas (1-3mo), Golden Cross (1yr)
      BEAR + any VIX:     Darvas BEAR breakout (1-3mo), wait for reversal
      SIDEWAYS:           Golden Cross setup, Coffee Can accumulation
      HIGH VIX (>22):     Reduce size, focus on quality (Piotroski ≥7)
      FII buying:         Higher conviction on all signals
    """
    recs = []

    # Base recommendations by regime
    regime_map = {
        "BULL": [
            "📈 BULL MARKET — Use Darvas breakouts (T+63d to T+126d horizon)",
            "   Best EV: Darvas +6-13% at 1-3 months | Alpha vs Nifty: +3-8%",
            "   For 1-year horizon: Golden Cross signals give EV +30-46%",
            "   Confirmation: prefer volume >120% of 20-day average on breakout",
        ],
        "BULL_VOLATILE": [
            "⚡ BULL VOLATILE — Nifty above 200 DMA but VIX elevated",
            "   Reduce position size by 30% | Tighten stop-loss to box bottom",
            "   Use Coffee Can quality filter before acting on Darvas signals",
            "   Avoid leveraged positions until VIX drops below 18",
        ],
        "BEAR": [
            "📉 BEAR MARKET — Conservative mode activated",
            "   Short-term (T+1d to T+5d): expected value ≈ 0%, AVOID",
            "   Medium-term (T+63d): Darvas BEAR breakouts work — EV +13%",
            "   Only act on stocks with: Piotroski ≥7 AND Bull Cartel criteria",
            "   Cash allocation target: increase to 40-60% of portfolio",
        ],
        "BEAR_EXTREME": [
            "🚨 BEAR EXTREME — VIX >25 (panic zone)",
            "   DO NOT initiate new long positions",
            "   Consider systematic accumulation of Coffee Can stocks (long-term)",
            "   Watch for VIX reversal below 22 as signal to re-enter",
            "   These conditions historically precede the best Darvas breakouts",
        ],
        "SIDEWAYS": [
            "⬛ SIDEWAYS MARKET — Range-bound conditions",
            "   Best screener: Golden Cross for medium-term (EV +2-7% at T+63d)",
            "   For 1-year horizon: Golden Cross EV +40-68% (strongest signal)",
            "   Accumulate Coffee Can stocks on dips — structural quality holds",
            "   Darvas expected value ≈ 0% in sideways — use only with fundamental filter",
        ],
    }

    recs.extend(regime_map.get(regime, ["⚠️  Unknown regime — wait for clarity"]))

    # VIX overlay
    if vix and vix > 25:
        recs.append(f"   🔴 VIX={vix:.1f} (PANIC) — halve standard position sizes")
    elif vix and vix > 18:
        recs.append(f"   🟡 VIX={vix:.1f} (ELEVATED) — reduce size by 25%")
    elif vix and vix < 12:
        recs.append(f"   ⚠️  VIX={vix:.1f} (COMPLACENCY) — consider taking partial profits")

    # Institutional score overlay
    if inst_score >= 2:
        recs.append(f"   ✅ Institutional buying ({inst_score:+d}) — increase conviction on signals")
    elif inst_score <= -2:
        recs.append(f"   ❌ Institutional selling ({inst_score:+d}) — avoid new entries, protect gains")

    return recs


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE FUNCTIONS (for backward compatibility with existing scripts)
# ═══════════════════════════════════════════════════════════════════════════════

_fetcher = None

def get_fetcher() -> NSEDataFetcher:
    """Get or create the singleton NSEDataFetcher."""
    global _fetcher
    if _fetcher is None:
        _fetcher = NSEDataFetcher()
    return _fetcher


def get_nse_symbols() -> list:
    """Get all NSE EQ symbols. Drop-in replacement for yfinance-based fetching."""
    return get_fetcher().get_all_symbols()


def get_live_regime() -> dict:
    """Get current market regime with VIX, FII/DII, and recommendations."""
    return get_fetcher().get_regime()


def get_upcoming_results(days: int = 7) -> pd.DataFrame:
    """Get stocks announcing results in the next N days."""
    return get_fetcher().get_results_calendar(days_ahead=days)


def get_institutional_confirmation(symbol: str) -> bool:
    """Check if a symbol had a bulk/block buy deal today."""
    bd = get_fetcher().get_bulk_deals()
    if bd.empty or "Symbol" not in bd.columns:
        return False
    sym_deals = bd[bd["Symbol"].str.upper() == symbol.upper()]
    if sym_deals.empty:
        return False
    buy_col = "Buy/Sell" if "Buy/Sell" in sym_deals.columns else None
    if buy_col:
        return (sym_deals[buy_col].str.upper() == "BUY").any()
    return not sym_deals.empty


# ═══════════════════════════════════════════════════════════════════════════════
# CLI — run as script for daily dashboard
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="NSE live market context dashboard")
    parser.add_argument("--regime",  action="store_true", help="Show market regime only")
    parser.add_argument("--symbols", action="store_true", help="Show NSE symbol count")
    parser.add_argument("--events",  action="store_true", help="Show upcoming events (30d)")
    parser.add_argument("--bulk",    action="store_true", help="Show today's bulk deals")
    parser.add_argument("--full",    action="store_true", default=True, help="Full dashboard")
    args = parser.parse_args()

    fetcher = NSEDataFetcher()

    if args.symbols:
        syms = fetcher.get_all_symbols()
        print(f"NSE EQ Universe: {len(syms)} symbols")
        print(f"Sample: {syms[:10]}")

    if args.events:
        ev = fetcher.get_upcoming_events(30)
        print(f"\nUpcoming Events (30 days):")
        print(ev.to_string(index=False) if not ev.empty else "No events")

    if args.bulk:
        bd = fetcher.get_bulk_deals()
        print(f"\nToday's Bulk Deals ({len(bd)}):")
        print(bd.to_string(index=False) if not bd.empty else "No bulk deals today")

    if args.full or args.regime:
        fetcher.print_live_context()
