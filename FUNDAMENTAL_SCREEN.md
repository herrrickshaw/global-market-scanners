# Global Fundamental Screen — all 19 markets

`fundamentals_global.py` sources the Trendlyne-style fundamental metrics (P/E, P/B,
**ROE, ROA, D/E**, revenue/earnings growth, operating margin, dividend yield, market
cap, sector) via yfinance for the liquid subset of every market with local OHLC,
then runs the public fundamental screens. Grounded in the **public** Trendlyne /
screener.in metric definitions — no login.

## Result — `high_roe_low_de` (ROE > 15% & D/E < 1), top-40 liquid per market

731/760 tickers sourced (96% coverage). **187 quality names across all 19 markets.**

| Market | with funds | hits | Market | with funds | hits |
|---|---|---|---|---|---|
| TW | 40 | **20** | AU | 40 | 10 |
| CN | 40 | 17 | CH | 38 | 9 |
| US | 35 | 15 | HK | 40 | 9 |
| ZA | 40 | 15 | BR | 40 | 8 |
| FI | 40 | 12 | CA | 40 | 8 |
| KR | 40 | 11 | DE | 40 | 8 |
| SA | 40 | 11 | UK | 40 | 8 |
| JP | 40 | 7 | DK | 40 | 5 |
| SG | 40 | 5 | EU | 18* | 5 |
| SE | 40 | 4 | **TOTAL** | **731** | **187** |

\* EU under-sourced (Yahoo rate-limited some fetches); the cache is resumable — a
second pass tops it up.

**Sample hits** (high ROE, low debt):

| Market | Ticker | ROE% | D/E | P/E | rev g% | Sector |
|---|---|---|---|---|---|---|
| US | AAPL | 141 | 0.80 | 35.1 | 16.6 | Technology |
| US | MSFT | 34 | 0.30 | 22.2 | 18.3 | Technology |
| DE | RHM.DE (Rheinmetall) | 22 | 0.36 | 45.0 | 7.7 | Industrials |
| DE | SAP.DE | 16 | 0.17 | 21.6 | 6.0 | Technology |
| JP | 5803.T (Fujikura) | 32 | 0.18 | 61.4 | 22.0 | Industrials |
| JP | 5801.T (Furukawa) | 19 | 0.76 | 42.2 | 12.2 | Industrials |

## Screens available
`high_roe_low_de` · `growth_roe_lowpe` (Trendlyne "High Growth High RoE Low PE")
· `value` (P/B<3 & 0<P/E<15 & D/E<1) · `dividend` (yield>3%).

```bash
python fundamentals_global.py --top 40 --screen high_roe_low_de
python fundamentals_global.py --markets US JP DE --top 80 --screen growth_roe_lowpe
```

## Coverage / caveats
- yfinance fundamentals are a **current snapshot**, not point-in-time — fine for live
  screening; a lookahead-free *backtest* of fundamental screens needs PIT data
  (US-only, via EDGAR / `pit_fundamentals.py`).
- Coverage thins for small/illiquid non-US names; re-run (cache is resumable) to fill gaps.
- D/E normalized for yfinance's percentage quirk (÷100 when >10).
