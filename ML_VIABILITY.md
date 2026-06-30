# ML Screen — 5-Year Cross-Market Viability

Walk-forward (no-lookahead) backtest of the `ml_signal_engine` Ridge directional
signal applied to a liquid index universe in each market over the last 5 years.
A market is **VIABLE** if the ML-bullish screen beats buy-and-hold (`edge>0`),
its bullish calls are right more than half the time (`bull_hit%>50`), and overall
directional accuracy exceeds chance (`dir_acc%>50`).

| Market   |   n_stocks |   n_preds |   dir_acc% |   rmse |   mae |   base_fwd% |   bull_fwd% |   edge% |   bull_hit% |   bull_n | VIABLE   |
|:---------|-----------:|----------:|-----------:|-------:|------:|------------:|------------:|--------:|------------:|---------:|:---------|
| US       |         12 |      1763 |       53.9 |  6.713 | 5.035 |       0.494 |       0.749 |   0.254 |        60   |      886 | YES      |
| India    |         12 |      1529 |       54.2 |  4.772 | 3.667 |       0.176 |       0.471 |   0.295 |        55.9 |      716 | YES      |
| Japan    |         10 |      1409 |       52.6 |  8.34  | 6.346 |       0.585 |       1.061 |   0.476 |        53.3 |      698 | YES      |
| Korea    |          8 |      1120 |       51.1 | 10.158 | 7.668 |       0.741 |       1.539 |   0.798 |        53.4 |      573 | YES      |
| Europe   |         10 |      1510 |       54   |  5.953 | 4.608 |       0.311 |       0.741 |   0.43  |        59.1 |      729 | YES      |

**Result: the ML-bullish screen is viable in all 5 markets.** Directional accuracy
sits at 51–54% (modest but above chance everywhere), and the bullish screen adds
positive 5-day forward edge over buy-and-hold in every market — largest in Korea
(+0.80%) and Japan (+0.48%), most reliable hit-rate in the US (60%) and Europe (59%).

_Universe = index heavyweights per market (8–12 names). Re-run at full scale via
`python ml_viability.py --years 5` (no `--top`)._