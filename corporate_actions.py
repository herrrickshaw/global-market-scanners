#!/usr/bin/env python3
"""
corporate_actions.py
--------------------
Two corporate-action screeners over the daily OHLC universe:

  STOCK SPLIT   — AUTHORITATIVE via yfinance `.splits`: fetch actual splits for the
                  most-liquid names per market and list those within the data window
                  (default). This is reliable — real splits are ADJUSTED OUT of the
                  close, so you cannot detect them from adjusted prices. An offline
                  price-gap heuristic (`--heuristic`) remains for *unadjusted* feeds,
                  but on adjusted data a −33%/−50% crash collides with 3:2/2:1 and it
                  mostly produces false positives.

  RIGHTS ISSUE  — a rights issue dilutes holders: on the ex-rights date the price gaps
                  DOWN by a discount, idiosyncratically, on heavy volume, then settles
                  at the diluted level. HONEST LIMIT: from daily OHLC this signature is
                  indistinguishable from an earnings repricing or a secondary offering,
                  so the output is a 'sharp idiosyncratic dilution/repricing candidate'
                  list — a true rights issue needs the exchange's corporate-action feed
                  (NSE/BSE announcements, 8-K) to confirm.

Usage:
  python corporate_actions.py --action split --market US          # yfinance, authoritative
  python corporate_actions.py --action split --market US --heuristic   # offline gap detector
  python corporate_actions.py --action rights --market US
  python corporate_actions.py --action both --all
"""

from __future__ import annotations

import argparse
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = os.path.expanduser("~/Downloads/code/python_files/cache_seed")

# known split factors -> price multiplier the close moves by (forward = <1, reverse = >1)
SPLIT_FACTORS = {"2:1": 0.5, "3:1": 1 / 3, "3:2": 2 / 3, "4:1": 0.25, "5:1": 0.2,
                 "10:1": 0.1, "1:2": 2.0, "1:3": 3.0, "1:5": 5.0, "1:10": 10.0}
SPLIT_TOL = 0.03          # close ratio must be within 3% of a clean factor
MIN_GAP = 0.08            # ignore drops to ~0 (glitches), below any real 10:1
VOL_MULT = 1.3            # forward-split day volume vs trailing average
RIGHTS_BAND = (-0.35, -0.08)   # plausible ex-rights discount (deeper = crash, not a rights issue)
RIGHTS_MKT_MARGIN = 0.08       # stock must fall this much more than the market (idiosyncratic)
RIGHTS_STABILISE = 0.15        # price settles near the diluted level (|c[i+3]/c[i]−1| < this)


# ── pure detectors ────────────────────────────────────────────────────────────
def nearest_split_ratio(gap: float, tol: float = SPLIT_TOL):
    """If the close ratio `gap` (=curr/prev) is within `tol` (relative) of a known
    split factor, return (label, factor); else None."""
    if not np.isfinite(gap) or gap <= 0:
        return None
    best = None
    for label, f in SPLIT_FACTORS.items():
        if abs(gap - f) / f <= tol:
            d = abs(gap - f) / f
            if best is None or d < best[2]:
                best = (label, f, d)
    return (best[0], best[1]) if best else None


def detect_splits(close, volume, dates=None, tol: float = SPLIT_TOL) -> list:
    """Flag candidate split days: close ratio near a clean factor + volume confirms
    (forward -> up, reverse -> down) + not a glitch + the move persists (a split does
    not bounce back the next day)."""
    c = np.asarray(close, dtype=float); v = np.asarray(volume, dtype=float)
    out = []
    for i in range(21, len(c)):
        if not (np.isfinite(c[i]) and np.isfinite(c[i - 1]) and c[i - 1] > 0):
            continue
        gap = c[i] / c[i - 1]
        if gap < MIN_GAP and gap < 0.4:                 # crude glitch guard for deep drops
            if gap < MIN_GAP:
                continue
        hit = nearest_split_ratio(gap, tol)
        if not hit:
            continue
        label, factor = hit
        vol_avg = np.nanmean(v[max(0, i - 21):i])
        forward = factor < 1
        vol_ok = (v[i] >= VOL_MULT * vol_avg) if forward else (v[i] <= vol_avg / VOL_MULT)
        # persistence: next day should not undo the move (a real split is permanent)
        persists = True
        if i + 1 < len(c) and np.isfinite(c[i + 1]):
            back = c[i + 1] / c[i]
            persists = abs(back - (1 / factor)) / (1 / factor) > tol      # not an immediate reversal
        if vol_ok and persists:
            out.append({"i": i, "date": (str(dates[i]) if dates is not None else i),
                        "ratio": label, "gap": round(float(gap), 4),
                        "vol_x": round(float(v[i] / vol_avg), 2) if vol_avg > 0 else np.nan,
                        "type": "forward" if forward else "reverse"})
    return out


def detect_rights(close, volume, market_ret, dates=None) -> list:
    """Flag candidate rights-issue ex-dates: an idiosyncratic gap-down within the
    plausible ex-rights band (not a deep crash, not a clean split ratio) on heavy
    volume, that then STABILISES near the diluted level (dilution settles; a crash
    keeps falling or bounces)."""
    c = np.asarray(close, dtype=float); v = np.asarray(volume, dtype=float)
    m = np.asarray(market_ret, dtype=float)
    lo, hi = RIGHTS_BAND
    out = []
    for i in range(21, len(c) - 3):
        if not (np.isfinite(c[i]) and np.isfinite(c[i - 1]) and c[i - 1] > 0):
            continue
        ret = c[i] / c[i - 1] - 1
        if not (lo <= ret <= hi):                        # outside the ex-rights band
            continue
        if nearest_split_ratio(c[i] / c[i - 1]):         # it's a split, not a rights issue
            continue
        mkt = m[i] if i < len(m) and np.isfinite(m[i]) else 0.0
        if (mkt - ret) < RIGHTS_MKT_MARGIN:              # market fell too -> not idiosyncratic
            continue
        vol_avg = np.nanmean(v[max(0, i - 21):i])
        if not (vol_avg > 0 and v[i] >= VOL_MULT * vol_avg):
            continue
        # stabilisation: 3 days later the price sits near the ex-date level (not still
        # cratering = crash, not recovered to pre-drop = a transient shock)
        if not np.isfinite(c[i + 3]) or abs(c[i + 3] / c[i] - 1) > RIGHTS_STABILISE:
            continue
        out.append({"i": i, "date": (str(dates[i]) if dates is not None else i),
                    "drop%": round(ret * 100, 1),
                    "vs_mkt%": round((ret - mkt) * 100, 1),
                    "vol_x": round(float(v[i] / vol_avg), 2)})
    return out


# ── data assembly (offline) ───────────────────────────────────────────────────
from marketdata import close_volume as _wide


def scan_splits(market: str) -> pd.DataFrame:
    import pead_factor as pf
    w = _wide(market)
    if w is None:
        return pd.DataFrame()
    close, vol = w
    dates = [str(d.date()) for d in close.index]
    rows = []
    for s in pf._liquid_symbols(close, vol):
        for ev in detect_splits(close[s].values, vol[s].values, dates):
            rows.append({"market": market, "ticker": s, **{k: ev[k] for k in
                        ("date", "ratio", "gap", "vol_x", "type")}})
    return pd.DataFrame(rows)


def scan_rights(market: str) -> pd.DataFrame:
    import pead_factor as pf
    w = _wide(market)
    if w is None:
        return pd.DataFrame()
    close, vol = w
    mkt = close.pct_change().mean(axis=1).values          # equal-weight market proxy
    dates = [str(d.date()) for d in close.index]
    rows = []
    for s in pf._liquid_symbols(close, vol):
        r = pd.Series(close[s].values).pct_change().values
        for ev in detect_rights(close[s].values, vol[s].values, mkt, dates):
            rows.append({"market": market, "ticker": s, **{k: ev[k] for k in
                        ("date", "drop%", "vs_mkt%", "vol_x")}})
    return pd.DataFrame(rows)


def label_ratio(factor: float) -> str:
    """Name a yfinance split factor (e.g. 10.0 -> '10:1', 0.5 -> '1:2' reverse)."""
    if factor >= 1:
        f = factor
        return f"{int(round(f))}:1" if abs(f - round(f)) < 0.05 else f"{f:.2f}:1"
    inv = 1 / factor
    return f"1:{int(round(inv))}" if abs(inv - round(inv)) < 0.05 else f"1:{inv:.2f}"


def confirm_split_yf(ticker: str) -> list:
    """Authoritative recent splits from yfinance (governed). Returns [(date, factor)]."""
    import apiclient
    import yfinance as yf
    try:
        s = apiclient.robust("yfinance", lambda: yf.Ticker(ticker).splits, retries=2)
        return [(str(d.date()), float(x)) for d, x in s.items()][-5:]
    except Exception:
        return []


def parse_edgar_hits(payload: dict, keyword: str = "") -> list:
    """Pure parser for an EDGAR full-text-search response: extract company, ticker,
    filing date and form from each hit (display_names look like
    'COMPANY NAME  (TICK, TICK2)  (CIK 000...)')."""
    import re
    rows = []
    for h in payload.get("hits", {}).get("hits", []):
        s = h.get("_source", {})
        names = s.get("display_names") or []
        name = names[0] if names else ""
        m = re.search(r"\(([A-Z0-9.\- ]+(?:,\s*[A-Z0-9.\- ]+)*)\)\s*\(CIK", name)
        ticker = m.group(1).split(",")[0].strip() if m else ""
        company = re.sub(r"\s*\(.*$", "", name).strip()
        forms = s.get("root_forms") or s.get("form_type") or []
        form = forms[0] if isinstance(forms, list) and forms else (forms if isinstance(forms, str) else "")
        rows.append({"company": company, "ticker": ticker,
                     "date": s.get("file_date"), "form": form, "event": keyword})
    return rows


def announcements_edgar(keyword: str, forms: str = "8-K", since: str = "2025-01-01",
                        limit: int = 50) -> pd.DataFrame:
    """AUTHORITATIVE corporate-action announcements from SEC EDGAR full-text search —
    real public filings (news), not a price inference. Governed via apiclient."""
    import urllib.parse
    import apiclient
    q = urllib.parse.quote(f'"{keyword}"').replace("%20", "+")
    url = (f"https://efts.sec.gov/LATEST/search-index?q={q}&forms={forms}"
           f"&startdt={since}&enddt=2035-01-01")
    # SEC requires a contact-style User-Agent (email format), else 403. Override via SEC_UA.
    ua = {"User-Agent": os.environ.get("SEC_UA", "global-market-scanners research admin@gms.dev")}
    try:
        r = apiclient.http_get("edgar", url, headers=ua, retries=2)
        if r.status_code != 200:
            return pd.DataFrame()
        return pd.DataFrame(parse_edgar_hits(r.json(), keyword)[:limit])
    except Exception as e:                      # noqa: BLE001
        print(f"  [edgar] {keyword!r} unavailable: {e}", file=sys.stderr)
        return pd.DataFrame()


SPLIT_KEYWORDS = ["forward stock split", "reverse stock split", "stock split"]
RIGHTS_KEYWORDS = ["rights offering", "rights issue"]


def announcements(kind: str, since: str = "2025-01-01", limit: int = 50) -> pd.DataFrame:
    """Union of EDGAR announcements for a corporate-action kind, deduped by
    ticker/company+date (US public filings)."""
    kws = SPLIT_KEYWORDS if kind == "split" else RIGHTS_KEYWORDS
    frames = [announcements_edgar(k, since=since, limit=limit) for k in kws]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    return df.drop_duplicates(subset=["company", "date"]).sort_values("date", ascending=False)


def screen_splits_yf(market: str, limit: int = 60, min_date: str | None = None) -> pd.DataFrame:
    """AUTHORITATIVE split screener: fetch yfinance .splits for the market's most
    liquid `limit` names and keep splits on/after `min_date` (default: the parquet's
    start, i.e. splits within the data window)."""
    import pead_factor as pf
    w = _wide(market)
    if w is None:
        return pd.DataFrame()
    close, vol = w
    if min_date is None:
        min_date = str(close.index.min().date())
    dv = (close * vol).tail(252).median().sort_values(ascending=False)
    names = [s for s in dv.index[:limit]]
    rows = []
    for s in names:
        for d, factor in confirm_split_yf(s):
            if d >= min_date:
                rows.append({"market": market, "ticker": s, "date": d,
                             "ratio": label_ratio(factor), "factor": round(factor, 3),
                             "type": "forward" if factor > 1 else "reverse"})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--action", choices=["split", "rights", "both"], default="split")
    ap.add_argument("--market", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--heuristic", action="store_true",
                    help="split: use the offline price-gap detector instead of yfinance")
    ap.add_argument("--news", action="store_true",
                    help="source from SEC EDGAR public announcements (8-K filings) — authoritative")
    ap.add_argument("--since", default="2025-01-01", help="--news: earliest filing date")
    ap.add_argument("--limit", type=int, default=60, help="split: liquid names to query on yfinance")
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()

    # ── news / public announcements (SEC EDGAR) — authoritative for US ──────────
    if args.news:
        for kind, want in [("split", args.action in ("split", "both")),
                           ("rights", args.action in ("rights", "both"))]:
            if not want:
                continue
            df = announcements(kind, since=args.since, limit=args.top)
            title = "STOCK SPLIT" if kind == "split" else "RIGHTS ISSUE"
            print(f"\n=== {title} — SEC EDGAR public announcements since {args.since} "
                  f"({len(df)} filings) ===")
            if df.empty:
                print("  none found (offline, or no matching 8-K filings)")
            else:
                print(f"  {'ticker':10}{'date':12}{'form':7}  company  ·  event")
                for _, r in df.head(args.top).iterrows():
                    print(f"  {str(r['ticker'] or '—'):10}{str(r['date']):12}{str(r['form']):7}  "
                          f"{str(r['company'])[:38]}  ·  {r['event']}")
        return

    markets = (marketdata.market_list()
               if (args.all or not args.market) else [args.market])

    if args.action in ("split", "both"):
        if args.heuristic:
            sp = pd.concat([scan_splits(m) for m in markets], ignore_index=True) if markets else pd.DataFrame()
            print(f"\n=== STOCK SPLIT SCREENER (offline heuristic) — {len(sp)} candidate events ===")
            print("  price-gap near a split ratio + volume; UNRELIABLE on adjusted data (crashes at "
                  "−33%/−50% collide with 3:2/2:1) — verify with yfinance.")
            if not sp.empty:
                print(f"  {'mkt':4}{'ticker':12}{'date':12}{'ratio':>7}{'gap':>8}{'vol×':>7}  type")
                for _, r in sp.head(args.top).iterrows():
                    print(f"  {str(r['market']):4}{str(r['ticker']):12}{str(r['date']):12}"
                          f"{str(r['ratio']):>7}{r['gap']:>8.3f}{r['vol_x']:>7.2f}  {r['type']}")
        else:
            sp = pd.concat([screen_splits_yf(m, args.limit) for m in markets], ignore_index=True) \
                if markets else pd.DataFrame()
            print(f"\n=== STOCK SPLIT SCREENER (yfinance, authoritative) — {len(sp)} splits in window ===")
            print(f"  actual splits over the data window, top-{args.limit} liquid names per market")
            if sp.empty:
                print("  no splits recorded for the queried names in this window (offline? try --heuristic)")
            else:
                print(f"  {'mkt':4}{'ticker':12}{'date':12}{'ratio':>8}  type")
                for _, r in sp.sort_values("date", ascending=False).head(args.top).iterrows():
                    print(f"  {str(r['market']):4}{str(r['ticker']):12}{str(r['date']):12}"
                          f"{str(r['ratio']):>8}  {r['type']}")

    if args.action in ("rights", "both"):
        ri = pd.concat([scan_rights(m) for m in markets], ignore_index=True) \
            if markets else pd.DataFrame()
        print(f"\n=== RIGHTS-ISSUE / DILUTION-EVENT SCREENER — {len(ri)} candidate ex-dates ===")
        print("  idiosyncratic, persistent gap-downs in the ex-rights band that then stabilise.")
        print("  HONEST CAVEAT: from daily OHLC a rights issue is indistinguishable from an "
              "earnings\n  repricing or a secondary offering — this list is 'sharp idiosyncratic "
              "dilution/repricing\n  candidates'; a true rights issue needs the exchange's "
              "corporate-action feed to confirm.")
        if not ri.empty:
            print(f"  {'mkt':4}{'ticker':12}{'date':12}{'drop%':>8}{'vs_mkt%':>9}{'vol×':>7}")
            for _, r in ri.sort_values("drop%").head(args.top).iterrows():
                print(f"  {str(r['market']):4}{str(r['ticker']):12}{str(r['date']):12}"
                      f"{r['drop%']:>8.1f}{r['vs_mkt%']:>9.1f}{r['vol_x']:>7.2f}")


if __name__ == "__main__":
    main()
