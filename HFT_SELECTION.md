# HFT-archetype stock selection — from daily OHLC

[`hft_selection.py`](hft_selection.py) turns the four HFT strategy archetypes (from
the microstructure literature survey) into an actual **stock picker built only from
daily OHLC** — day's high, low, close and volume — over a **1-week window**, across
the liquid universe.

We have no tick/limit-order-book feed, so this is the **Tier-1 (universe /
tradeability) screen** each archetype implies. Each microstructure quantity is
estimated with an established **daily-data proxy**:

| Archetype | What it wants (literature) | Daily-OHLC proxy here |
|---|---|---|
| **Market making** | stable, earnable spread + low toxicity (Avellaneda-Stoikov 2008; Ho-Stoll 1981) | tight & stable daily range (Corwin-Schultz 2012 high-low spread + std of range) and **low toxicity** = low Kaufman efficiency ratio |
| **Statistical arb** | fast mean reversion (Avellaneda-Lee 2010) | negative return autocorrelation + short **Ornstein-Uhlenbeck half-life** + low efficiency ratio (choppy) |
| **Latency / order-anticipation** | predictable, persistent flow | **high efficiency ratio** (directional persistence) + **volume autocorrelation** (predictable order flow) |
| **Index/ETF arb** | the mispriced leg of a real relationship | high correlation to the **sector-peer basket** × current standardised deviation from it |

## The proxies (all pure functions of daily bars)
- **Daily range** `(High−Low)/Close`, averaged over the week — the intraday
  spread/volatility proxy the request centres on.
- **Corwin-Schultz (2012)** high-low spread estimator — a proportional bid-ask
  spread from just daily highs and lows.
- **Kaufman efficiency ratio** `|net move| / total travel` ∈ [0,1] — the toxicity /
  predictability axis: **high = trending** (informed flow, toxic for a market maker,
  good for latency); **low = choppy** (good for MM and stat-arb).
- **Ornstein-Uhlenbeck half-life** from an AR(1) fit on the price level — mean-
  reversion speed (∞ when not reverting).
- **Lag-1 autocorrelation** of returns (reversion vs persistence) and of volume
  (flow predictability).

Archetype scores are cross-sectional z-composites over the liquid names; the top
names per archetype are the picks. A tradeability gate drops penny/junk names
(>12% average daily range) before scoring.

## What it produces (US, 1-week window)
- **Market making** → the tightest, most stable, least-toxic names: currency/bond
  ETFs and closed-end funds (e.g. FXF at 0.30% daily range, efficiency ratio ≈ 0).
- **Statistical arb** → strong mean-reverters: bond CEFs with return autocorrelation
  ≈ −0.65 and sub-1-day half-lives.
- **Latency** → efficiency ratio ≈ 1.0 with high volume autocorrelation (persistent,
  predictable flow).
- **Index/ETF arb** → names most correlated to their sector basket but currently most
  deviated from it.

## Honest boundary
This is **Tier-1 only** — *which liquid names suit each archetype*. The actual HFT
edge lives in **Tier-2**: millisecond order-book signals (order-book imbalance,
order-flow imbalance, microprice, Hawkes intensities) that need a **tick/LOB feed**
we don't have (LOBSTER / NASDAQ ITCH / Refinitiv). The daily proxies here are
directionally faithful to the microstructure quantities but cannot capture
intraday execution edge. It complements [`liquidity_factor.py`](liquidity_factor.py)
(the ILLIQ/capacity screen) — together they form the platform's tradeability layer.

## Quick start
```bash
python hft_selection.py --market US --window 5 --top 10       # all four archetypes
python hft_selection.py --all --archetype stat_arb            # one archetype, all markets
```

Pure cores (`daily_range_pct`, `avg_range`, `corwin_schultz_spread`,
`efficiency_ratio`, `lag1_autocorr`, `ou_half_life`, `archetype_scores`) are covered
by [`tests/`](tests/test_core.py) and enforced by CI.
