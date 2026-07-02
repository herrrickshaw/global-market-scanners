# Liquidity factor — Amihud illiquidity (scout gap #2)

[`liquidity_factor.py`](liquidity_factor.py) implements the **liquidity factor** —
the second research gap the [literature scout](SCOUT.md) surfaced after PEAD. It
computes Amihud's (2002) **illiquidity** measure and the associated **liquidity
premium** across all 19 markets, using only price and volume (already in the cache).

## The measure
```
ILLIQ_i = average over the window of  |return_i,t| / dollar_volume_i,t   (× 1e6)
```
A stock whose price jumps a lot per dollar traded is illiquid → high ILLIQ. Amihud
(2002), Amihud-Mendelson (1986) and Pastor-Stambaugh (2003) show illiquid stocks
must offer **higher expected returns** to compensate for trading frictions.

## Two uses, deliberately opposite
The module exposes both, because they point different ways:

| Output | Meaning | Direction |
|---|---|---|
| **liquidity premium** (`premium_by_illiq`) | forward return by ILLIQ quantile | a *return* signal — illiquid → higher return, but hard to harvest net of the very costs that cause it |
| **capacity score** (`capacity_score`) | 0–100 tradeability, 100 = liquid | a *risk* lens — size up liquid names, cap illiquid ones; the retail-appropriate use |

## What the data shows
- **Capacity score — validated.** The most-liquid names it identifies are exactly the
  world's megacaps: NVDA, SPY, QQQ, TSLA, MSFT, AAPL, AMZN, TSMC (2330.TW),
  Mitsubishi UFJ (8306.T), … — strong evidence the ILLIQ computation is correct.
- **Liquidity premium — weak in this window.** Pooled across 19 markets (29,418
  stock-observations, 21-day forward) the Q5−Q1 premium is **+1.97% gross** but the
  curve is not cleanly monotone (monotonicity 0.60). Honest read: the *measure* is
  solid; the *premium* is noisy over the ~1 year of seed data and, crucially, is
  **gross** — the trading costs that create illiquidity also erode the premium
  (that's exactly why [`apply_costs.py`](apply_costs.py) and `portfolio.py`'s
  capacity-aware caps matter).

## Integration
- **meta_screen** — `capacity_score` is a fusion component (`--liquidity`): a
  tradeability tilt that nudges conviction toward names a retail book can actually
  trade, echoing the quality paper's "accessible to retail" theme.
- **portfolio / risk** — the capacity score is the natural input for tightening
  position caps on illiquid names (`portfolio.py`) and reading portfolio capacity.

```bash
python liquidity_factor.py --all --out liquidity.csv
python meta_screen.py --quality quality.csv --pead pead.csv --liquidity liquidity.csv
```

## Quick start
```bash
python liquidity_factor.py --market US            # ILLIQ + liquidity-premium study
python liquidity_factor.py --all                  # pooled 19-market study + capacity ranking
python liquidity_factor.py --all --out liquidity.csv
```

Pure cores (`amihud_illiq`, `zero_return_frac`, `capacity_score`, `illiq_pctile`,
`premium_by_illiq`, `monotonicity`) are covered by [`tests/`](tests/test_core.py) and
enforced by CI. Windows default to ~6-month lookback / 1-month forward to fit the
seed data's ~1 trading year.
