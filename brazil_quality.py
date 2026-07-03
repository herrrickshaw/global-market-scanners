#!/usr/bin/env python3
"""
brazil_quality.py  —  a POINT-IN-TIME quality test on Brazil, built entirely from
the CVM filed-date fundamentals (brazil_cvm.py) joined to B3 price data.

This is the pay-off of the L1 remediation: instead of a snapshot of today's
fundamentals, quality (ROE) is measured only from filings whose receipt date
(DT_RECEB) precedes the return window — a genuine look-ahead-free test on a
non-US market.

Pipeline:
  1. filed_panel(year) for a few years  -> filed-date fundamentals (brazil_cvm)
  2. quality_asof(panel, T)             -> per-company ROE known at T (plausible only)
  3. to_tickers(., TICKER_MAP)          -> map CD_CVM -> B3 ticker
  4. forward_returns(close, T, horizon) -> realised return AFTER T
  5. quality_quintiles(quality, fwd)    -> IC, top-minus-bottom spread, monotonicity

Pure functions (steps 2,3,5) are unit-tested; the CLI runs the live join.
Caveat: the local BR price cache is ~1 year, so this is a short-horizon
demonstration (the paper's L5 point) — the *machinery*, not the magnitude, is the
contribution. Not investment advice.
"""
from __future__ import annotations

# CD_CVM -> most-liquid B3 ticker (.SA). EVERY entry was verified by matching the
# CVM registration code to the filing's company name (see the module test) — hand-
# guessing codes silently mislabels data (e.g. CD 11592 is Unipar, not CSN), so the
# map contains only name-confirmed pairs. Codes not present in a given price cache
# simply drop at join time.
TICKER_MAP = {
    # verified present in the current BR cache
    "4170": "VALE3.SA",  "1023": "BBAS3.SA",  "23264": "ABEV3.SA", "5410": "WEGE3.SA",
    "21610": "B3SA3.SA", "8133": "LREN3.SA",  "22470": "MGLU3.SA", "13986": "SUZB3.SA",
    "17329": "EGIE3.SA", "20010": "EQTL3.SA", "18660": "CPFE3.SA", "19836": "CSAN3.SA",
    "14460": "CYRE3.SA", "22187": "PRIO3.SA", "16659": "PSSA3.SA",
    # verified-correct mega-caps absent from this particular cache (join when present)
    "9512": "PETR4.SA",  "19348": "ITUB4.SA", "906": "BBDC4.SA",
}


def quality_asof(panel, asof, quality_key="roe") -> dict:
    """{cd_cvm: quality} using only filings known at `asof` (pit_panel.as_of),
    keeping plausible values only (filters the financial-taxonomy outliers)."""
    import pit_panel as pit
    from brazil_cvm import plausible_roe
    view = pit.as_of(panel, asof, symbol_col="cd_cvm", filed_col="filed_date")
    out = {}
    for cd, row in view.items():
        v = row.get(quality_key)
        if quality_key == "roe" and not plausible_roe(v):
            continue
        if v is not None:
            out[cd] = v
    return out


def _norm_cd(cd) -> str:
    """CVM codes appear zero-padded ('009512') or bare ('9512'); normalise to bare."""
    return str(cd).strip().lstrip("0") or "0"


def to_tickers(quality_by_cd: dict, ticker_map: dict = TICKER_MAP,
               valid_tickers=None) -> dict:
    """Map {cd_cvm: quality} -> {ticker: quality}, tolerant of zero-padding. If
    valid_tickers is given, keep only tickers present in the price universe."""
    norm_map = {_norm_cd(k): v for k, v in ticker_map.items()}
    out = {}
    for cd, q in quality_by_cd.items():
        t = norm_map.get(_norm_cd(cd))
        if t and (valid_tickers is None or t in valid_tickers):
            out[t] = q
    return out


def forward_returns(close, start, horizon_days: int | None = None) -> dict:
    """Realised return per ticker from the first trading day >= `start` to
    `horizon_days` later (or the last available day if horizon is None)."""
    import pandas as pd
    idx = close.index
    start = pd.Timestamp(start)
    pos0 = idx.searchsorted(start)
    if pos0 >= len(idx):
        return {}
    pos1 = min(len(idx) - 1, pos0 + horizon_days) if horizon_days else len(idx) - 1
    p0, p1 = close.iloc[pos0], close.iloc[pos1]
    out = {}
    for t in close.columns:
        a, b = p0.get(t), p1.get(t)
        if a and b and a == a and b == b and a > 0:      # non-NaN, positive
            out[t] = b / a - 1.0
    return out


def quality_quintiles(quality: dict, fwd: dict, q: int = 5) -> dict:
    """Join quality with forward returns, sort into q buckets, and report the
    top-minus-bottom spread, rank information coefficient, and monotonicity."""
    import numpy as np
    import pandas as pd
    from marketdata import information_coefficient, monotonicity
    keys = [k for k in quality if k in fwd]
    df = pd.DataFrame({"q": [quality[k] for k in keys], "r": [fwd[k] for k in keys]},
                      index=keys).dropna()
    n = len(df)
    if n < q:
        return {"n": n, "note": "too few names to bucket"}
    df["bucket"] = pd.qcut(df["q"].rank(method="first"), q, labels=False) + 1
    means = df.groupby("bucket")["r"].mean()
    ic = information_coefficient(df["q"], df["r"])
    curve = means.rename("r").reset_index()
    return {
        "n": n,
        "IC": round(float(ic), 4),
        "Q1_ret%": round(float(means.get(1, np.nan)) * 100, 2),
        "Q5_ret%": round(float(means.get(q, np.nan)) * 100, 2),
        "Q5_minus_Q1%": round(float(means.get(q, np.nan) - means.get(1, np.nan)) * 100, 2),
        "monotonicity": round(float(monotonicity(curve, "r")), 3),
    }


def main():
    import sys
    print("brazil_quality.py — point-in-time quality (ROE) test on Brazil\n")
    try:
        import brazil_cvm as cvm
        import marketdata as md
        years = [int(y) for y in (sys.argv[1:] or ["2022", "2023", "2024"])]
        panel = []
        for y in years:
            try:
                panel += cvm.filed_panel(y, "DFP")
            except Exception as e:
                print(f"  (skip {y}: {e})")
        close, _ = md.close_volume("BR")
        asof = str(close.index.min().date())                 # measure quality as of price-window start
        quality_cd = quality_asof(panel, asof)
        quality_t = to_tickers(quality_cd, valid_tickers=set(close.columns))
        fwd = forward_returns(close, asof, horizon_days=126)  # ~6 months forward
        res = quality_quintiles(quality_t, fwd, q=5)
        print(f"  fundamentals: {len(panel)} filings over {years}")
        print(f"  quality known as of {asof}: {len(quality_cd)} companies "
              f"({len(quality_t)} mapped to liquid B3 tickers)")
        print(f"  point-in-time quality quintiles (6-month forward): {res}")
        print("\n  (short 1-year price cache => demonstration of the PIT machinery, "
              "not a powered magnitude.)")
    except Exception as e:
        print(f"  (live run unavailable: {e})")


if __name__ == "__main__":
    main()
