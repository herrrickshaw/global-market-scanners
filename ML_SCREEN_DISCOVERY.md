# Hybrid ML Screen Discovery

`ml_screen_discovery.py` — a tool that **invents new screens** from existing ones,
following the 3-layer architecture in *ML_Stock_Screening_System.docx* (Supervised
→ Unsupervised → Reinforcement), implemented on scikit-learn.

## The three layers (as built)

| Layer | Doc's design | Here |
|---|---|---|
| **1 — Supervised** (existing knowledge) | XGBoost/RandomForest on 5y labelled data, SHAP | `GradientBoosting` learns which features precede outperformance; feature-importances = explainability; its high-confidence picks = the **known-good universe** |
| **2 — Unsupervised** (discover new patterns → new screens) | UMAP + HDBSCAN + KMeans, novel clusters | `StandardScaler → PCA → KMeans` + `IsolationForest` outliers. Each **outperforming cluster becomes a NEW SCREEN** = the inter-quantile feature box that defines it, conditioned on **market regime + a liquidity floor** |
| **3 — Reinforcement** (stay tethered) | PPO policy, risk-adjusted reward | **RL-from-screeners**: if a proposed screen deviates too far from the known screener universe (low overlap), a **cross-entropy-method** policy refines its thresholds to maximise `reward = forward-edge − λ·deviation` |

So: the supervised layer encodes what the screener.in screens already know; the
unsupervised layer proposes screens the market hasn't priced; and when a proposal
drifts too far off the validated universe, RL pulls it back — **learning from the
screeners**, not replacing them.

## Run
```bash
python ml_screen_discovery.py --market US --limit 400
python ml_screen_discovery.py --market India --min-dollar-vol 3e6 --dev-threshold 0.5
```
Output: ranked new-screen recommendations (rule + edge vs market + deviation +
whether RL-refined) to `screen_reco.db`.

## Wiring to the rest of the system
- **Data:** OHLC via `market_store.cached_download` (Cassandra cache — no re-download).
- **Calendar:** `market_holidays.should_run_today()` gates the run; `trading_days()`
  builds clean date grids so non-trading days aren't processed (US 253 / Japan 245 /
  Korea 251 / Europe 255 sessions in 2026).
- **Existing screens:** the screener.in technical screens in `screen_viability.py`
  are the reference the RL layer anchors on.

## Honest limitations
- The "forward return" label for the *latest* bar is necessarily **trailing** (you
  can't observe the future), so the tool discovers *currently* outperforming
  patterns — good for surfacing candidate screens, not a lookahead-free backtest.
  Validate any proposed screen with `screen_viability.py --horizon 21` before use.
- Small samples are noisy; run on the full universe (`--limit` unset) for stable
  clusters. Uses sklearn proxies (PCA for UMAP, KMeans+IsolationForest for HDBSCAN,
  CEM for PPO) — same shapes, lighter deps.
