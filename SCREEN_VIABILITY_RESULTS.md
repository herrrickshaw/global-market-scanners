# Full-Universe Screen Viability — 5 Years

Backtest of the OHLC-computable screener.in screens across **all 17,027 tickers** in the 5 market universes (US 6,218 · India 4,587 · Japan 3,566 · Korea 2,606 · Europe 50), 5-year daily history. Metric = realised 5-day forward return on signal days vs the stock's own baseline (returns clipped ±30% to remove penny-stock spikes). `viable` = avg_edge>0 AND avg_hit%>50.

| market   | screen             |   n_tickers |   total_signals |   avg_hit_pct |   avg_edge |   pct_tickers_pos_edge | viable   |
|:---------|:-------------------|------------:|----------------:|--------------:|-----------:|-----------------------:|:---------|
| Europe   | price_vol_breakout |          16 |              24 |          42.2 |     1.125  |                   43.8 | no       |
| Europe   | rsi_oversold       |          50 |            5879 |          59.5 |     0.5413 |                   86   | YES      |
| Europe   | golden_crossover   |          50 |             152 |          53.5 |     0.2094 |                   54   | YES      |
| Europe   | near_52w_high      |          50 |           32797 |          54.3 |    -0.1069 |                   26   | no       |
| Europe   | darvas_proximity   |          49 |           27314 |          52   |    -0.3345 |                   32.7 | no       |
| India    | price_vol_breakout |        4049 |           71432 |          47.4 |     0.8888 |                   54.7 | no       |
| India    | golden_crossover   |        3832 |           10909 |          46.8 |     0.3315 |                   47.2 | no       |
| India    | near_52w_high      |        3999 |         1020143 |          44.5 |     0.2793 |                   54.5 | no       |
| India    | rsi_oversold       |        4076 |          608211 |          49.7 |     0.2187 |                   60.5 | no       |
| India    | darvas_proximity   |        3198 |          380718 |          45   |    -0.4991 |                   45.6 | no       |
| Japan    | rsi_oversold       |        3564 |          429360 |          55.9 |     0.5565 |                   78   | YES      |
| Japan    | golden_crossover   |        3540 |           10728 |          51.1 |     0.0772 |                   48.9 | YES      |
| Japan    | near_52w_high      |        3544 |         1590175 |          48.9 |    -0.3109 |                   41.5 | no       |
| Japan    | price_vol_breakout |        3175 |           26768 |          44.2 |    -0.6003 |                   38.4 | no       |
| Japan    | darvas_proximity   |        3246 |          563931 |          44   |    -1.1929 |                   35   | no       |
| Korea    | rsi_oversold       |        2540 |          456992 |          48.2 |     0.2442 |                   62.3 | no       |
| Korea    | golden_crossover   |        2462 |            7655 |          43.2 |    -0.1256 |                   44.6 | no       |
| Korea    | price_vol_breakout |        2526 |           47597 |          40.4 |    -0.6101 |                   37.9 | no       |
| Korea    | near_52w_high      |        2460 |          381794 |          37.8 |    -1.1262 |                   39.6 | no       |
| Korea    | darvas_proximity   |        2360 |          175955 |          37.2 |    -1.8216 |                   33.2 | no       |
| US       | rsi_oversold       |        5360 |          745161 |          53.6 |     0.4593 |                   70.2 | YES      |
| US       | golden_crossover   |        5050 |           16229 |          50.2 |     0.0325 |                   48.9 | YES      |
| US       | price_vol_breakout |        4023 |           28836 |          47.2 |    -0.3178 |                   44.9 | no       |
| US       | near_52w_high      |        5224 |         2042389 |          47.9 |    -0.6588 |                   32.9 | no       |
| US       | darvas_proximity   |        4184 |          945124 |          46.6 |    -1.373  |                   31.5 | no       |

## Takeaways
- **RSI-oversold (mean-reversion) is the most robust screen** — positive edge in *all five* markets, flagged viable in US, Japan, Europe; 70–86% of tickers show positive edge. The 5-day bounce after RSI<30 is consistent globally.
- **Golden Crossover** is marginally viable (US/Japan/Europe) with small edges.
- **Momentum/high-proximity screens (near-52w-high, Darvas-proximity) have negative edge almost everywhere** at this 5-day horizon — buying already-extended stocks underperforms short-term. These screens are built for *longer* holding periods (weeks–months), so a 5-day forward window understates them.
- **Price-volume breakout** works in India/Europe, not US/Japan/Korea.

> Horizon caveat: FWD=5 days favours mean-reversion over momentum. Re-run with a longer horizon to fairly judge the momentum screens. Results are pre-cost.

_Source: `screen_viability.py` → `viability_summary.db` (compact). Raw per-ticker detail in `viability.db` (gitignored)._