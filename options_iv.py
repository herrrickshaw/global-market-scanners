#!/usr/bin/env python3
"""
options_iv.py
-------------
Closes the scout's 'options_implied' gap: option-implied metrics from the yfinance
options chain — ATM implied volatility, the put/call ratio, and the IV skew (the
"fear gauge": OTM puts richer than OTM calls). These are forward-looking risk signals
(implied vol, variance risk premium, skew) that price history can't give.

Only optionable names (mostly US large caps) have chains; the pure aggregators are
unit-tested and the fetch degrades gracefully offline.

Usage:
  python options_iv.py --tickers AAPL,NVDA,SPY
"""

from __future__ import annotations

import argparse
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


# ── pure option-chain aggregators ─────────────────────────────────────────────
def atm_iv(calls: pd.DataFrame, puts: pd.DataFrame, spot: float) -> float:
    """Average implied vol of the call and put nearest the money."""
    ivs = []
    for df in (calls, puts):
        if df is None or df.empty or "strike" not in df or "impliedVolatility" not in df:
            continue
        i = (df["strike"] - spot).abs().idxmin()
        iv = df.loc[i, "impliedVolatility"]
        if np.isfinite(iv) and iv > 0:
            ivs.append(float(iv))
    return float(np.mean(ivs)) if ivs else np.nan


def put_call_ratio(calls: pd.DataFrame, puts: pd.DataFrame, field: str = "openInterest") -> float:
    """Put/call ratio by open interest (or volume): >1 = defensive/bearish positioning."""
    pc = puts[field].sum() if field in puts else np.nan
    cc = calls[field].sum() if field in calls else np.nan
    return float(pc / cc) if cc and np.isfinite(pc) and cc > 0 else np.nan


def iv_skew(calls: pd.DataFrame, puts: pd.DataFrame, spot: float, moneyness: float = 0.10) -> float:
    """IV skew = (OTM put IV) − (OTM call IV) at ~`moneyness` away from spot. Positive
    = downside protection is bid up (the classic equity fear skew)."""
    def _otm_iv(df, target):
        if df is None or df.empty:
            return np.nan
        i = (df["strike"] - target).abs().idxmin()
        return float(df.loc[i, "impliedVolatility"])
    put_iv = _otm_iv(puts, spot * (1 - moneyness))       # OTM put (below spot)
    call_iv = _otm_iv(calls, spot * (1 + moneyness))     # OTM call (above spot)
    if not (np.isfinite(put_iv) and np.isfinite(call_iv)):
        return np.nan
    return float(put_iv - call_iv)


def chain_metrics(calls, puts, spot) -> dict:
    return {"atm_iv%": round(atm_iv(calls, puts, spot) * 100, 1) if np.isfinite(atm_iv(calls, puts, spot)) else np.nan,
            "put_call": round(put_call_ratio(calls, puts), 2),
            "iv_skew%": round(iv_skew(calls, puts, spot) * 100, 1) if np.isfinite(iv_skew(calls, puts, spot)) else np.nan}


# ── fetch (yfinance options, graceful) ────────────────────────────────────────
def fetch_chain_yf(ticker: str):
    import apiclient
    import yfinance as yf
    try:
        t = yf.Ticker(ticker)
        exps = apiclient.robust("yfinance", lambda: t.options, retries=2)
        if not exps:
            return None, None, None
        chain = apiclient.robust("yfinance", lambda: t.option_chain(exps[0]), retries=2)
        spot = apiclient.robust("yfinance", lambda: t.fast_info.get("lastPrice"), retries=2)
        return chain.calls, chain.puts, float(spot) if spot else np.nan
    except Exception:
        return None, None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default="AAPL,NVDA,SPY,MSFT,TSLA")
    args = ap.parse_args()
    rows = []
    for tk in [t.strip().upper() for t in args.tickers.split(",") if t.strip()]:
        calls, puts, spot = fetch_chain_yf(tk)
        if calls is None or not np.isfinite(spot):
            continue
        rows.append({"ticker": tk, "spot": round(spot, 2), **chain_metrics(calls, puts, spot)})
    if not rows:
        print("no option chains fetched (offline / non-optionable) — aggregators are unit-tested"); return
    df = pd.DataFrame(rows).sort_values("iv_skew%", ascending=False)
    print(f"\n=== OPTION-IMPLIED METRICS (yfinance, nearest expiry) — {len(df)} names ===")
    print(f"  {'ticker':10}{'spot':>9}{'ATM_IV%':>9}{'put/call':>10}{'IV_skew%':>10}")
    for _, r in df.iterrows():
        print(f"  {str(r['ticker']):10}{r['spot']:>9.2f}{r['atm_iv%']:>9}{r['put_call']:>10}"
              f"{r['iv_skew%']:>10}")
    print("\n  ATM_IV = implied vol; put/call>1 = defensive; IV_skew>0 = downside 'fear' bid up.")


if __name__ == "__main__":
    main()
