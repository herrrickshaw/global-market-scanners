#!/usr/bin/env python3
"""
screen_viability.py
-------------------
Full-universe, 5-year viability backtest of price/technical SCREENS across all
five markets, plus the ML-bullish screen — results stored in a compact SQLite DB.

Screens checked (the OHLC-computable subset of screener.in/screens/):
  rsi_oversold        RSI(14) < 30                        (mean-reversion)
  near_52w_high       within 10% of 52-week high          ("creating new high")
  price_vol_breakout  volume >= 5x 20d avg & up day        ("Price Volume Action")
  darvas_proximity    <=10% below 52w high, >=10% above 52w low, price>10, vol>1e5
  golden_crossover    50DMA crosses above 200DMA           (Golden Crossover)
  ml_bullish          ml_signal_engine Ridge BULLISH       (optional, --include-ml)

For each (market, ticker, screen) we measure the realised PREDICT_DAYS (5d) forward
return on signal days vs the stock's all-day baseline, hit rate, and the edge.
Aggregated per (market, screen) into `market_screen_summary` — the small,
git-friendly artifact. Per-ticker detail lives in `ticker_screen` (gitignored).

Low-memory + small file:
  - one ticker processed at a time; only summary rows are kept
  - SQLite with DELETE journal + bulk commits (safe on macOS, per project note)
  - resumable: tickers already in `done` are skipped
  - `--export-summary out.db` writes a tiny DB with ONLY the aggregate table

Usage:
  python screen_viability.py --markets India --limit 200          # quick
  python screen_viability.py --years 5 --db viability.db          # full universe
  python screen_viability.py --include-ml --limit 100             # add ML screen
  python screen_viability.py --export-summary viability_summary.db
"""

import argparse
import glob
import os
import sqlite3
import sys
import time
import warnings
from datetime import datetime, timezone

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

DL = os.path.expanduser("~/Downloads")
FWD = 5            # forward return horizon (trading days) — matches ML PREDICT_DAYS
MIN_BARS = 300     # need enough history for 200DMA + 52w window + forward

SCAN_GLOBS = {
    "US":     "data/us_full_scan/**/us_full_scan_*.xlsx",
    "India":  "data/**/indian_full_scan_*.xlsx",
    "Japan":  "data/japan_scan/**/japan_market_scan_*.xlsx",
    "Korea":  "data/korea_scan/**/korea_market_scan_*.xlsx",
    "Europe": "data/european_scan/**/european_market_scan_*.xlsx",
}


def load_universe(market, limit=None):
    """Full ticker list for a market, as yfinance symbols, from the latest scan."""
    hits = sorted(glob.glob(os.path.join(DL, SCAN_GLOBS[market]), recursive=True))
    if not hits:
        return []
    a = pd.ExcelFile(hits[-1]).parse("All_Stocks")
    tickers = []
    for _, r in a.iterrows():
        if market == "India":
            t = f"{str(r['Symbol']).strip()}{str(r['Suffix']).strip()}"
        elif market in ("Japan", "Korea"):
            t = str(r.get("YF_Ticker", "")).strip()
        else:
            t = str(r["Symbol"]).strip()
        if t and t != "nan":
            tickers.append(t)
    tickers = list(dict.fromkeys(tickers))
    return tickers[:limit] if limit else tickers


# ── screen signal functions: each returns a boolean Series over the OHLC index ──

def screens_for(df):
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    vol = df["Volume"].astype(float)

    # RSI(14)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))

    hi52 = close.rolling(252, min_periods=120).max()
    lo52 = close.rolling(252, min_periods=120).min()
    vol20 = vol.rolling(20).mean()
    dma50 = close.rolling(50).mean()
    dma200 = close.rolling(200).mean()
    up = close.pct_change() > 0

    gc = (dma50 > dma200) & (dma50.shift(1) <= dma200.shift(1))  # cross event

    return {
        "rsi_oversold":       rsi < 30,
        "near_52w_high":      close >= 0.90 * hi52,
        "price_vol_breakout": (vol >= 5 * vol20) & up,
        "darvas_proximity":   (close >= 0.90 * hi52) & (close >= 1.10 * lo52)
                              & (close > 10) & (vol > 1e5),
        "golden_crossover":   gc,
    }


def eval_ticker(df, include_ml=False, ml_engine=None):
    """Return list of per-screen summary dicts for one ticker, or [] if too short."""
    if df is None or len(df) < MIN_BARS:
        return []
    close = df["Close"].astype(float)
    # Realised 5d forward return %, clipped to ±30% to stop penny-stock / illiquid
    # spikes (and split glitches) from dominating the mean — a >30% move in a
    # week is outlier noise for this purpose. hit_pct is unaffected (sign only).
    fwd = (close.pct_change(FWD).shift(-FWD) * 100).clip(-30, 30)
    base = float(fwd.mean())
    rows = []
    sig = screens_for(df)

    if include_ml and ml_engine is not None:
        try:
            from ml_signal_engine import compute_features, z_score_normalise, \
                FEATURE_NAMES, LOOKBACK, TRAIN_WINDOW, BULLISH_THRESHOLD
            feats = compute_features(df)
            tgt = close.pct_change(FWD).shift(-FWD).reindex(feats.index) * 100
            al = feats.join(tgt.rename("t"), how="inner").dropna()
            bull = pd.Series(False, index=df.index)
            if len(al) > TRAIN_WINDOW + LOOKBACK + 30:
                fdf, tser = al[FEATURE_NAMES], al["t"]
                for ti in range(TRAIN_WINDOW + LOOKBACK, len(al) - FWD, 10):
                    X, y = ml_engine._make_sequences(fdf.iloc[ti-TRAIN_WINDOW:ti],
                                                     tser.iloc[ti-TRAIN_WINDOW:ti])
                    if len(X) < 20:
                        continue
                    m = ml_engine._make_model(); m.fit(X, y)
                    z = z_score_normalise(fdf.iloc[ti-LOOKBACK:ti].values).flatten().reshape(1, -1)
                    if float(m.predict(z)[0]) >= BULLISH_THRESHOLD:
                        bull.iloc[df.index.get_loc(al.index[ti])] = True
            sig["ml_bullish"] = bull
        except Exception:
            pass

    for name, mask in sig.items():
        mask = mask.reindex(close.index).fillna(False)
        r = fwd[mask].dropna()
        n = int(len(r))
        if n == 0:
            rows.append({"screen": name, "n_signals": 0, "avg_fwd5d": None,
                         "hit_pct": None, "baseline": round(base, 4), "edge": None})
            continue
        avg = float(r.mean()); hit = float((r > 0).mean() * 100)
        rows.append({"screen": name, "n_signals": n, "avg_fwd5d": round(avg, 4),
                     "hit_pct": round(hit, 1), "baseline": round(base, 4),
                     "edge": round(avg - base, 4)})
    return rows


# ── SQLite (low-memory, DELETE journal, bulk commits) ─────────────────────────

def init_db(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=DELETE;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS ticker_screen(
        market TEXT, ticker TEXT, screen TEXT, n_signals INT,
        avg_fwd5d REAL, hit_pct REAL, baseline REAL, edge REAL,
        PRIMARY KEY (market, ticker, screen));
      CREATE TABLE IF NOT EXISTS done(market TEXT, ticker TEXT,
        PRIMARY KEY (market, ticker));
    """)
    conn.commit()
    return conn


def already_done(conn, market):
    return set(r[0] for r in conn.execute(
        "SELECT ticker FROM done WHERE market=?", (market,)))


def write_summary(conn):
    """Aggregate per (market, screen) into market_screen_summary."""
    conn.execute("DROP TABLE IF EXISTS market_screen_summary;")
    conn.execute("""
      CREATE TABLE market_screen_summary AS
      SELECT market, screen,
             COUNT(*) AS n_tickers,
             SUM(n_signals) AS total_signals,
             ROUND(AVG(avg_fwd5d), 4) AS avg_fwd5d,
             ROUND(AVG(hit_pct), 1)   AS avg_hit_pct,
             ROUND(AVG(edge), 4)      AS avg_edge,
             ROUND(100.0*AVG(CASE WHEN edge>0 THEN 1 ELSE 0 END),1) AS pct_tickers_pos_edge,
             CASE WHEN AVG(edge)>0 AND AVG(hit_pct)>50 THEN 'YES' ELSE 'no' END AS viable
      FROM ticker_screen WHERE n_signals > 0
      GROUP BY market, screen ORDER BY market, avg_edge DESC;
    """)
    conn.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", nargs="*", default=list(SCAN_GLOBS))
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--limit", type=int, default=None, help="cap tickers per market")
    ap.add_argument("--db", default="viability.db")
    ap.add_argument("--batch", type=int, default=40, help="yfinance download batch size")
    ap.add_argument("--include-ml", action="store_true")
    ap.add_argument("--export-summary", default=None,
                    help="write a tiny DB with only market_screen_summary, then exit")
    args = ap.parse_args()

    conn = init_db(args.db)

    if args.export_summary:
        write_summary(conn)
        out = sqlite3.connect(args.export_summary)
        out.execute("PRAGMA journal_mode=DELETE;")
        conn.backup(out, name="main") if False else None
        # copy just the summary table
        df = pd.read_sql("SELECT * FROM market_screen_summary", conn)
        df.to_sql("market_screen_summary", out, if_exists="replace", index=False)
        out.execute("VACUUM;"); out.commit(); out.close()
        print(f"exported summary ({len(df)} rows) -> {args.export_summary} "
              f"({os.path.getsize(args.export_summary)} bytes)", file=sys.stderr)
        return

    import yfinance as yf
    ml_engine = None
    if args.include_ml:
        from ml_signal_engine import MLSignalEngine
        ml_engine = MLSignalEngine(model_type="ridge")

    for market in args.markets:
        universe = load_universe(market, args.limit)
        done = already_done(conn, market)
        todo = [t for t in universe if t not in done]
        print(f"[{market}] universe={len(universe)} done={len(done)} todo={len(todo)}",
              file=sys.stderr, flush=True)

        for i in range(0, len(todo), args.batch):
            batch = todo[i:i + args.batch]
            try:
                data = yf.download(batch, period=f"{args.years}y", auto_adjust=True,
                                   progress=False, group_by="ticker", threads=True)
            except Exception as e:
                print(f"  [{market}] download error: {e}", file=sys.stderr); continue

            rows, donerows = [], []
            for t in batch:
                try:
                    df = data[t] if isinstance(data.columns, pd.MultiIndex) else data
                    df = df.dropna(how="all")
                except Exception:
                    df = None
                for r in eval_ticker(df, args.include_ml, ml_engine):
                    rows.append((market, t, r["screen"], r["n_signals"],
                                 r["avg_fwd5d"], r["hit_pct"], r["baseline"], r["edge"]))
                donerows.append((market, t))

            conn.executemany(
                "INSERT OR REPLACE INTO ticker_screen VALUES (?,?,?,?,?,?,?,?)", rows)
            conn.executemany("INSERT OR REPLACE INTO done VALUES (?,?)", donerows)
            conn.commit()
            print(f"  [{market}] {min(i+args.batch,len(todo))}/{len(todo)} processed",
                  file=sys.stderr, flush=True)

    write_summary(conn)
    summ = pd.read_sql("SELECT * FROM market_screen_summary", conn)
    print("\n=== SCREEN VIABILITY BY MARKET (full-universe, 5y) ===")
    print(summ.to_string(index=False))
    conn.close()


if __name__ == "__main__":
    main()
