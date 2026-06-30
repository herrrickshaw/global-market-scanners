# Screener coverage — screener.in/screens/ → this repo

Mapping of the popular [screener.in](https://www.screener.in/screens/) screens to
this repo, and how each is viability-tested.

## Price/technical screens — backtested by `screen_viability.py`
These are computable from OHLC alone, so their 5-year forward-return viability is
measured across the full universe of all 5 markets and stored in `viability.db`
(summary in `viability_summary.db`).

| screener.in screen | key in `screen_viability.py` | rule |
|---|---|---|
| RSI – Oversold Stocks | `rsi_oversold` | RSI(14) < 30 |
| Companies creating new high | `near_52w_high` | within 10% of 52-week high |
| Price Volume Action | `price_vol_breakout` | volume ≥ 5× 20-day avg on an up day |
| Darvas Scan / Breakout stocks | `darvas_proximity` | ≤10% below 52w high, ≥10% above 52w low, price>10, vol>1e5 |
| Golden Crossover | `golden_crossover` | 50DMA crosses above 200DMA |
| (ML overlay) | `ml_bullish` | `ml_signal_engine` Ridge BULLISH (`--include-ml`) |

## Fundamental screens — implemented in `strategies/` (need point-in-time financials)
These already exist as strategy modules in the system-integrated scanners; they are
**not** OHLC-backtestable here because they require historical fundamentals.

| screener.in screen | strategy module |
|---|---|
| Magic Formula | `strategies/magic_formula.py` |
| Piotroski Scan | `strategies/piotroski.py` |
| Coffee Can Portfolio | `strategies/coffee_can.py` |
| Debt reduction | `strategies/debt_reduction.py` |
| Loss to Profit Companies | `strategies/loss_to_profit.py` |
| Highest Dividend Yield | `strategies/dividend_yield.py` |
| Bluest of the Blue Chips | `strategies/bluest_blue_chips.py` |
| High Growth High RoE Low PE / GARP | `strategies/garp.py` |
| The Bull Cartel | US scan `Bull_Cartel` sheet |

Not yet implemented (fundamental, candidates to add): FII Buying, Low on 10-year
average earnings (Graham), Growth Stocks (G-Factor), Capacity expansion,
Benjamin Graham & Warren Buffett, Quarterly Growers, Value Stocks, Multibagger.

## Run
```bash
python screen_viability.py --years 5                 # full universe → viability.db
python screen_viability.py --include-ml --limit 200  # add ML overlay (slower)
python screen_viability.py --export-summary viability_summary.db   # tiny committable DB
```
