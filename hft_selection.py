#!/usr/bin/env python3
"""
hft_selection.py
----------------
Operationalises the four HFT strategy archetypes (from the microstructure
literature survey) as a **stock picker built only from daily OHLC** — day's high,
low, close and volume — averaged over a 1-week window, across the liquid universe.

We have no tick/limit-order-book feed, so this is the **Tier-1 (universe/tradeability)
screen** each archetype implies, using well-established daily-data *proxies* for the
microstructure quantities they care about:

  Market making      stable, earnable spread + low toxicity  (Avellaneda-Stoikov 2008;
                     Ho-Stoll 1981). Proxies: Corwin-Schultz (2012) high-low spread,
                     spread stability (std of daily range), and a VPIN-style toxicity
                     proxy = the Kaufman efficiency ratio (net move / total travel;
                     trending = informed/toxic).
  Statistical arb    fast mean reversion (Avellaneda-Lee 2010). Proxies: negative
                     return autocorrelation + short Ornstein-Uhlenbeck half-life +
                     low efficiency ratio (choppy = reverting).
  Latency / order-   predictable, persistent flow. Proxies: high efficiency ratio
  anticipation       (directional persistence) + volume autocorrelation (predictable
                     order flow).
  Index/ETF arb      the mispriced leg of a real relationship. Proxies: high
                     correlation to the sector-peer basket (relationship) × current
                     standardised deviation from it (mispricing).

Each proxy is a pure function of daily bars (unit-tested). Archetype scores are
cross-sectional z-scores over the liquid names; the top names per archetype are the
picks. Reuses the liquid-universe filter from `liquidity_factor.py`.

Usage:
  python hft_selection.py --market US --window 5 --top 10
  python hft_selection.py --all --archetype market_making
"""

from __future__ import annotations

import argparse
import os
import sys
import warnings

import numpy as np
import pandas as pd

import marketdata

warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = os.path.expanduser("~/Downloads/code/python_files/cache_seed")
COMPANIES = os.path.join(HERE, "companies_industry.parquet")

WINDOW = 5           # 1 trading week — the range/spread block (the user's ask)
STAT_WINDOW = 20     # ~1 month for the serial-correlation proxies (5 pts is too few)
TRADEABLE_MAX_RANGE = 0.12   # >12% average daily range = penny/junk, not HFT-tradeable


# ── pure OHLC proxies ─────────────────────────────────────────────────────────
def daily_range_pct(high, low, close) -> np.ndarray:
    """Day's high-to-low as a fraction of close — the intraday spread/vol proxy."""
    h, l, c = (np.asarray(x, dtype=float) for x in (high, low, close))
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(c > 0, (h - l) / c, np.nan)


def avg_range(high, low, close) -> float:
    return float(np.nanmean(daily_range_pct(high, low, close)))


def range_stability(high, low, close) -> float:
    """Std of the daily range over the window — lower = more stable spread (MM likes)."""
    r = daily_range_pct(high, low, close)
    return float(np.nanstd(r))


def corwin_schultz_spread(high, low) -> float:
    """Corwin & Schultz (2012) high-low bid-ask spread estimator, averaged over the
    consecutive 2-day pairs in the window. Returns a proportional spread (>=0)."""
    h = np.asarray(high, dtype=float); l = np.asarray(low, dtype=float)
    if len(h) < 2:
        return np.nan
    k = 3 - 2 * np.sqrt(2)
    spreads = []
    for t in range(len(h) - 1):
        if l[t] <= 0 or l[t + 1] <= 0:
            continue
        beta = np.log(h[t] / l[t]) ** 2 + np.log(h[t + 1] / l[t + 1]) ** 2
        hi2 = max(h[t], h[t + 1]); lo2 = min(l[t], l[t + 1])
        if lo2 <= 0:
            continue
        gamma = np.log(hi2 / lo2) ** 2
        alpha = (np.sqrt(2 * beta) - np.sqrt(beta)) / k - np.sqrt(gamma / k)
        s = 2 * (np.exp(alpha) - 1) / (1 + np.exp(alpha))
        spreads.append(max(s, 0.0))            # negative estimates set to 0 (per the paper)
    return float(np.mean(spreads)) if spreads else np.nan


def efficiency_ratio(close) -> float:
    """Kaufman efficiency ratio = |net move| / total travel over the window, in [0,1].
    High = trending/predictable (informed, 'toxic' for a market maker; good for
    latency); low = choppy/mean-reverting (good for MM and stat-arb)."""
    c = np.asarray(close, dtype=float)
    travel = np.sum(np.abs(np.diff(c)))
    if travel <= 0:
        return np.nan
    return float(abs(c[-1] - c[0]) / travel)


def lag1_autocorr(x) -> float:
    """Lag-1 autocorrelation. Negative on returns = mean reversion; positive =
    persistence. On volume = predictable flow."""
    a = np.asarray(x, dtype=float)
    a = a[np.isfinite(a)]
    if len(a) < 3 or np.std(a) == 0:
        return np.nan
    return float(np.corrcoef(a[:-1], a[1:])[0, 1])


def ou_half_life(close) -> float:
    """Ornstein-Uhlenbeck mean-reversion half-life (in days) from an AR(1) fit on the
    price level: ΔP = a + b·P₋₁ ; half-life = −ln(2)/ln(1+b) for b<0. Returns +inf
    when not mean-reverting (b>=0)."""
    p = np.asarray(close, dtype=float)
    p = p[np.isfinite(p)]
    if len(p) < 4:
        return np.nan
    lag = p[:-1]; dp = np.diff(p)
    b = np.polyfit(lag, dp, 1)[0]              # slope of ΔP on P₋₁
    if b >= -1e-9 or (1 + b) <= 0:             # not (meaningfully) reverting -> no finite half-life
        return np.inf
    return float(-np.log(2) / np.log(1 + b))


# ── archetype scoring (cross-sectional) ───────────────────────────────────────
from marketdata import zscore as _z


def archetype_scores(feat: pd.DataFrame) -> pd.DataFrame:
    """Given per-stock features, add the four archetype scores (cross-sectional
    z-composites; higher = better fit for that archetype)."""
    f = feat.copy()
    # clip half-life to a sane range so ±inf doesn't dominate the z-score
    hl = f["half_life"].replace([np.inf, -np.inf], np.nan).clip(0, 60)
    # market makers want TIGHT, STABLE, LOW-TOXICITY names (you quote the liquid,
    # boring names and earn the rebate + small spread on volume) — not max spread,
    # which just selects junk. So reward low range, low range-instability, low toxicity.
    f["market_making"] = (-_z(f["avg_range%"]) - _z(f["range_stability"]) - _z(f["eff_ratio"])).round(3)
    f["stat_arb"] = (-_z(f["ret_autocorr"]) - _z(f["eff_ratio"]) - _z(hl)).round(3)
    f["latency"] = (_z(f["eff_ratio"]) + _z(f["vol_autocorr"]) + _z(f["ret_autocorr"])).round(3)
    if "peer_corr" in f and f["peer_corr"].notna().any():
        f["etf_arb"] = (_z(f["peer_corr"]) + _z(f["peer_dev"].abs())).round(3)
    else:
        f["etf_arb"] = np.nan
    return f


ARCHETYPES = ["market_making", "stat_arb", "latency", "etf_arb"]


# ── data assembly (offline, daily OHLC) ───────────────────────────────────────
def _sector_map() -> dict:
    if not os.path.exists(COMPANIES):
        return {}
    c = pd.read_parquet(COMPANIES)[["ticker", "sector"]]
    c["key"] = c["ticker"].astype(str).str.split(".").str[0].str.upper()
    return dict(zip(c["key"], c["sector"]))


def scan_market(market: str, window: int = WINDOW, stat_window: int = STAT_WINDOW) -> pd.DataFrame:
    """Build the per-stock feature table for one market's liquid names."""
    import liquidity_factor as lf
    import pead_factor as pf
    close, vol = lf._market_wide(market)
    if close is None:
        return pd.DataFrame()
    px = pd.read_parquet(os.path.join(SEED, f"cleaned_long_{market}.parquet"))
    high = px.pivot_table(index="Date", columns="Symbol", values="High", aggfunc="last").astype(float)
    low = px.pivot_table(index="Date", columns="Symbol", values="Low", aggfunc="last").astype(float)
    symbols = pf._liquid_symbols(close, vol)
    rets = close.pct_change(fill_method=None)
    sectors = _sector_map()

    # sector basket daily returns (for ETF-arb peer relationship)
    key = lambda s: str(s).split(".")[0].upper()
    sec_of = {s: sectors.get(key(s), "Unknown") for s in symbols}
    basket = {}
    for sec in set(sec_of.values()):
        members = [s for s in symbols if sec_of[s] == sec]
        if len(members) >= 3:
            basket[sec] = rets[members].mean(axis=1)

    rows = []
    for s in symbols:
        c = close[s].dropna()
        if len(c) < stat_window + 2:
            continue
        cw = c.tail(window); hw = high[s].reindex(cw.index); lw = low[s].reindex(cw.index)
        avg_r = avg_range(hw, lw, cw)
        if not np.isfinite(avg_r) or avg_r > TRADEABLE_MAX_RANGE:
            continue                            # penny/junk — not HFT-tradeable at any archetype
        rr = rets[s].tail(stat_window); vw = vol[s].tail(stat_window)
        feat = {
            "market": market, "ticker": s,
            "avg_range%": round(avg_r * 100, 3),
            "cs_spread": corwin_schultz_spread(hw, lw),
            "range_stability": range_stability(hw, lw, cw),
            "eff_ratio": efficiency_ratio(cw),
            "ret_autocorr": lag1_autocorr(rr.values),
            "vol_autocorr": lag1_autocorr(vw.values),
            "half_life": ou_half_life(c.tail(stat_window).values),
        }
        # ETF-arb: correlation to sector basket + current standardized deviation
        sec = sec_of[s]
        if sec in basket:
            b = basket[sec].reindex(rr.index)
            if b.notna().sum() > 5 and rr.std() > 0 and b.std() > 0:
                feat["peer_corr"] = float(rr.corr(b))
                feat["peer_dev"] = float((cw.iloc[-1] / cw.iloc[0] - 1) -
                                         (1 + b.tail(window)).prod() + 1)
        rows.append(feat)
    return pd.DataFrame(rows)


def pick(market_scan: pd.DataFrame, archetype: str, top: int) -> pd.DataFrame:
    cols = ["market", "ticker", "avg_range%", "cs_spread", "range_stability",
            "eff_ratio", "ret_autocorr", "vol_autocorr", "half_life", archetype]
    d = market_scan.dropna(subset=[archetype]).sort_values(archetype, ascending=False)
    return d[[c for c in cols if c in d.columns]].head(top)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default=None, help="market code, e.g. US, JP; default: all")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--window", type=int, default=WINDOW, help="range/spread window (trading days)")
    ap.add_argument("--archetype", choices=ARCHETYPES, default=None,
                    help="show only one archetype (default: all four)")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    markets = (marketdata.market_list()
               if (args.all or not args.market) else [args.market])
    scans = [scan_market(m, args.window) for m in markets]
    scans = [s for s in scans if not s.empty]
    if not scans:
        raise SystemExit("no data (missing price parquets?)")
    feat = archetype_scores(pd.concat(scans, ignore_index=True))

    tag = ", ".join(markets) if len(markets) <= 3 else f"{len(markets)} markets"
    print(f"\n=== HFT-ARCHETYPE STOCK PICKS — {tag}, {args.window}-day (1-week) window, "
          f"{feat['ticker'].nunique()} liquid names ===")
    show = [args.archetype] if args.archetype else ARCHETYPES
    labels = {"market_making": "MARKET MAKING (stable earnable spread, low toxicity)",
              "stat_arb": "STATISTICAL ARB (fast mean reversion)",
              "latency": "LATENCY / ORDER-ANTICIPATION (predictable, persistent flow)",
              "etf_arb": "INDEX/ETF ARB (mispriced leg of a peer relationship)"}
    for a in show:
        picks = pick(feat, a, args.top)
        if picks.empty:
            print(f"\n-- {labels[a]}: no eligible names --"); continue
        print(f"\n-- {labels[a]} --")
        print(f"  {'mkt':4}{'ticker':13}{'avgRng%':>8}{'spread':>8}{'effR':>7}"
              f"{'retAC':>7}{'volAC':>7}{'halflife':>9}{'score':>7}")
        for _, r in picks.iterrows():
            hl = r['half_life']
            hl_s = "inf" if not np.isfinite(hl) else f"{hl:.1f}"
            print(f"  {str(r['market']):4}{str(r['ticker']):13}{r['avg_range%']:>8.2f}"
                  f"{(r['cs_spread'] or 0)*100:>8.3f}{r['eff_ratio']:>7.2f}"
                  f"{r['ret_autocorr']:>7.2f}{r['vol_autocorr']:>7.2f}{hl_s:>9}{r[a]:>7.2f}")
    print("\n  Tier-1 (universe/tradeability) proxies from daily OHLC only — no tick/LOB feed.")


if __name__ == "__main__":
    main()
