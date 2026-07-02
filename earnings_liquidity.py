#!/usr/bin/env python3
"""
earnings_liquidity.py
---------------------
Ties three of the platform's building blocks together into one study: does a stock's
**liquidity, traded volume and price** condition the market's reaction to and drift
after **quarterly announcements / results** (the post-earnings-announcement drift,
PEAD)?

The classic finding (Chordia, Goyal, Sadka, Sadka 2009; Sadka 2006) is that PEAD is
**stronger in illiquid, low-volume, smaller/lower-priced stocks** — because trading
frictions are the limit-to-arbitrage that lets the drift persist. This module tests
that on the platform's data.

Method (reusing pead_factor + liquidity_factor, all point-in-time / look-ahead-free):
  * detect earnings-announcement proxy events (volume spike + return jump) per stock;
  * at each event measure PRE-event **Amihud illiquidity**, average **dollar-volume**,
    **price** level, and the announcement-day **volume surge**;
  * measure the **surprise** (event-window CAR) and the **PEAD drift** (post-event CAR);
  * compute the *directional* drift = drift × sign(surprise) — how far price continues
    in the surprise's direction — and compare it across liquidity / volume / price
    quantiles.

Usage:
  python earnings_liquidity.py --market US
  python earnings_liquidity.py --all --horizon 40
"""

from __future__ import annotations

import argparse
import os
import sys
import warnings

import numpy as np
import pandas as pd

from pead_factor import detect_events, market_adjust, event_surprise, car, DAILY_CLIP, SURPRISE_CAP
from liquidity_factor import amihud_illiq

warnings.filterwarnings("ignore")

SEED = os.path.expanduser("~/Downloads/code/python_files/cache_seed")
PRE = 42             # pre-event window for liquidity/volume
HORIZON = 40         # post-event drift window


# ── pure study core ───────────────────────────────────────────────────────────
def directional_drift(surprise: float, fwd_car: float) -> float:
    """PEAD magnitude in the surprise's own direction: drift × sign(surprise).
    Positive = price kept moving the way the surprise pointed (the drift)."""
    if not (np.isfinite(surprise) and np.isfinite(fwd_car)) or surprise == 0:
        return np.nan
    return float(fwd_car * np.sign(surprise))


def bucket_stats(panel: pd.DataFrame, by: str, val: str, q: int = 5,
                 labels=None) -> pd.DataFrame:
    """Mean/median of `val` across quantiles of `by` — the comparison table."""
    d = panel.dropna(subset=[by, val]).copy()
    if len(d) < q * 3:
        return pd.DataFrame()
    lab = labels or [f"Q{i}" for i in range(1, q + 1)]
    d["bucket"] = pd.qcut(d[by], q, labels=lab, duplicates="drop")
    g = d.groupby("bucket")[val].agg(mean="mean", median="median", n="count").reset_index()
    g[f"{val}_mean%"] = (g["mean"] * 100).round(2)
    g[f"{val}_med%"] = (g["median"] * 100).round(2)
    return g[["bucket", f"{val}_mean%", f"{val}_med%", "n"]]


def spread_qhigh_qlow(table: pd.DataFrame, col: str) -> float:
    """Top-minus-bottom quantile difference (e.g. illiquid − liquid)."""
    return round(float(table[col].iloc[-1] - table[col].iloc[0]), 2) if not table.empty else np.nan


def per_market_summary(panel_by_market: dict, min_events: int = 40) -> pd.DataFrame:
    """One row per country: event count, announcement volume surge, and the
    information coefficient of directional PEAD drift vs illiquidity / volume / price.
    Reveals WHERE the liquidity-conditioning of PEAD holds across the testing universe."""
    from accumulation_screener import information_coefficient
    rows = []
    for m, p in panel_by_market.items():
        p = p[p["dir_drift"].abs() < 2.0] if not p.empty else p
        if len(p) < min_events:
            continue
        rows.append({"market": m, "events": len(p),
                     "vol_surge×": round(float(p["vol_surge"].median()), 1),
                     "illiq_IC": round(information_coefficient(p["illiq"], p["dir_drift"]), 3),
                     "vol_IC": round(information_coefficient(p["dollar_vol"], p["dir_drift"]), 3),
                     "price_IC": round(information_coefficient(p["price"], p["dir_drift"]), 3)})
    return pd.DataFrame(rows).sort_values("illiq_IC", ascending=False) if rows else pd.DataFrame()


# ── data assembly (offline, point-in-time) ────────────────────────────────────
def _wide(market: str):
    import marketdata
    return marketdata.wide(market)


def parse_submissions(payload: dict, forms=("10-Q", "10-K")) -> list:
    """Pure: extract sorted, unique filing dates for the given forms from an EDGAR
    company-submissions JSON (the real quarterly/annual results-announcement dates)."""
    rec = payload.get("filings", {}).get("recent", {})
    fs = rec.get("form", []); ds = rec.get("filingDate", [])
    return sorted({ds[i] for i in range(min(len(fs), len(ds))) if fs[i] in forms})


_CIK = None


def _cik_map():
    global _CIK
    if _CIK is None:
        try:
            import pit_fundamentals as pf
            _CIK = pf._ticker_cik()
        except Exception:
            _CIK = {}
    return _CIK


def fetch_earnings_dates_edgar(ticker: str, since: str = "2000-01-01") -> list:
    """AUTHORITATIVE US earnings-announcement dates: 10-Q/10-K filing dates from EDGAR
    submissions (governed; SEC needs a contact-style User-Agent). Empty on offline."""
    import apiclient
    cik = _cik_map().get(ticker.upper())
    if not cik:
        return []
    url = f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json"
    ua = {"User-Agent": os.environ.get("SEC_UA", "global-market-scanners research admin@gms.dev")}
    try:
        r = apiclient.http_get("edgar", url, headers=ua, retries=2)
        if r.status_code != 200:
            return []
        return [d for d in parse_submissions(r.json()) if d >= since]
    except Exception:
        return []


def scan_us_edgar(tickers, horizon: int = HORIZON, pre: int = 21) -> pd.DataFrame:
    """Same conditioning panel as scan_market, but events are REAL EDGAR 10-Q/10-K
    filing dates (US) rather than the volume-spike proxy."""
    import pead_factor as pf
    w = _wide("US")
    if w is None:
        return pd.DataFrame()
    close, high, low, vol = w["Close"], w["High"], w["Low"], w["Volume"]
    liquid = pf._liquid_symbols(close, vol)
    mkt = close[liquid].pct_change(fill_method=None).mean(axis=1)
    rows = []
    for s in tickers:
        if s not in close.columns:
            continue
        c = close[s].dropna()
        if len(c) < pre + horizon + 10:
            continue
        idx = c.index
        dates = fetch_earnings_dates_edgar(s, since=str(idx[0].date()))
        if not dates:
            continue
        v = vol[s].reindex(idx); dv = c * v
        r = c.pct_change()
        abn = market_adjust(r, mkt.reindex(idx)).clip(-DAILY_CLIP, DAILY_CLIP)
        for d in dates:
            ev = int(idx.searchsorted(pd.Timestamp(d)))     # first trading day on/after the filing
            if ev >= len(idx) or ev < pre or ev + horizon > len(c) - 1:
                continue
            surprise = event_surprise(abn, ev)
            if surprise == 0 or abs(surprise) > SURPRISE_CAP:
                continue
            fwd = car(abn, ev + 2, ev + horizon)
            pre_illiq = amihud_illiq(r.iloc[ev - pre:ev].values, dv.iloc[ev - pre:ev].values)
            pre_dv = float(np.nanmean(dv.iloc[ev - pre:ev].values))
            vsurge = float(v.iloc[ev] / np.nanmean(v.iloc[ev - pre:ev].values)) \
                if np.nanmean(v.iloc[ev - pre:ev].values) > 0 else np.nan
            rows.append({"market": "US", "ticker": s, "date": str(idx[ev].date()),
                         "price": float(c.iloc[ev]), "illiq": pre_illiq, "dollar_vol": pre_dv,
                         "vol_surge": vsurge, "surprise": surprise, "fwd_car": fwd,
                         "dir_drift": directional_drift(surprise, fwd)})
    return pd.DataFrame(rows)


def scan_market(market: str, horizon: int = HORIZON, pre: int = PRE) -> pd.DataFrame:
    import pead_factor as pf
    w = _wide(market)
    if w is None:
        return pd.DataFrame()
    close, high, low, vol = w["Close"], w["High"], w["Low"], w["Volume"]
    symbols = pf._liquid_symbols(close, vol)
    mkt = close[symbols].pct_change(fill_method=None).mean(axis=1)
    rows = []
    for s in symbols:
        c = close[s].dropna()
        if len(c) < pre + horizon + 25:
            continue
        idx = c.index
        v = vol[s].reindex(idx); dv = (c * v)
        r = c.pct_change()
        abn = market_adjust(r, mkt.reindex(idx)).clip(-DAILY_CLIP, DAILY_CLIP)
        for ev in detect_events(c, v):
            if ev < pre or ev + horizon > len(c) - 1:
                continue
            surprise = event_surprise(abn, ev)
            if abs(surprise) > SURPRISE_CAP or surprise == 0:
                continue
            fwd = car(abn, ev + 2, ev + horizon)
            pre_illiq = amihud_illiq(r.iloc[ev - pre:ev].values, dv.iloc[ev - pre:ev].values)
            pre_dv = float(np.nanmean(dv.iloc[ev - pre:ev].values))
            vsurge = float(v.iloc[ev] / np.nanmean(v.iloc[ev - pre:ev].values)) \
                if np.nanmean(v.iloc[ev - pre:ev].values) > 0 else np.nan
            rows.append({"market": market, "ticker": s,
                         "price": float(c.iloc[ev]), "illiq": pre_illiq,
                         "dollar_vol": pre_dv, "vol_surge": vsurge,
                         "surprise": surprise, "fwd_car": fwd,
                         "dir_drift": directional_drift(surprise, fwd)})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--horizon", type=int, default=HORIZON)
    ap.add_argument("--by-market", action="store_true",
                    help="test each country separately (per-market PEAD-liquidity IC table)")
    ap.add_argument("--edgar", action="store_true",
                    help="US: use REAL EDGAR 10-Q/10-K filing dates instead of the volume proxy")
    ap.add_argument("--limit", type=int, default=120, help="--edgar: liquid US names to query")
    args = ap.parse_args()

    # ── real earnings dates (US, SEC EDGAR) vs the volume-spike proxy ────────────
    if args.edgar:
        import pead_factor as pf
        w = _wide("US"); close, vol = w["Close"], w["Volume"]
        liquid = pf._liquid_symbols(close, vol)[:args.limit]
        print(f"fetching EDGAR 10-Q/10-K filing dates for {len(liquid)} liquid US names…",
              file=sys.stderr)
        panel = scan_us_edgar(liquid, args.horizon)
        panel = panel[panel["dir_drift"].abs() < 2.0] if not panel.empty else panel
        if panel.empty:
            print("no EDGAR-dated events (offline, or no filings in the price window)"); return
        from accumulation_screener import information_coefficient
        t = bucket_stats(panel, "illiq", "dir_drift")
        ic = information_coefficient(panel["illiq"], panel["dir_drift"])
        print(f"\n=== US PEAD × LIQUIDITY on REAL EDGAR filing dates "
              f"({len(panel)} 10-Q/10-K events) ===")
        if not t.empty:
            meds = " ".join(f"{b}={m:+.1f}" for b, m in zip(t["bucket"], t["dir_drift_med%"]))
            print(f"  directional drift by illiquidity quintile (Q1=liquid…Q5=illiquid): {meds}")
            print(f"  Q5−Q1 = {spread_qhigh_qlow(t,'dir_drift_med%'):+.2f}%")
        print(f"  illiq_IC (real dates) = {ic:+.3f}   vs proxy US ≈ +0.010")
        print(f"  announcement-day volume surge: median {panel['vol_surge'].median():.1f}× "
              f"(vs proxy 3.7×)")
        print("\n  Real filing dates remove the proxy's noise (volume spikes that aren't earnings);"
              "\n  a cleaner, higher illiq_IC here = the liquidity-conditioning of PEAD confirmed.")
        return

    markets = (marketdata.market_list()
               if (args.all or not args.market) else [args.market])

    # ── per-country breakdown (expand the testing universe) ─────────────────────
    if args.by_market:
        by = {m: scan_market(m, args.horizon) for m in markets}
        summ = per_market_summary(by)
        print(f"\n=== EARNINGS × LIQUIDITY — PER-COUNTRY test ({len(summ)} markets) ===")
        print("    illiq_IC = corr(pre-event illiquidity, directional PEAD drift);")
        print("    POSITIVE = PEAD stronger in illiquid names (Chordia-Sadka holds there).")
        if summ.empty:
            print("  insufficient events per market"); return
        print(f"  {'mkt':4}{'events':>7}{'volSurge×':>10}{'illiq_IC':>9}{'vol_IC':>8}{'price_IC':>9}")
        for _, r in summ.iterrows():
            print(f"  {str(r['market']):4}{int(r['events']):>7}{r['vol_surge×']:>10.1f}"
                  f"{r['illiq_IC']:>9.3f}{r['vol_IC']:>8.3f}{r['price_IC']:>9.3f}")
        pos = summ[summ["illiq_IC"] > 0]
        print(f"\n  {len(pos)}/{len(summ)} markets show the Chordia-Sadka sign (illiq_IC>0); "
              f"strongest: {', '.join(summ['market'].head(3))}")
        return

    panel = pd.concat([scan_market(m, args.horizon) for m in markets], ignore_index=True)
    panel = panel[panel["dir_drift"].abs() < 2.0] if not panel.empty else panel
    if panel.empty:
        raise SystemExit("no earnings-event panel (missing price parquets?)")

    tag = ", ".join(markets) if len(markets) <= 3 else f"{len(markets)} markets"
    print(f"\n=== EARNINGS × LIQUIDITY/VOLUME/PRICE — PEAD conditioning study ===")
    print(f"    {tag}  ·  {len(panel)} announcement-proxy events  ·  {args.horizon}d drift")
    print(f"    directional drift = post-event CAR × sign(surprise) (higher = stronger PEAD)")

    from accumulation_screener import information_coefficient

    def _show(by, label, ascending_desc):
        t = bucket_stats(panel, by, "dir_drift")
        if t.empty:
            return
        meds = " ".join(f"{b}={m:+.1f}" for b, m in zip(t["bucket"], t["dir_drift_med%"]))
        print(f"\n  by {label} (Q1={ascending_desc[0]} … Q5={ascending_desc[1]}):")
        print(f"    median dir-drift: {meds}")
        print(f"    Q5−Q1 = {spread_qhigh_qlow(t,'dir_drift_med%'):+.2f}%   "
              f"IC(signal,dir_drift) = {information_coefficient(panel[by], panel['dir_drift']):+.3f}")

    _show("illiq", "Amihud ILLIQUIDITY", ("liquid", "illiquid"))
    _show("dollar_vol", "dollar VOLUME", ("low-vol", "high-vol"))
    _show("price", "PRICE level", ("low-price", "high-price"))

    # announcement-day volume surge summary
    vs = panel["vol_surge"].dropna()
    print(f"\n  announcement-day volume surge: median {vs.median():.1f}× pre-event average "
          f"(p90 {vs.quantile(0.9):.1f}×)")
    print("\n  Hypothesis (Chordia-Sadka): PEAD is stronger in ILLIQUID / LOW-VOLUME / "
          "lower-priced names\n  — i.e. dir-drift should RISE with illiquidity and FALL "
          "with volume/price.")


if __name__ == "__main__":
    main()
