# Post-Earnings-Announcement Drift (PEAD) — closing the scout loop

[`pead_factor.py`](pead_factor.py) implements the **post-earnings-announcement drift**
anomaly — the factor the [literature scout](SCOUT.md) flagged as the platform's **top
research gap**. This is the scout→implement→covered loop closing: the scout surfaced 7
PEAD papers with no module; now there's a module, and the scout reclassifies PEAD as
`covered` (mapped to `pead_factor.py`).

## The anomaly
After a firm reports an earnings **surprise**, its price keeps drifting in the
surprise's direction for weeks — positive surprises drift up, negative down (Ball &
Brown 1968; Bernard & Thomas 1989). It's a slow, predictable under-reaction.

## Faithful-but-honest implementation
A textbook PEAD test needs a history of quarterly EPS vs consensus and the exact
announcement dates — the global cache has neither. So, as with
[`pit_global.py`](pit_global.py), we implement the honest **price-only** version that
generalises to all 19 markets:

1. **Event proxy** — earnings days are the canonical **high-volume return-jump** days,
   so `detect_events` flags days with a volume spike (>2.5× trailing avg) coincident
   with a large move (>2σ), de-clustered by a 40-day gap.
2. **Surprise** — the market-adjusted return over the tight `[t−1, t+1]` window
   (`event_surprise`); its sign is the surprise direction.
3. **Drift** — the cumulative abnormal return over `[t+2, t+horizon]`, measured
   strictly *after* the event, so the study is **lookahead-free**. Only events with a
   complete forward window are included.
4. **Guards** — abnormal returns are clipped to ±25%/day and events with a >40% one-day
   move are dropped (splits/glitches), on a liquidity-filtered universe (top 40% by
   median dollar-volume). Without these, dirty penny-stock prints produce nonsense CARs.

For the US, `sue()` provides the genuine point-in-time **Standardised Unexpected
Earnings** path (EDGAR filing dates + YoY earnings change) — the classic sort variable.

## What the data shows
Running the event study on the platform's parquets:

| Universe | Q5−Q1 forward CAR | monotonicity | read |
|---|---|---|---|
| **US only** (60d) | ≈ −0.5% | 0.00 | weak/absent — the US is efficient and the price-proxy is noisy |
| **All 19 markets** (40d) | **+2.50%** | **0.90** | **PEAD present** — positive-surprise names drift up (+1.4%), negative down (−1.1%) |

The pooled, cross-market result shows the classic monotone drift — consistent with the
literature that PEAD is **stronger in less-efficient markets** (the same theme as the
IIMA quality paper). Reporting both is the point: the crude US proxy is honestly weak,
but the anomaly emerges cleanly in the broader sample.

## Two outputs
- **Event study** — `drift_by_surprise` gives the forward-CAR-by-surprise-quantile
  curve; `monotonicity` scores whether higher surprise ⇒ higher drift.
- **Current signal** — for each stock's most recent event still inside the drift
  window, a 0–100 `pead_score` (surprise sign × magnitude × time-decay), exported for
  the ensemble.

## Integration
Wired into [`meta_screen.py`](meta_screen.py) as a conviction component:
```bash
python pead_factor.py --all --out pead.csv
python quality_factor.py --all --out quality.csv
python meta_screen.py --quality quality.csv --pead pead.csv    # fuse all signals
```

## Quick start
```bash
python pead_factor.py --market US --horizon 60      # US event study + top signals
python pead_factor.py --all --horizon 40            # pooled 19-market study (PEAD shows here)
python pead_factor.py --all --out pead.csv          # export the signal for meta_screen
```

Pure cores (`sue`, `market_adjust`, `car`, `detect_events`, `pead_score`,
`drift_by_surprise`, `monotonicity`) are covered by [`tests/`](tests/test_core.py) and
enforced by CI.
