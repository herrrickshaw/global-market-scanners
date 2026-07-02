#!/usr/bin/env python3
"""
ml_viability.py
---------------
Cross-market 5-year viability backtest for the ML directional signal and the
screen built on top of it. Applies the (market-agnostic) ml_signal_engine logic
to every market — US, India, Japan, Korea, Europe — and asks:

  "Over the last 5 years, is the ML-bullish screen viable in each market?"

For each market it runs a walk-forward evaluation (no lookahead): on each test
day a Ridge model is trained on the prior 252 days and predicts the T+5d return.
We then measure, per market:

  • directional_acc  — % of days the predicted sign matched the realised sign
  • rmse / mae       — prediction error (vs AlQahtani et al. 2025 benchmarks)
  • bull_fwd_ret%    — avg realised 5d forward return when signal = BULLISH
  • base_fwd_ret%    — avg realised 5d forward return across all days (buy&hold proxy)
  • edge%            — bull_fwd_ret − base_fwd_ret  (the screen's value-add)
  • bull_hit%        — % of BULLISH calls with positive realised forward return
  • VIABLE           — edge > 0 AND bull_hit% > 50 AND directional_acc > 50

Usage:
  python ml_viability.py                       # all markets, 5y, weekly test steps
  python ml_viability.py --years 5 --step 5    # step = test every Nth trading day
  python ml_viability.py --markets US India    # subset
  python ml_viability.py --top 8               # first N tickers per market
"""

import argparse
import sys
import warnings
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from ml_signal_engine import (
    MLSignalEngine, compute_features, z_score_normalise,
    FEATURE_NAMES, LOOKBACK, TRAIN_WINDOW, PREDICT_DAYS,
    BULLISH_THRESHOLD, BEARISH_THRESHOLD,
)

warnings.filterwarnings("ignore")

# Representative liquid universe per market (yfinance symbols). Index heavyweights
# — enough to gauge viability without downloading the full exchange.
MARKET_UNIVERSES = {
    "US":     ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "JPM", "V",
               "UNH", "XOM", "JNJ", "WMT"],
    "India":  ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
               "LT.NS", "ITC.NS", "SBIN.NS", "BHARTIARTL.NS", "KOTAKBANK.NS",
               "HINDUNILVR.NS", "MARUTI.NS"],
    "Japan":  ["7203.T", "6758.T", "9984.T", "6861.T", "8306.T", "9433.T",
               "6098.T", "7974.T", "4063.T", "8035.T"],
    "Korea":  ["005930.KS", "000660.KS", "005380.KS", "035420.KS", "051910.KS",
               "005490.KS", "035720.KS", "012330.KS"],
    "Europe": ["ASML.AS", "MC.PA", "SAP.DE", "SIE.DE", "OR.PA", "AIR.PA",
               "RMS.PA", "SU.PA", "ALV.DE", "DTE.DE"],
}


def download_ohlc(tickers, years):
    """OHLC via the Cassandra cache (local after first run; yfinance fallback).
    Returns {ticker: DataFrame}. See PERFORMANCE.md — avoids re-downloading."""
    from market_store import cached_download
    return {t: df for t, df in cached_download(tickers, years=years).items()
            if df is not None and len(df) > MIN_NEEDED}


MIN_NEEDED = LOOKBACK + TRAIN_WINDOW + PREDICT_DAYS + 250  # ~ enough for a few yrs of test pts


def evaluate_market(name, ohlc, engine, step):
    """Walk-forward ML eval + screen viability for one market. Returns a dict row."""
    n_correct = n_total = 0
    actuals, preds = [], []
    bull_rets, all_rets = [], []
    bull_hits = bull_n = 0

    for sym, df in ohlc.items():
        try:
            feats = compute_features(df)
            close = df["Close"].astype(float).reindex(feats.index)
            target = close.pct_change(PREDICT_DAYS).shift(-PREDICT_DAYS) * 100
            aligned = feats.join(target.rename("t"), how="inner").dropna()
            if len(aligned) < TRAIN_WINDOW + LOOKBACK + 30:
                continue
            fdf, tser = aligned[FEATURE_NAMES], aligned["t"]
            start = TRAIN_WINDOW + LOOKBACK
            for ti in range(start, len(aligned) - PREDICT_DAYS, step):
                tr_f = fdf.iloc[ti - TRAIN_WINDOW:ti]
                tr_t = tser.iloc[ti - TRAIN_WINDOW:ti]
                X, y = engine._make_sequences(tr_f, tr_t)
                if len(X) < 20:
                    continue
                model = engine._make_model()
                model.fit(X, y)
                win = fdf.iloc[ti - LOOKBACK:ti].values
                z = z_score_normalise(win).flatten().reshape(1, -1)
                pred = float(model.predict(z)[0])
                act = float(tser.iloc[ti])
                actuals.append(act); preds.append(pred); all_rets.append(act)
                if (pred > 0 and act > 0) or (pred < 0 and act < 0):
                    n_correct += 1
                n_total += 1
                if pred >= BULLISH_THRESHOLD:
                    bull_rets.append(act); bull_n += 1
                    if act > 0:
                        bull_hits += 1
        except Exception:
            continue

    if n_total == 0:
        return {"Market": name, "n_stocks": len(ohlc), "n_preds": 0, "VIABLE": "n/a"}

    from sklearn.metrics import mean_squared_error, mean_absolute_error
    rmse = float(np.sqrt(mean_squared_error(actuals, preds)))
    mae = float(mean_absolute_error(actuals, preds))
    dacc = n_correct / n_total * 100
    base = float(np.mean(all_rets))
    bull = float(np.mean(bull_rets)) if bull_rets else 0.0
    edge = bull - base
    bhit = bull_hits / bull_n * 100 if bull_n else 0.0
    viable = (edge > 0) and (bhit > 50) and (dacc > 50)
    return {
        "Market": name, "n_stocks": len(ohlc), "n_preds": n_total,
        "dir_acc%": round(dacc, 1), "rmse": round(rmse, 3), "mae": round(mae, 3),
        "base_fwd%": round(base, 3), "bull_fwd%": round(bull, 3),
        "edge%": round(edge, 3), "bull_hit%": round(bhit, 1), "bull_n": bull_n,
        "VIABLE": "YES" if viable else "no",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--step", type=int, default=5, help="test every Nth trading day (5=weekly)")
    ap.add_argument("--top", type=int, default=None, help="first N tickers per market")
    ap.add_argument("--markets", nargs="*", default=list(MARKET_UNIVERSES))
    ap.add_argument("--model", default="ridge", choices=["ridge", "lr"])
    ap.add_argument("--out", default="ml_viability_5y.xlsx")
    args = ap.parse_args()

    engine = MLSignalEngine(model_type=args.model)
    rows = []
    for mkt in args.markets:
        tickers = MARKET_UNIVERSES.get(mkt, [])
        if args.top:
            tickers = tickers[:args.top]
        if not tickers:
            print(f"  [skip] unknown market {mkt}", file=sys.stderr); continue
        print(f"[{mkt}] downloading {len(tickers)} tickers ({args.years}y)…", file=sys.stderr, flush=True)
        ohlc = download_ohlc(tickers, args.years)
        print(f"[{mkt}] {len(ohlc)} usable; running walk-forward ML eval (step={args.step})…",
              file=sys.stderr, flush=True)
        row = evaluate_market(mkt, ohlc, engine, args.step)
        rows.append(row)
        print(f"[{mkt}] -> {row}", file=sys.stderr, flush=True)

    res = pd.DataFrame(rows)
    res.attrs["generated"] = datetime.now(timezone.utc).isoformat()
    try:
        res.to_excel(args.out, index=False)
    except Exception:
        res.to_csv(args.out.replace(".xlsx", ".csv"), index=False)
    print("\n=== 5-YEAR ML SCREEN VIABILITY BY MARKET ===")
    print(res.to_string(index=False))
    print("\nVIABLE = edge>0 AND bull_hit%>50 AND dir_acc%>50  "
          "(ML-bullish screen beats buy&hold and calls direction better than chance)")


if __name__ == "__main__":
    main()
