# Global DVM / Trendlyne Screening — all markets with data

`dvm_global.py` extends the Trendlyne-style approach to every market with local
OHLC (`cache_seed/cleaned_long_*.parquet`), using Trendlyne's technical metrics as
the filter criteria: **Momentum score (0-100)** + RSI, MACD, DMA-stack (50/200),
price-vs-52w-high, ADX/DMI, volume thrust, beta. Fully local — no network.

## Coverage
19 markets, **30,785 stocks scored** in one pass:
US, CN, JP, TW, CA, KR, AU, HK, SG, DE, SE, EU, UK, ZA, BR, CH, DK, FI, SA.
(Durability/Valuation need fundamentals and are US-only via EDGAR — see `dvm_engine.py`;
the cross-market subset here is Momentum + the technical filters.)

## Result — `momentum_breakout` screen (M≥70 & within 10% of 52w-high & ADX≥25 & volume thrust)

| Market | scored | hits | avg momentum |
|---|---|---|---|
| US | 8,470 | 874 | 52.8 |
| CN | 5,139 | 243 | 45.7 |
| JP | 3,081 | 193 | **56.8** (highest) |
| TW | 2,166 | 52 | 46.8 |
| CA | 2,056 | 24 | 41.1 |
| KR | 2,570 | 21 | 39.4 |
| AU | 1,485 | 17 | 43.3 |
| HK | 1,303 | 16 | 42.4 |
| … | … | … | … |
| **TOTAL** | **30,785** | **1,478** | |

## Screens available (Trendlyne pre-built types, as filters)
- `momentum_breakout` — M≥70 & near 52w-high & ADX≥25 & volume thrust
- `high_momentum` — M≥75
- `golden_crossover` — 50DMA crossed above 200DMA (last 5 sessions)
- `uptrend_quality` — above 200DMA & RSI 50–70 & ADX≥20

```bash
python dvm_global.py --screen momentum_breakout          # all markets
python dvm_global.py --markets US JP KR --screen high_momentum
```
All metrics land in `dvm_global.db` (~2 MB, one row per stock: M, RSI, ADX,
dist_52w, above_200dma, golden_cross, vol_ratio, beta).

## Notes
- Data is ~1 year of daily bars per stock, so 52-week windows are full but multi-year
  history isn't — momentum/technical only, not long-horizon.
- Beta is vs each market's equal-weight index.
