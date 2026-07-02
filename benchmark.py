#!/usr/bin/env python3
"""
benchmark.py
------------
Wires the public factor libraries from `data_sources.py` into the platform: it
fetches **Kenneth French's** published daily factor-return series (the free
analogue of the IIMA IFFM library the quality paper uses) and runs the paper's
alpha regression against them — so our factors are validated against *real*,
externally-published benchmark returns rather than an internal proxy.

Two things it does:
  * `factors(region)` — download + parse + cache the region's Fama-French 5 factors
    (Mkt-RF, SMB, HML, RMW, CMA) + Momentum + RF from Ken French's data library; and
  * `validate_quality(market)` — form the long-only quality (LQ) portfolio from
    `quality_factor.py`, and regress its daily excess return on the real
    market/size/value/momentum factors (Carhart 4-factor, the paper's model) to
    estimate its **alpha** — the exact test in the IIMA paper, now with public
    Ken French factors.

The CSV parser is pure and unit-tested; the download is governed via `apiclient`
and fails gracefully offline. Ken French publishes DAILY factors for the US and the
developed regions (Europe/Japan/Asia-Pacific/Developed) but not for Emerging (daily),
so emerging markets fall back to the Developed series, clearly labelled.

Usage:
  python benchmark.py --region "North America"      # show the real factor premia
  python benchmark.py --validate-quality --market US
  python benchmark.py --market JP --validate-quality
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import warnings
import zipfile

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = os.path.expanduser("~/Downloads/code/python_files/cache_seed")
CACHE_DIR = os.path.join(HERE, "benchmark_cache")
KF_BASE = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
_UA = {"User-Agent": "Mozilla/5.0 (global-market-scanners research)"}

# region -> Ken French daily files (verified to exist). Emerging has no daily 5-factor
# file, so it uses the Developed series as a labelled proxy.
REGION_FILES = {
    "North America":         {"ff5": "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip",
                              "mom": "F-F_Momentum_Factor_daily_CSV.zip"},
    "Europe":                {"ff5": "Europe_5_Factors_Daily_CSV.zip",
                              "mom": "Europe_MOM_Factor_Daily_CSV.zip"},
    "Japan":                 {"ff5": "Japan_5_Factors_Daily_CSV.zip",
                              "mom": "Japan_MOM_Factor_Daily_CSV.zip"},
    "Asia Pacific ex Japan": {"ff5": "Asia_Pacific_ex_Japan_5_Factors_Daily_CSV.zip",
                              "mom": "Asia_Pacific_ex_Japan_MOM_Factor_Daily_CSV.zip"},
    "Emerging":              {"ff5": "Developed_5_Factors_Daily_CSV.zip",   # proxy (no emerging daily)
                              "mom": "Developed_MOM_Factor_Daily_CSV.zip"},
}


# ── pure parser ───────────────────────────────────────────────────────────────
def parse_ff_csv(text: str) -> pd.DataFrame:
    """Parse a Ken French factor CSV (the text inside the zip). Returns a DataFrame
    indexed by date with the factor columns as DECIMALS (the file is in percent).
    Reads the daily block only (rows keyed by an 8-digit YYYYMMDD), stopping at the
    first non-date line; missing codes (-99.99, -999) become NaN."""
    lines = text.splitlines()
    hdr_i = next((i for i, l in enumerate(lines) if "Mkt-RF" in l or l.strip().startswith(",Mom")), None)
    if hdr_i is None:
        raise ValueError("no factor header found")
    cols = [c.strip() for c in lines[hdr_i].split(",")[1:] if c.strip()]
    rows = []
    for l in lines[hdr_i + 1:]:
        tok = [t.strip() for t in l.split(",")]
        if len(tok) < 2 or not (tok[0].isdigit() and len(tok[0]) == 8):
            if rows:
                break                      # end of the daily block (annual section / blank)
            continue
        try:
            vals = [float(x) for x in tok[1:len(cols) + 1]]
        except ValueError:
            continue
        rows.append([tok[0]] + vals)
    df = pd.DataFrame(rows, columns=["date"] + cols)
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df = df.set_index("date").apply(pd.to_numeric, errors="coerce")
    df = df.mask((df <= -99.0))            # Ken French missing codes
    return df / 100.0                      # percent -> decimal


# ── fetch + cache ─────────────────────────────────────────────────────────────
def _download_member(filename: str) -> str | None:
    import apiclient
    try:
        r = apiclient.http_get("kenfrench", KF_BASE + filename, headers=_UA, timeout=30, retries=2)
        if r.status_code != 200:
            return None
        z = zipfile.ZipFile(io.BytesIO(r.content))
        return z.read(z.namelist()[0]).decode("latin-1")
    except Exception as e:                 # noqa: BLE001
        print(f"  [benchmark] {filename} unavailable: {e}", file=sys.stderr)
        return None


def factors(region: str, use_cache: bool = True) -> pd.DataFrame | None:
    """FF5 + Mom + RF daily factors for a region (cached to parquet). Decimals."""
    spec = REGION_FILES.get(region)
    if not spec:
        return None
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache = os.path.join(CACHE_DIR, f"kf_{region.replace(' ', '_')}.parquet")
    if use_cache and os.path.exists(cache):
        return pd.read_parquet(cache)
    ff = _download_member(spec["ff5"])
    if ff is None:
        return None
    df = parse_ff_csv(ff)
    mom = _download_member(spec["mom"])
    if mom is not None:
        try:
            mdf = parse_ff_csv(mom).rename(columns=lambda c: "Mom" if c.lower().startswith("mom") else c)
            df = df.join(mdf[["Mom"]], how="left")
        except Exception:
            pass
    df.to_parquet(cache)
    return df


# ── alpha regression (the paper's test, with real factors) ────────────────────
def carhart_alpha(port_excess: pd.Series, fac: pd.DataFrame) -> dict:
    """Regress a portfolio's daily EXCESS return on the available factors
    (Mkt-RF, SMB, HML, and Mom if present = Carhart 4-factor). Returns the alpha
    (daily), its t-stat, monthly/annualised alpha, factor loadings and R²."""
    from factor_research import ols
    use = [c for c in ["Mkt-RF", "SMB", "HML", "Mom"] if c in fac.columns]
    j = pd.concat([port_excess.rename("y"), fac[use]], axis=1).dropna()
    if len(j) < 30:
        return {"n": len(j), "alpha_daily": None}
    res = ols(j["y"].values, j[use].values, use)
    a_daily = res["intercept"][0]
    # alpha is only trustworthy with a long window and a broad portfolio; with the
    # ~1y seed data and a snapshot (look-ahead) universe it is a diagnostic, not a
    # performance claim. The LOADINGS, by contrast, are interpretable.
    reliable = len(j) >= 400
    return {"n": len(j), "alpha_daily": a_daily, "alpha_t": res["intercept"][1],
            "alpha_monthly%": round(a_daily * 21 * 100, 3),
            "alpha_reliable": reliable,
            "loadings": {k: res[k] for k in use}, "R2": res["_R2"]}


def factor_premia(fac: pd.DataFrame) -> pd.DataFrame:
    """Annualised mean / vol / Sharpe of each published factor — the reference premia."""
    rows = []
    for c in fac.columns:
        s = fac[c].dropna()
        mu = s.mean() * 252
        vol = s.std() * np.sqrt(252)
        rows.append({"factor": c, "ann_mean%": round(mu * 100, 2),
                     "ann_vol%": round(vol * 100, 2),
                     "sharpe": round(mu / vol, 2) if vol else np.nan,
                     "n_days": len(s), "from": str(s.index.min().date()),
                     "to": str(s.index.max().date())})
    return pd.DataFrame(rows)


# ── portfolio formation (long-only quality, per the paper) ────────────────────
def _clean(t):
    return str(t).split(".")[0].upper()


def quality_lq_returns(market: str) -> pd.Series | None:
    """Daily equal-weight return of the top-decile (LQ) quality portfolio for a
    market, over the local price history. Snapshot quality scores (look-ahead) — the
    resulting alpha is illustrative, not point-in-time (same caveat as dvm_composite)."""
    import quality_factor as qf
    f = qf.load_fundamentals([market])
    if f.empty:
        return None
    f = qf.attach_price_risk(f)
    scored = qf.score_universe(f, by_market=True).dropna(subset=["quality"])
    if scored.empty:
        return None
    scored["decile"] = qf.assign_deciles(scored["quality"])
    lq_keys = {_clean(t) for t in scored[scored["decile"] == "quality"]["ticker"]}
    p = os.path.join(SEED, f"cleaned_long_{market}.parquet")
    if not os.path.exists(p) or not lq_keys:
        return None
    px = pd.read_parquet(p)
    px["key"] = px["Symbol"].map(_clean)
    wide = px[px["key"].isin(lq_keys)].pivot_table(index="Date", columns="Symbol",
                                                    values="Close", aggfunc="last").astype(float)
    if wide.shape[1] < 3:
        return None
    ret = wide.pct_change(fill_method=None).mean(axis=1)       # equal-weight daily return
    ret.index = pd.to_datetime(ret.index)
    return ret.dropna()


def validate_quality(market: str) -> dict:
    """Form LQ for a market and regress its excess return on the region's real Ken
    French factors -> alpha (the paper's headline test, with public factors)."""
    import data_sources
    region = data_sources.KEN_FRENCH_REGION.get(market.upper())
    if not region:
        return {"error": f"no Ken French region for {market}"}
    fac = factors(region)
    if fac is None:
        return {"error": "Ken French factors unavailable (offline?)"}
    port = quality_lq_returns(market)
    if port is None:
        return {"error": f"could not form LQ portfolio for {market}"}
    excess = (port - fac["RF"].reindex(port.index)).dropna()
    out = carhart_alpha(excess, fac)
    out.update({"market": market, "region": region})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default=None, help="show real factor premia for a region")
    ap.add_argument("--market", default=None)
    ap.add_argument("--validate-quality", action="store_true",
                    help="regress the market's LQ quality portfolio on real Ken French factors")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    if args.validate_quality:
        mkt = args.market or "US"
        res = validate_quality(mkt)
        print(f"=== QUALITY (LQ) ALPHA vs real Ken French factors — {mkt} ===")
        if res.get("error"):
            print("  " + res["error"]); return
        if res.get("alpha_daily") is None:
            print(f"  too few overlapping days ({res['n']})"); return
        print(f"  region: {res['region']}  ·  {res['n']} overlapping days  ·  R² = {res['R2']}")
        print("  factor loadings vs REAL Ken French factors (the interpretable result):")
        for k, (c, t) in res["loadings"].items():
            sig = " *" if abs(t) > 2 else ""
            print(f"     {k:8} {c:>8}  (t={t}){sig}")
        # sign-check against the paper's findings
        hml = res["loadings"].get("HML", (0, 0))[0]
        mom = res["loadings"].get("Mom", (0, 0))[0]
        print(f"  -> quality tilt: HML {'−' if hml < 0 else '+'} , Mom {'+' if mom > 0 else '−'}  "
              f"({'matches' if hml < 0 and mom > 0 else 'differs from'} the paper: −value, +momentum)")
        flag = "" if res["alpha_reliable"] else "  [UNRELIABLE: short window + snapshot look-ahead]"
        print(f"\n  alpha (diagnostic only): {res['alpha_daily']*100:.3f}%/day, t={res['alpha_t']}{flag}")
        print("  NB: alpha is inflated by snapshot (look-ahead) quality selection over a ~1y window"
              "\n  and a small fundamentals universe — read the LOADINGS, not the alpha. The factors"
              "\n  themselves are the real, published Ken French series.")
        return

    region = args.region or (__import__("data_sources").KEN_FRENCH_REGION.get(
        (args.market or "US").upper(), "North America"))
    fac = factors(region, use_cache=not args.no_cache)
    if fac is None:
        print(f"Ken French factors for '{region}' unavailable (offline?)"); return
    print(f"=== real Ken French factor premia — {region} ===")
    print(factor_premia(fac).to_string(index=False))
    print(f"\n  source: Kenneth French Data Library ({KF_BASE})")


if __name__ == "__main__":
    main()
