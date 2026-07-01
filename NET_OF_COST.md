# Screen Viability — NET of local tax + brokerage

Pre-cost edges minus a per-market round-trip cost (retail brokerage + local statutory taxes). Round-trip cost assumptions (% of trade value):

| Market | round-trip cost | main components |
|---|---|---|
| India | 0.30% | STT 0.20% + brokerage ~0.06% + stamp/exch/GST ~0.04% |
| US | 0.10% | ~$0 commission + SEC/TAF ~0.01% + ~0.09% spread |
| Japan | 0.20% | brokerage ~0.10% + no STT + ~0.10% spread |
| Korea | 0.25% | STT ~0.18% (sell) + brokerage ~0.03% + ~0.04% |
| Europe | 0.40% | French FTT 0.30% + brokerage ~0.20% (blended FR/DE/NL) |

`net_edge = gross_edge − round_trip_cost`. `net_viable = net_edge > 0 AND hit% > 50`.


## 5d horizon  (6/25 screens keep positive edge net of cost; 12 were positive pre-cost)

| market   | screen             |   gross_edge |   cost% |   net_edge |   hit% | net_viable   |
|:---------|:-------------------|-------------:|--------:|-----------:|-------:|:-------------|
| Europe   | price_vol_breakout |       1.125  |    0.4  |      0.725 |   42.2 | no           |
| Europe   | rsi_oversold       |       0.5413 |    0.4  |      0.141 |   59.5 | YES          |
| Europe   | golden_crossover   |       0.2094 |    0.4  |     -0.191 |   53.5 | no           |
| Europe   | near_52w_high      |      -0.1069 |    0.4  |     -0.507 |   54.3 | no           |
| Europe   | darvas_proximity   |      -0.3345 |    0.4  |     -0.734 |   52   | no           |
| India    | price_vol_breakout |       0.8888 |    0.3  |      0.589 |   47.4 | no           |
| India    | golden_crossover   |       0.3315 |    0.3  |      0.032 |   46.8 | no           |
| India    | near_52w_high      |       0.2793 |    0.3  |     -0.021 |   44.5 | no           |
| India    | rsi_oversold       |       0.2187 |    0.3  |     -0.081 |   49.7 | no           |
| India    | darvas_proximity   |      -0.4991 |    0.3  |     -0.799 |   45   | no           |
| Japan    | rsi_oversold       |       0.5565 |    0.2  |      0.356 |   55.9 | YES          |
| Japan    | golden_crossover   |       0.0772 |    0.2  |     -0.123 |   51.1 | no           |
| Japan    | near_52w_high      |      -0.3109 |    0.2  |     -0.511 |   48.9 | no           |
| Japan    | price_vol_breakout |      -0.6003 |    0.2  |     -0.8   |   44.2 | no           |
| Japan    | darvas_proximity   |      -1.1929 |    0.2  |     -1.393 |   44   | no           |
| Korea    | rsi_oversold       |       0.2442 |    0.25 |     -0.006 |   48.2 | no           |
| Korea    | golden_crossover   |      -0.1256 |    0.25 |     -0.376 |   43.2 | no           |
| Korea    | price_vol_breakout |      -0.6101 |    0.25 |     -0.86  |   40.4 | no           |
| Korea    | near_52w_high      |      -1.1262 |    0.25 |     -1.376 |   37.8 | no           |
| Korea    | darvas_proximity   |      -1.8216 |    0.25 |     -2.072 |   37.2 | no           |
| US       | rsi_oversold       |       0.4593 |    0.1  |      0.359 |   53.6 | YES          |
| US       | golden_crossover   |       0.0325 |    0.1  |     -0.068 |   50.2 | no           |
| US       | price_vol_breakout |      -0.3178 |    0.1  |     -0.418 |   47.2 | no           |
| US       | near_52w_high      |      -0.6588 |    0.1  |     -0.759 |   47.9 | no           |
| US       | darvas_proximity   |      -1.373  |    0.1  |     -1.473 |   46.6 | no           |

## 21d horizon  (11/25 screens keep positive edge net of cost; 12 were positive pre-cost)

| market   | screen             |   gross_edge |   cost% |   net_edge |   hit% | net_viable   |
|:---------|:-------------------|-------------:|--------:|-----------:|-------:|:-------------|
| Europe   | price_vol_breakout |       3.8501 |    0.4  |      3.45  |   64.1 | YES          |
| Europe   | rsi_oversold       |       2.0168 |    0.4  |      1.617 |   65.2 | YES          |
| Europe   | golden_crossover   |       0.8135 |    0.4  |      0.414 |   59.1 | YES          |
| Europe   | near_52w_high      |      -0.5001 |    0.4  |     -0.9   |   54.8 | no           |
| Europe   | darvas_proximity   |      -0.7802 |    0.4  |     -1.18  |   53.5 | no           |
| India    | price_vol_breakout |       1.3691 |    0.3  |      1.069 |   48.6 | no           |
| India    | rsi_oversold       |       0.4739 |    0.3  |      0.174 |   52.3 | YES          |
| India    | golden_crossover   |       0.305  |    0.3  |      0.005 |   47.7 | no           |
| India    | near_52w_high      |      -0.6326 |    0.3  |     -0.933 |   44.8 | no           |
| India    | darvas_proximity   |      -2.8693 |    0.3  |     -3.169 |   42.8 | no           |
| Japan    | rsi_oversold       |       0.977  |    0.2  |      0.777 |   57.8 | YES          |
| Japan    | golden_crossover   |       0.4351 |    0.2  |      0.235 |   55.1 | YES          |
| Japan    | price_vol_breakout |      -0.3627 |    0.2  |     -0.563 |   47.8 | no           |
| Japan    | near_52w_high      |      -0.5615 |    0.2  |     -0.762 |   50.3 | no           |
| Japan    | darvas_proximity   |      -2.167  |    0.2  |     -2.367 |   44.7 | no           |
| Korea    | rsi_oversold       |       0.6333 |    0.25 |      0.383 |   47   | no           |
| Korea    | golden_crossover   |      -0.3501 |    0.25 |     -0.6   |   40.4 | no           |
| Korea    | price_vol_breakout |      -1.1108 |    0.25 |     -1.361 |   38.2 | no           |
| Korea    | near_52w_high      |      -3.2933 |    0.25 |     -3.543 |   33.5 | no           |
| Korea    | darvas_proximity   |      -5.0045 |    0.25 |     -5.254 |   31.1 | no           |
| US       | rsi_oversold       |       1.0141 |    0.1  |      0.914 |   55.6 | YES          |
| US       | golden_crossover   |       0.2086 |    0.1  |      0.109 |   50.7 | YES          |
| US       | price_vol_breakout |       0.0833 |    0.1  |     -0.017 |   50.3 | no           |
| US       | near_52w_high      |      -2.0252 |    0.1  |     -2.125 |   47.9 | no           |
| US       | darvas_proximity   |      -3.5742 |    0.1  |     -3.674 |   44.9 | no           |

## Bottom line
Costs are small relative to the strongest signals but decisive for the marginal ones. Screens with sub-cost gross edge (e.g. Golden Crossover's ~0.03–0.2% in some markets, US Price-Volume) flip to **not viable** once tax+brokerage are paid — only the higher-edge screens (RSI-oversold, Europe/India volume breakouts at the monthly horizon) survive net of cost. Per-signal edges this thin also assume no slippage beyond the spread proxy.