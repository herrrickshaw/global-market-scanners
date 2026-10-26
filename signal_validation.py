#!/usr/bin/env python3
"""
signal_validation.py
--------------------
Pure, offline statistics for validating cross-sectional equity signals the way
a systematic-equities research desk does. No I/O, no network: every function
takes pandas/numpy objects and returns numbers, so the whole module is unit-
tested in tests/test_signal_validation.py and reused by signal_research.py.

Why it exists. Twenty-two open quant-researcher roles (IMC, Squarepoint,
WorldQuant, QRT, Trexquant, DRW, HRT, Graviton, BestEx, ... — see
SIGNAL_RESEARCH.md) ask for the same handful of things. The repo's earlier
studies (ML_VIABILITY, FACTOR_RESEARCH, zone-rule backtests) were single
cross-sections or 8-12-name universes with plain t-stats. This module supplies
the missing machinery:

  JD ask                                         function(s)
  ---------------------------------------------  ------------------------------
  "statistically robust signals"                 rank_ic_series, newey_west_t
  "risks of overfitting", hypothesis testing     bh_fdr, deflated_sharpe
  "market-neutral signals" (Trexquant)           neutralize
  "orthogonal features across families" (IMC)    fama_macbeth
  "transaction costs, liquidity" (DRW, BestEx)   quantile_spread_turnover
  "robust model validation" (IMC lead alpha)     purged_walk_forward
  "monitor signal behaviour over time" (QRT)     ic_health

Conventions. Panels are wide DataFrames: index = formation dates (sorted),
columns = symbols. A signal panel and a forward-return panel must share the
same index/columns; NaN means "not in the universe that date".
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats


# ── information coefficient ──────────────────────────────────────────────────
def rank_ic_series(signal: pd.DataFrame, fwd: pd.DataFrame,
                   min_names: int = 30) -> pd.Series:
    """Per-date Spearman rank IC between signal and forward return.

    Ranks are taken only over names where BOTH values exist, so a name with a
    signal but no forward return (delisted mid-window) does not distort ranks.
    Dates with fewer than `min_names` valid names return NaN.
    """
    s, f = signal.align(fwd, join="inner")
    both = s.notna() & f.notna()
    rs = s.where(both).rank(axis=1)
    rf = f.where(both).rank(axis=1)
    rs = rs.sub(rs.mean(axis=1), axis=0)
    rf = rf.sub(rf.mean(axis=1), axis=0)
    num = (rs * rf).sum(axis=1)
    den = np.sqrt((rs ** 2).sum(axis=1) * (rf ** 2).sum(axis=1))
    ic = num / den.replace(0, np.nan)
    ic[both.sum(axis=1) < min_names] = np.nan
    return ic


def newey_west_t(x: pd.Series | np.ndarray, lags: int) -> tuple[float, float, float]:
    """Mean, HAC (Newey-West, Bartlett kernel) t-stat and two-sided p-value.

    Overlapping forward windows (e.g. 21-day returns sampled weekly) make
    successive ICs autocorrelated; a plain t-stat then overstates significance
    by roughly sqrt(overlap). `lags` should be about the overlap in periods.
    """
    v = np.asarray(pd.Series(x).dropna(), dtype=float)
    n = len(v)
    if n < 3:
        return float("nan"), float("nan"), float("nan")
    mu = v.mean()
    e = v - mu
    gamma0 = e @ e / n
    var = gamma0
    for k in range(1, min(lags, n - 1) + 1):
        w = 1.0 - k / (lags + 1.0)
        var += 2.0 * w * (e[k:] @ e[:-k]) / n
    se = math.sqrt(max(var, 1e-300) / n)
    t = mu / se
    p = 2.0 * stats.t.sf(abs(t), df=n - 1)
    return float(mu), float(t), float(p)


# ── multiple testing ─────────────────────────────────────────────────────────
def bh_fdr(pvals) -> np.ndarray:
    """Benjamini-Hochberg q-values (NaN-preserving)."""
    p = np.asarray(pvals, dtype=float)
    q = np.full_like(p, np.nan)
    ok = ~np.isnan(p)
    m = ok.sum()
    if m == 0:
        return q
    pv = p[ok]
    order = np.argsort(pv)
    ranked = pv[order] * m / np.arange(1, m + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(m)
    out[order] = np.clip(ranked, 0, 1)
    q[ok] = out
    return q


def deflated_sharpe(returns: pd.Series | np.ndarray, n_trials: int,
                    sr_trials_var: float) -> tuple[float, float]:
    """Bailey & López de Prado (2014) Deflated Sharpe Ratio.

    Returns (per-period Sharpe, DSR probability). DSR is the probability that
    the true Sharpe exceeds the maximum Sharpe expected from `n_trials`
    independent strategies with zero skill, given the observed variance of
    Sharpe ratios across those trials. DSR > 0.95 is the usual bar.
    """
    r = np.asarray(pd.Series(returns).dropna(), dtype=float)
    T = len(r)
    if T < 10 or r.std(ddof=1) == 0:
        return float("nan"), float("nan")
    sr = r.mean() / r.std(ddof=1)
    g3 = stats.skew(r)
    g4 = stats.kurtosis(r, fisher=False)
    emc = 0.5772156649
    n = max(int(n_trials), 2)
    sd = math.sqrt(max(sr_trials_var, 0.0))
    sr0 = sd * ((1 - emc) * stats.norm.ppf(1 - 1.0 / n)
                + emc * stats.norm.ppf(1 - 1.0 / (n * math.e)))
    denom = math.sqrt(max(1 - g3 * sr + (g4 - 1) / 4.0 * sr ** 2, 1e-12))
    z = (sr - sr0) * math.sqrt(T - 1) / denom
    return float(sr), float(stats.norm.cdf(z))


# ── neutralisation ───────────────────────────────────────────────────────────
def neutralize(signal: pd.DataFrame, exposures: list[pd.DataFrame]) -> pd.DataFrame:
    """Cross-sectional residual of the signal's rank on the exposures' ranks.

    Per date: OLS of rank(signal) on [1, rank(exp_1), ..., rank(exp_k)] across
    names, keep the residual. Removes what the signal shares with e.g. size /
    liquidity / volatility so the remaining IC is the signal's own.
    """
    out = pd.DataFrame(np.nan, index=signal.index, columns=signal.columns)
    rs = signal.rank(axis=1, pct=True)
    rx = [e.reindex_like(signal).rank(axis=1, pct=True) for e in exposures]
    for d in signal.index:
        y = rs.loc[d].to_numpy()
        X = np.column_stack([np.ones_like(y)] + [x.loc[d].to_numpy() for x in rx])
        ok = ~np.isnan(y) & ~np.isnan(X).any(axis=1)
        if ok.sum() < X.shape[1] + 10:
            continue
        beta, *_ = np.linalg.lstsq(X[ok], y[ok], rcond=None)
        res = np.full_like(y, np.nan)
        res[ok] = y[ok] - X[ok] @ beta
        out.loc[d] = res
    return out


# ── orthogonality: Fama-MacBeth on ranked signals ────────────────────────────
def _zrank(df: pd.DataFrame) -> pd.DataFrame:
    r = df.rank(axis=1, pct=True)
    return r.sub(r.mean(axis=1), axis=0).div(r.std(axis=1), axis=0)


def fama_macbeth(signals: dict[str, pd.DataFrame], fwd: pd.DataFrame,
                 lags: int, min_names: int = 50) -> pd.DataFrame:
    """Per-date multivariate regression of z-ranked forward return on all
    z-ranked signals jointly; report mean coefficient and Newey-West t.

    A signal whose univariate IC is significant but whose FM coefficient is
    not is redundant with the others — not an orthogonal feature.
    """
    names = list(signals)
    Z = {k: _zrank(v) for k, v in signals.items()}
    y_all = _zrank(fwd)
    coefs = []
    for d in fwd.index:
        y = y_all.loc[d].to_numpy()
        X = np.column_stack([Z[k].loc[d].reindex(fwd.columns).to_numpy() for k in names])
        ok = ~np.isnan(y) & ~np.isnan(X).any(axis=1)
        if ok.sum() < max(min_names, len(names) + 10):
            coefs.append([np.nan] * len(names))
            continue
        Xo = np.column_stack([np.ones(ok.sum()), X[ok]])
        b, *_ = np.linalg.lstsq(Xo, y[ok], rcond=None)
        coefs.append(list(b[1:]))
    C = pd.DataFrame(coefs, index=fwd.index, columns=names)
    rows = []
    for k in names:
        mu, t, p = newey_west_t(C[k], lags)
        rows.append({"signal": k, "fm_coef": mu, "fm_t": t, "fm_p": p,
                     "n_dates": int(C[k].notna().sum())})
    return pd.DataFrame(rows)


# ── costs: quantile spread, turnover, break-even ─────────────────────────────
def quantile_spread_turnover(signal: pd.DataFrame, fwd: pd.DataFrame,
                             q: float = 0.2, min_names: int = 50) -> dict:
    """Top-minus-bottom quantile return, long-only excess, and turnover.

    Call it on a NON-overlapping grid (one formation date per holding period)
    so each period's return is one real holding and turnover is per rebalance.
    Turnover = share of the leg's names replaced vs the previous rebalance
    (one-sided, 0..1). A round-trip cost c then costs the long-short book
    2·τ·c per period (both legs), the long-only book τ_long·c.
    """
    s, f = signal.align(fwd, join="inner")
    ls, lo, tl, ts = [], [], [], []
    prev_top = prev_bot = None
    for d in s.index:
        row = s.loc[d]
        fr = f.loc[d]
        ok = row.notna() & fr.notna()
        if ok.sum() < min_names:
            prev_top = prev_bot = None
            continue
        r = row[ok].rank(pct=True)
        top = set(r.index[r > 1 - q])
        bot = set(r.index[r <= q])
        ret = fr[ok]
        ls.append(ret[list(top)].mean() - ret[list(bot)].mean())
        lo.append(ret[list(top)].mean() - ret.mean())
        if prev_top:
            tl.append(1 - len(top & prev_top) / max(len(top), 1))
            ts.append(1 - len(bot & prev_bot) / max(len(bot), 1))
        prev_top, prev_bot = top, bot
    ls, lo = np.array(ls), np.array(lo)
    tau_l = float(np.mean(tl)) if tl else float("nan")
    tau_s = float(np.mean(ts)) if ts else float("nan")
    tau = np.nanmean([tau_l, tau_s])
    spread = float(np.nanmean(ls)) if len(ls) else float("nan")
    excess = float(np.nanmean(lo)) if len(lo) else float("nan")
    return {
        "n_periods": int(len(ls)),
        "ls_spread": spread,
        "ls_sharpe": float(np.nanmean(ls) / np.nanstd(ls, ddof=1)) if len(ls) > 2 else float("nan"),
        "long_excess": excess,
        "turnover_long": tau_l,
        "turnover_ls": float(tau),
        "breakeven_rt_cost_ls": spread / (2 * tau) if tau and tau > 0 else float("nan"),
        "breakeven_rt_cost_long": excess / tau_l if tau_l and tau_l > 0 else float("nan"),
        "ls_series": ls,
    }


# ── purged walk-forward combination ──────────────────────────────────────────
def purged_walk_forward(signals: dict[str, pd.DataFrame], fwd: pd.DataFrame,
                        embargo: int, n_folds: int = 5, alpha: float = 0.1,
                        min_train: int = 104) -> pd.DataFrame:
    """Expanding-window, purged out-of-sample test of three ways to combine
    signals, all chosen using TRAINING data only:

      best_single   the single signal with the largest |train IC| (sign kept)
      ic_weighted   z-ranked signals weighted by train mean IC
      ridge         pooled ridge regression of z-ranked fwd on z-ranked signals

    `embargo` dates between the end of training and the start of each test
    fold are dropped so training labels (forward windows) never overlap the
    test period — López de Prado's purging for overlapping labels.
    Returns per-fold OOS mean IC for each method.
    """
    names = list(signals)
    Z = {k: _zrank(v.reindex_like(fwd)) for k, v in signals.items()}
    Y = _zrank(fwd)
    dates = fwd.index
    n = len(dates)
    start = min_train
    if n - start < n_folds * 4:
        return pd.DataFrame()
    edges = np.linspace(start, n, n_folds + 1).astype(int)
    rows = []
    for i in range(n_folds):
        t0, t1 = edges[i], edges[i + 1]
        tr = dates[: max(t0 - embargo, 0)]
        te = dates[t0:t1]
        if len(tr) < 52:
            continue
        ic_tr = {k: rank_ic_series(Z[k].loc[tr], fwd.loc[tr]).mean() for k in names}
        best = max(ic_tr, key=lambda k: abs(ic_tr[k]) if pd.notna(ic_tr[k]) else -1)
        sign = np.sign(ic_tr[best])
        Xtr = np.column_stack([Z[k].loc[tr].to_numpy().ravel() for k in names])
        ytr = Y.loc[tr].to_numpy().ravel()
        ok = ~np.isnan(ytr) & ~np.isnan(Xtr).any(axis=1)
        Xo, yo = Xtr[ok], ytr[ok]
        # features are unit-variance z-ranks, so `alpha` is a penalty relative
        # to the per-observation Gram matrix (0.1 = mild shrinkage)
        nobs = len(yo)
        beta = np.linalg.solve(Xo.T @ Xo / nobs + alpha * np.eye(len(names)),
                               Xo.T @ yo / nobs)
        w = np.array([ic_tr[k] if pd.notna(ic_tr[k]) else 0.0 for k in names])
        preds = {
            "best_single": Z[best].loc[te] * sign,
            "ic_weighted": sum(Z[k].loc[te] * w[j] for j, k in enumerate(names)),
            "ridge": sum(Z[k].loc[te] * beta[j] for j, k in enumerate(names)),
        }
        row = {"fold": i + 1, "test_start": str(te[0].date()), "test_end": str(te[-1].date()),
               "best_single_name": best}
        for m, p in preds.items():
            row[f"oos_ic_{m}"] = float(rank_ic_series(p, fwd.loc[te]).mean())
        rows.append(row)
    return pd.DataFrame(rows)


# ── live-signal health monitor ───────────────────────────────────────────────
def ic_health(ic: pd.Series, window: int = 52, lags: int = 1) -> dict:
    """Compare the trailing `window` IC with the full history.

    Status:
      HEALTHY   recent mean IC has the full-sample sign and >= half its size
      DECAYING  same sign but < half the full-sample magnitude
      FLIPPED   opposite sign and recent HAC |t| >= 2 (the signal now works
                in reverse — act on it)
      NOISE     full-sample IC itself is not significant (|t| < 2)
    """
    full_mu, full_t, _ = newey_west_t(ic, lags)
    recent = ic.dropna().iloc[-window:]
    rec_mu, rec_t, _ = newey_west_t(recent, lags)
    if not np.isfinite(full_t) or abs(full_t) < 2:
        status = "NOISE"
    elif np.sign(rec_mu) != np.sign(full_mu) and abs(rec_t) >= 2:
        status = "FLIPPED"
    elif np.sign(rec_mu) == np.sign(full_mu) and abs(rec_mu) >= 0.5 * abs(full_mu):
        status = "HEALTHY"
    else:
        status = "DECAYING"
    return {"full_ic": full_mu, "full_t": full_t, "recent_ic": rec_mu,
            "recent_t": rec_t, "status": status}
