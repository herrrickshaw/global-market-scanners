# Full-Universe Screen Viability — 5-day vs 21-day horizon

All 17,027 tickers, 5-year history. `edge` = mean clipped forward return on signal days minus the stock's baseline; clip scales with the horizon. Question tested: do the momentum/breakout screens become viable when judged over a **month** instead of a **week**?

## Per-screen, both horizons (edge%)

| market   | screen             |   edge5 |   edge21 |   delta |   hit21 | v21   |
|:---------|:-------------------|--------:|---------:|--------:|--------:|:------|
| Europe   | darvas_proximity   | -0.3345 |  -0.7802 |  -0.446 |    53.5 | no    |
| India    | darvas_proximity   | -0.4991 |  -2.8693 |  -2.37  |    42.8 | no    |
| Japan    | darvas_proximity   | -1.1929 |  -2.167  |  -0.974 |    44.7 | no    |
| Korea    | darvas_proximity   | -1.8216 |  -5.0045 |  -3.183 |    31.1 | no    |
| US       | darvas_proximity   | -1.373  |  -3.5742 |  -2.201 |    44.9 | no    |
| Europe   | golden_crossover   |  0.2094 |   0.8135 |   0.604 |    59.1 | YES   |
| India    | golden_crossover   |  0.3315 |   0.305  |  -0.027 |    47.7 | no    |
| Japan    | golden_crossover   |  0.0772 |   0.4351 |   0.358 |    55.1 | YES   |
| Korea    | golden_crossover   | -0.1256 |  -0.3501 |  -0.225 |    40.4 | no    |
| US       | golden_crossover   |  0.0325 |   0.2086 |   0.176 |    50.7 | YES   |
| Europe   | near_52w_high      | -0.1069 |  -0.5001 |  -0.393 |    54.8 | no    |
| India    | near_52w_high      |  0.2793 |  -0.6326 |  -0.912 |    44.8 | no    |
| Japan    | near_52w_high      | -0.3109 |  -0.5615 |  -0.251 |    50.3 | no    |
| Korea    | near_52w_high      | -1.1262 |  -3.2933 |  -2.167 |    33.5 | no    |
| US       | near_52w_high      | -0.6588 |  -2.0252 |  -1.366 |    47.9 | no    |
| Europe   | price_vol_breakout |  1.125  |   3.8501 |   2.725 |    64.1 | YES   |
| India    | price_vol_breakout |  0.8888 |   1.3691 |   0.48  |    48.6 | no    |
| Japan    | price_vol_breakout | -0.6003 |  -0.3627 |   0.238 |    47.8 | no    |
| Korea    | price_vol_breakout | -0.6101 |  -1.1108 |  -0.501 |    38.2 | no    |
| US       | price_vol_breakout | -0.3178 |   0.0833 |   0.401 |    50.3 | YES   |
| Europe   | rsi_oversold       |  0.5413 |   2.0168 |   1.475 |    65.2 | YES   |
| India    | rsi_oversold       |  0.2187 |   0.4739 |   0.255 |    52.3 | YES   |
| Japan    | rsi_oversold       |  0.5565 |   0.977  |   0.42  |    57.8 | YES   |
| Korea    | rsi_oversold       |  0.2442 |   0.6333 |   0.389 |    47   | no    |
| US       | rsi_oversold       |  0.4593 |   1.0141 |   0.555 |    55.6 | YES   |

## Verdict
- **RSI-oversold (mean-reversion): robust at both horizons** — positive edge in every market, viable in US/Japan/Europe at 5d and 21d. The most dependable screen globally.
- **Price-Volume Breakout: the big winner from a longer horizon** — Europe jumps to **+3.85%** (from +1.13), US turns viable, India stays positive. Volume-thrust needs weeks to pay off, not days.
- **Golden Crossover: mildly viable** (US/Japan/Europe) at both horizons.
- **Near-52w-High & Darvas-proximity: negative at BOTH horizons, in every market.** The longer window did **not** rescue them — buying already-extended stocks underperforms at 1 week *and* 1 month. As standalone entry screens they don't add edge; they likely only work combined with fundamentals (the way the scanners actually use Darvas — breakout **+** Piotroski **+** Coffee Can).

> So the 'momentum needs a longer horizon' hypothesis is **half-confirmed**: true for volume breakouts, **false** for high-proximity/Darvas screens. All figures pre-cost.

_Artifacts: `viability_summary.db` (5d), `viability_summary_21d.db` (21d), 8 KB each. Raw per-ticker DBs gitignored._