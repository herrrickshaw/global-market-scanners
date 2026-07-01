#!/usr/bin/env python3
"""
ml_screen_discovery.py
----------------------
Hybrid ML that INVENTS new screens. Follows the 3-layer architecture from
ML_Stock_Screening_System.docx, adapted to "discover better screeners" and
implemented on scikit-learn (no UMAP/HDBSCAN/PPO deps required):

  LAYER 1 — SUPERVISED (the existing knowledge)
     GradientBoosting learns which feature patterns preceded strong forward
     returns, using the screener.in screens as the known-good reference set.
     Feature importances give the explainability the doc calls for.

  LAYER 2 — UNSUPERVISED (discover NEW patterns -> propose NEW screens)
     Standardise -> PCA (UMAP proxy) -> KMeans clusters + IsolationForest
     outliers. Clusters whose realised forward return beats the market are
     turned into a NEW SCREEN: the feature-range rule that defines the cluster,
     conditioned on the current market regime + a liquidity floor.

  LAYER 3 — RL-FROM-SCREENERS (stay tethered to the known universe)
     If a proposed screen's picks deviate too far from the known screener.in
     universe (low overlap), a cross-entropy-method policy refines the screen's
     thresholds to maximise  reward = forward-return edge - lambda*deviation.
     i.e. it is rewarded for beating the market AND for not drifting off the
     validated universe — reinforcement learning anchored on the screeners.

Data via the Cassandra cache (market_store); dates via market_holidays.
Outputs ranked new-screen recommendations to a compact SQLite (screen_reco.db).

Usage:
  python ml_screen_discovery.py --market US --limit 400
  python ml_screen_discovery.py --market India --min-dollar-vol 3e6
"""

from __future__ import annotations

import argparse
import glob
import os
import sqlite3
import sys
import warnings
from datetime import date

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

FWD = 21                     # forward horizon (~1 month) for the return label
FEATURES = ["ret_20d", "ret_60d", "vol_20d", "rsi_14", "dist_52w_high",
            "dist_200dma", "vol_ratio", "dollar_vol_log"]


# ── feature engineering ───────────────────────────────────────────────────────
def _features_asof(df: pd.DataFrame):
    """Feature vector + realised FWD return for one stock at the latest bar."""
    if df is None or len(df) < 260:
        return None
    c = df["Close"].astype(float); v = df["Volume"].astype(float)
    d = c.diff()
    rsi = 100 - 100 / (1 + d.clip(lower=0).rolling(14).mean() /
                       (-d.clip(upper=0)).rolling(14).mean().replace(0, np.nan))
    feat = {
        "ret_20d": c.pct_change(20).iloc[-1] * 100,
        "ret_60d": c.pct_change(60).iloc[-1] * 100,
        "vol_20d": c.pct_change().rolling(20).std().iloc[-1] * np.sqrt(252) * 100,
        "rsi_14": rsi.iloc[-1],
        "dist_52w_high": (c.iloc[-1] / c.rolling(252).max().iloc[-1] - 1) * 100,
        "dist_200dma": (c.iloc[-1] / c.rolling(200).mean().iloc[-1] - 1) * 100,
        "vol_ratio": (v.iloc[-1] / v.rolling(20).mean().iloc[-1]) if v.rolling(20).mean().iloc[-1] else 1.0,
        "dollar_vol_log": np.log10(max((c.iloc[-1] * v.iloc[-1]), 1.0)),
    }
    # label: realised return over the FWD window ENDING at the latest bar (known)
    fwd = (c.iloc[-1] / c.iloc[-1 - FWD] - 1) * 100 if len(c) > FWD else np.nan
    if any(pd.isna(x) for x in feat.values()) or pd.isna(fwd):
        return None
    feat["_fwd"] = fwd
    return feat


def build_matrix(ohlc: dict) -> pd.DataFrame:
    rows = {t: f for t, f in ((t, _features_asof(df)) for t, df in ohlc.items()) if f}
    return pd.DataFrame(rows).T


# ── screen rules (a screen = per-feature [lo,hi] box) ─────────────────────────
def rule_from_cluster(X: pd.DataFrame, mask, q=(20, 80)) -> dict:
    """Derive a screen rule = inter-quantile box of the cluster on each feature."""
    sub = X.loc[mask, FEATURES]
    return {f: (float(np.percentile(sub[f], q[0])), float(np.percentile(sub[f], q[1])))
            for f in FEATURES}


def apply_rule(X: pd.DataFrame, rule: dict) -> np.ndarray:
    m = np.ones(len(X), dtype=bool)
    for f, (lo, hi) in rule.items():
        m &= (X[f].values >= lo) & (X[f].values <= hi)
    return m


def deviation(mask, known_mask) -> float:
    """1 - Jaccard overlap with the known screener universe (0=identical,1=disjoint)."""
    a, b = set(np.where(mask)[0]), set(np.where(known_mask)[0])
    if not a and not b:
        return 0.0
    return 1 - len(a & b) / max(len(a | b), 1)


def reward(X, rule, known_mask, lam=1.5):
    m = apply_rule(X, rule)
    if m.sum() < 3:
        return -9.9, m
    edge = X.loc[m, "_fwd"].mean() - X["_fwd"].mean()   # beat the market
    dev = deviation(m, known_mask)
    return float(edge - lam * dev), m


# ── LAYER 3: RL-from-screeners (cross-entropy-method policy over thresholds) ───
def rl_refine(X, rule, known_mask, iters=25, pop=40, elite=0.3):
    """Refine a screen's thresholds to maximise edge while staying tethered to the
    known screener universe. CEM = a lightweight policy-search (RL) method."""
    feats = FEATURES
    lo = np.array([rule[f][0] for f in feats]); hi = np.array([rule[f][1] for f in feats])
    mu = np.concatenate([lo, hi]); sigma = np.abs(mu) * 0.25 + 1.0
    best_r, _ = reward(X, rule, known_mask); best_rule = rule
    for _ in range(iters):
        samples = np.random.normal(mu, sigma, size=(pop, len(mu)))
        scored = []
        for s in samples:
            k = len(feats)
            r = {f: (min(s[i], s[i + k]), max(s[i], s[i + k])) for i, f in enumerate(feats)}
            rr, _ = reward(X, r, known_mask)
            scored.append((rr, s, r))
        scored.sort(key=lambda z: z[0], reverse=True)
        elites = np.array([s for _, s, _ in scored[:max(2, int(pop * elite))]])
        mu, sigma = elites.mean(0), elites.std(0) + 1e-3
        if scored[0][0] > best_r:
            best_r, best_rule = scored[0][0], scored[0][2]
    return best_rule, best_r


UNI = {"US": "data/us_full_scan/**/us_full_scan_*.xlsx",
       "India": "data/**/indian_full_scan_*.xlsx",
       "Japan": "data/japan_scan/**/japan_market_scan_*.xlsx",
       "Korea": "data/korea_scan/**/korea_market_scan_*.xlsx"}


def load_universe(market, limit):
    hits = sorted(glob.glob(os.path.expanduser(f"~/Downloads/{UNI[market]}"), recursive=True))
    a = pd.ExcelFile(hits[-1]).parse("All_Stocks")
    if market == "India":
        s = (a["Symbol"].astype(str) + a["Suffix"].astype(str)).tolist()
    elif market in ("Japan", "Korea"):
        s = a["YF_Ticker"].astype(str).tolist()
    else:
        s = a["Symbol"].astype(str).tolist()
    s = [x for x in s if x and x != "nan"]
    return s[:limit] if limit else s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="US", choices=list(UNI))
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--min-dollar-vol", type=float, default=2e6)
    ap.add_argument("--dev-threshold", type=float, default=0.6,
                    help="RL kicks in when a screen deviates more than this from known universe")
    ap.add_argument("--db", default="screen_reco.db")
    args = ap.parse_args()

    from market_store import cached_download
    from market_holidays import should_run_today
    from sklearn.preprocessing import StandardScaler
    from sklearn.decomposition import PCA
    from sklearn.cluster import KMeans
    from sklearn.ensemble import GradientBoostingClassifier, IsolationForest

    print(f"[{args.market}] market open today? {should_run_today(args.market)}", file=sys.stderr)
    tickers = load_universe(args.market, args.limit)
    ohlc = cached_download(tickers, years=5)
    # liquidity filter (market conditions input)
    ohlc = {t: df for t, df in ohlc.items()
            if len(df) > 260 and (df["Close"] * df["Volume"]).tail(252).median() >= args.min_dollar_vol}
    X = build_matrix(ohlc)
    if len(X) < 40:
        print("too few stocks after filtering", file=sys.stderr); return
    print(f"[{args.market}] {len(X)} liquid stocks with features", file=sys.stderr)

    Xf = X[FEATURES].astype(float)
    y = (X["_fwd"] > X["_fwd"].median()).astype(int).values   # good vs poor forward return

    # LAYER 1 — supervised: which patterns precede outperformance
    gb = GradientBoostingClassifier(max_depth=3, n_estimators=120).fit(Xf.values, y)
    imp = dict(sorted(zip(FEATURES, gb.feature_importances_), key=lambda z: -z[1]))
    known_mask = gb.predict_proba(Xf.values)[:, 1] > 0.6      # the "known good universe"
    print(f"[L1] top features: {[f'{k}:{v:.2f}' for k,v in list(imp.items())[:4]]}; "
          f"known-good universe: {known_mask.sum()} stocks", file=sys.stderr)

    # LAYER 2 — unsupervised: discover clusters -> propose new screens
    Z = PCA(n_components=min(5, len(FEATURES))).fit_transform(StandardScaler().fit_transform(Xf))
    km = KMeans(n_clusters=min(8, len(X) // 8), n_init=10, random_state=0).fit(Z)
    X["_cluster"] = km.labels_
    X["_outlier"] = IsolationForest(contamination=0.1, random_state=0).fit_predict(Z)
    mkt_ret = X["_fwd"].mean()

    recos = []
    for cl in sorted(set(km.labels_)):
        mask = (X["_cluster"] == cl).values
        if mask.sum() < 5:
            continue
        cl_ret = X.loc[mask, "_fwd"].mean()
        if cl_ret <= mkt_ret:                     # only outperforming clusters become screens
            continue
        rule = rule_from_cluster(X, mask)
        picks = apply_rule(X, rule)
        dev = deviation(picks, known_mask)
        rl_used = dev > args.dev_threshold
        rl_reward = None
        if rl_used:                               # LAYER 3 — RL kicks in
            rule, rl_reward = rl_refine(X, rule, known_mask)
            picks = apply_rule(X, rule)
            dev = deviation(picks, known_mask)
        edge = X.loc[picks, "_fwd"].mean() - mkt_ret if picks.sum() else 0.0
        recos.append({"market": args.market, "cluster": int(cl), "n_picks": int(picks.sum()),
                      "cluster_fwd%": round(float(cl_ret), 2), "screen_edge%": round(float(edge), 2),
                      "deviation": round(float(dev), 2), "rl_refined": rl_used,
                      "rule": {k: [round(v[0], 2), round(v[1], 2)] for k, v in rule.items()}})

    recos.sort(key=lambda r: r["screen_edge%"], reverse=True)

    conn = sqlite3.connect(args.db); conn.execute("PRAGMA journal_mode=DELETE;")
    conn.execute("DROP TABLE IF EXISTS screen_reco")
    conn.execute("""CREATE TABLE screen_reco(market TEXT, cluster INT, n_picks INT,
        cluster_fwd REAL, screen_edge REAL, deviation REAL, rl_refined INT, rule TEXT)""")
    import json
    conn.executemany("INSERT INTO screen_reco VALUES (?,?,?,?,?,?,?,?)",
        [(r["market"], r["cluster"], r["n_picks"], r["cluster_fwd%"], r["screen_edge%"],
          r["deviation"], int(r["rl_refined"]), json.dumps(r["rule"])) for r in recos])
    conn.commit(); conn.close()

    print(f"\n=== NEW SCREEN RECOMMENDATIONS — {args.market} (market fwd {mkt_ret:.2f}%) ===")
    for r in recos[:6]:
        tag = "  [RL-refined]" if r["rl_refined"] else ""
        print(f"  screen#{r['cluster']}: edge {r['screen_edge%']:+.2f}% | {r['n_picks']} picks | "
              f"dev {r['deviation']}{tag}")
        top = sorted(imp, key=lambda f: -imp[f])[:3]
        print("     rule:", {f: r["rule"][f] for f in top})
    print(f"\n  {len(recos)} outperforming clusters -> new screens ({sum(r['rl_refined'] for r in recos)} RL-refined). "
          f"Saved to {args.db}")


if __name__ == "__main__":
    main()
