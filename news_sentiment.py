#!/usr/bin/env python3
"""
news_sentiment.py
-----------------
Closes the scout's 'text_nlp' gap: news-headline sentiment using a compact
**Loughran-McDonald-style finance lexicon** (Loughran & McDonald 2011 showed generic
sentiment dictionaries misclassify financial text, so finance-specific word lists are
used). Headlines come from yfinance `.news`.

The scorer is pure and unit-tested; the fetch degrades gracefully offline. This is a
lexicon method (transparent, keyless) — not an LLM — so it's fast and reproducible;
the full LM master dictionary (~4k words) can be dropped in to replace the compact
lists below.

Usage:
  python news_sentiment.py --tickers AAPL,MSFT,NVDA
  python news_sentiment.py --market US --top 20      # liquid US names (slower)
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

SEED = os.path.expanduser("~/Downloads/code/python_files/cache_seed")

POSITIVE = {
    "beat", "beats", "exceeded", "exceed", "exceeds", "growth", "profit", "profitable",
    "gain", "gains", "surge", "surged", "upgrade", "upgraded", "strong", "record",
    "outperform", "rally", "rallied", "boost", "boosted", "wins", "won", "award",
    "awarded", "expansion", "expand", "improve", "improved", "higher", "rise", "rose",
    "positive", "robust", "opportunity", "breakthrough", "dividend", "buyback",
    "raised", "tops", "jumps", "soars", "soared", "approval", "approved", "milestone",
}
NEGATIVE = {
    "miss", "missed", "misses", "loss", "losses", "decline", "declined", "drop",
    "dropped", "fall", "fell", "weak", "downgrade", "downgraded", "lawsuit", "sued",
    "fraud", "investigation", "probe", "bankruptcy", "default", "cut", "cuts",
    "warning", "warn", "warned", "plunge", "plunged", "slump", "slumped", "concern",
    "concerns", "risk", "risks", "delay", "delayed", "halt", "halted", "resign",
    "resigned", "layoff", "layoffs", "recall", "deficit", "shortfall", "negative",
    "distress", "impairment", "writedown", "dilution", "penalty", "fine", "fined",
    "breach", "disappointing", "sluggish", "headwind", "headwinds", "probe", "delist",
}


# ── pure sentiment core ───────────────────────────────────────────────────────
def tokenize(text: str) -> list:
    return re.findall(r"[a-z]+", (text or "").lower())


def score_text(text: str, positive=POSITIVE, negative=NEGATIVE) -> dict:
    """Net finance sentiment of a text: (pos−neg)/(pos+neg) ∈ [−1,1], plus counts."""
    toks = tokenize(text)
    p = sum(1 for t in toks if t in positive)
    n = sum(1 for t in toks if t in negative)
    tot = p + n
    return {"pos": p, "neg": n, "sentiment": round((p - n) / tot, 3) if tot else 0.0}


def score_headlines(headlines) -> dict:
    """Aggregate sentiment over a list of headlines (mean of per-headline scores)."""
    scores = [score_text(h)["sentiment"] for h in headlines if h]
    hits = sum(score_text(h)["pos"] + score_text(h)["neg"] for h in headlines if h)
    return {"n_headlines": len(scores), "sentiment": round(float(np.mean(scores)), 3) if scores else 0.0,
            "lexicon_hits": hits}


# ── fetch (yfinance news, graceful) ───────────────────────────────────────────
def fetch_news_yf(ticker: str, limit: int = 15) -> list:
    import apiclient
    import yfinance as yf
    try:
        items = apiclient.robust("yfinance", lambda: yf.Ticker(ticker).news, retries=2) or []
    except Exception:
        return []
    out = []
    for it in items[:limit]:
        # yfinance schema varies: title at top-level or under 'content'
        title = it.get("title") or (it.get("content") or {}).get("title")
        if title:
            out.append(title)
    return out


def _universe(args) -> list:
    if args.tickers:
        return [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    import liquidity_factor as lf, pead_factor as pf
    close, vol = lf._market_wide(args.market or "US")
    return pf._liquid_symbols(close, vol)[:args.top]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default=None, help="comma-separated, e.g. AAPL,MSFT")
    ap.add_argument("--market", default="US")
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()

    rows = []
    for tk in _universe(args):
        heads = fetch_news_yf(tk)
        agg = score_headlines(heads)
        if agg["n_headlines"]:
            rows.append({"ticker": tk, **agg})
    if not rows:
        print("no news fetched (offline?) — the scorer is unit-tested; try --tickers AAPL"); return
    df = pd.DataFrame(rows).sort_values("sentiment", ascending=False)
    print(f"\n=== NEWS-HEADLINE SENTIMENT (Loughran-McDonald lexicon) — {len(df)} names ===")
    print(f"  {'ticker':10}{'sentiment':>11}{'headlines':>11}{'lex_hits':>10}")
    for _, r in df.iterrows():
        print(f"  {str(r['ticker']):10}{r['sentiment']:>11.2f}{int(r['n_headlines']):>11}"
              f"{int(r['lexicon_hits']):>10}")
    print("\n  sentiment ∈ [−1,+1]; +1 = uniformly positive finance-word headlines.")


if __name__ == "__main__":
    main()
