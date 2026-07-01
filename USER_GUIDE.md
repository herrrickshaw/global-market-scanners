# User Guide

End-to-end guide to the platform. Every capability, in the order you'd typically use
them. Commands assume `pip install -r requirements.txt` is done.

- [1. Full-market scans](#1-full-market-scans)
- [2. Multi-market DVM / Trendlyne screening](#2-multi-market-dvm--trendlyne-screening)
- [3. Fundamentals & the global DVM composite](#3-fundamentals--the-global-dvm-composite)
- [4. Screen viability backtesting](#4-screen-viability-backtesting)
- [5. Point-in-time backtesting (US)](#5-point-in-time-backtesting-us)
- [6. Factor research](#6-factor-research)
- [7. ML screen discovery](#7-ml-screen-discovery)
- [8. The industry/peer dataset](#8-the-industrypeer-dataset)
- [9. Data backbone (Cassandra / Kafka / Flink)](#9-data-backbone)
- [10. Trading calendars](#10-trading-calendars)

---

## 1. Full-market scans
Full-universe Darvas + Piotroski + Coffee-Can scan → a styled Excel workbook.

```bash
python full_indian_market_scan.py                 # full run
python full_indian_market_scan.py --top 200       # first 200 tickers (quick)
python full_indian_market_scan.py --no-scans      # Darvas only, skip fundamentals
python full_us_market_scan.py --workers 10
python full_korea_market_scan.py --kospi-only
```
Output: a 4+ sheet workbook (All Stocks, Darvas Signals, Fundamentals, Triple Hits;
US/India also add Magic Formula, Golden Crossover, ML Bullish/Bearish, Multi-Screen).

## 2. Multi-market DVM / Trendlyne screening
Trendlyne-style **technical** screening across all 19 markets with local OHLC
(`cache_seed/cleaned_long_*.parquet`). Fully local — no network.

```bash
python dvm_global.py --screen trendlyne_technical   # RSI+MFI+MACD (Trendlyne public)
python dvm_global.py --screen sma_golden            # SMA50-above-SMA200
python dvm_global.py --screen momentum_breakout     # M>=70 & near-52w-high & ADX & volume
python dvm_global.py --markets US JP KR --screen high_momentum
```
Metrics per stock (→ `dvm_global.db`): Momentum(0-100), RSI, MFI, ADX, dist-52w-high,
above-200DMA, golden-cross, SMA-state, MACD, volume ratio, beta. See [GLOBAL_DVM.md](GLOBAL_DVM.md).

## 3. Fundamentals & the global DVM composite
Source Trendlyne **fundamental** metrics (ROE, D/E, P/E, P/B, growth, margin, dividend)
via yfinance, then fuse with momentum into the Trendlyne **GGG/GGB/BBG** composite.

```bash
# a) source fundamentals for the liquid subset (cached in fundamentals_cache.db, resumable)
python fundamentals_global.py --top 40 --screen high_roe_low_de

# b) fuse momentum + fundamentals -> global DVM composite ranking
python dvm_composite.py                 # global GGG "Strong Performers"
python dvm_composite.py --code GGB      # Value-Under-Radar only
```
US-only single-market DVM with EDGAR durability/valuation: `python dvm_engine.py --market US --screen high_dvm`.
See [FUNDAMENTAL_SCREEN.md](FUNDAMENTAL_SCREEN.md), [DVM_COMPOSITE.md](DVM_COMPOSITE.md).

## 4. Screen viability backtesting
Backtest the OHLC-computable screener.in screens over N years, net of tax+brokerage.

```bash
python screen_viability.py --years 5 --horizon 21    # full universe, monthly horizon
python screen_viability.py --export-summary viability_summary.db
python apply_costs.py                                # net-of-cost re-judgement
```
Findings: RSI-oversold is the most robust screen net of cost; momentum screens need a
longer horizon. See [SCREEN_VIABILITY_RESULTS.md](SCREEN_VIABILITY_RESULTS.md), [NET_OF_COST.md](NET_OF_COST.md).

## 5. Point-in-time backtesting (US)
Lookahead-free backtest using SEC EDGAR fundamentals filtered by filing date — tests
whether the fundamental gate (Piotroski, Coffee-Can) adds value over Darvas alone.

```bash
python pit_fundamentals.py AAPL          # inspect PIT fundamentals as-of dates
python pit_backtest.py --universe scan --years 5 --min-dollar-vol 2e6
```
Verdict: Darvas + F≥7 is the sweet spot; Coffee-Can over-filters. See
[SCOPE_PIT_FUNDAMENTALS.md](SCOPE_PIT_FUNDAMENTALS.md), [PIT_BACKTEST_RESULTS.md](PIT_BACKTEST_RESULTS.md).

## 6. Factor research
Test four foundational papers (Markowitz, Sharpe/CAPM, Fama, Fama-French) as
falsifiable proposals on the US universe, point-in-time.

```bash
python factor_research.py --limit 500
```
See [FACTOR_RESEARCH.md](FACTOR_RESEARCH.md).

## 7. ML screen discovery
Hybrid ML that invents new screens: supervised (known-good) → unsupervised (discover
outperforming clusters → new screen rules) → RL-from-screeners (refine when drifting).

```bash
python ml_screen_discovery.py --market US --limit 400
```
See [ML_SCREEN_DISCOVERY.md](ML_SCREEN_DISCOVERY.md).

## 8. The industry/peer dataset
Build an industry-segmented company dataset with global peers, and enrich with
unlisted firms.

```bash
python build_industry_parquet.py                    # companies_industry.parquet
python enrich_industries.py --workers 3 --rebuild   # fill India/Korea industries (yfinance)
python unlisted_enrichment.py --source gleif --merge # add unlisted firms (GLEIF, keyless)
```

## 9. Data backbone
Cassandra caches OHLC so repeated runs read locally (the real speedup); Kafka
distributes scanning; Flink does streaming aggregation. See [INFRA.md](INFRA.md).

```bash
# servers (macOS)
brew install cassandra kafka apache-flink
brew services start cassandra kafka

# OHLC via the cache (Cassandra-first, yfinance fallback)
python -c "from market_store import cached_download; print(len(cached_download(['AAPL','MSFT'])))"

python stream_pipeline.py demo                       # Kafka produce+consume round-trip
```
Continuous CDC (Cassandra → Debezium → Kafka): see [cdc/CDC_DEPLOYMENT.md](cdc/CDC_DEPLOYMENT.md).

## 11. Data warehouse (DuckDB) — filter & depict across everything
One SQL surface over all sources (7.7M+ OHLC rows + all result tables), no ETL.

```bash
python warehouse.py --show markets                 # OHLC coverage per market
python warehouse.py --show ggg_global              # global GGG Strong Performers
python warehouse.py --filter "c.code='GGG' AND f.roe>15 AND f.de<1 AND c.M>=75"
python warehouse.py --sql "SELECT market, count(*) FROM ohlc GROUP BY 1"
```
Update = just re-run the producers; views reflect the live files. See [WAREHOUSE.md](WAREHOUSE.md).

## 10. Trading calendars
Skip non-trading days to cut processing time (US/India/Japan/Korea/Europe).

```python
from market_holidays import is_trading_day, trading_days, should_run_today
should_run_today("NSE")                 # gate a daily pipeline
trading_days("US", "2026-01-01", "2026-12-31")
```

---

## Data locations
- Scan outputs / OHLC seed: `~/Downloads/data/**` and `~/Downloads/code/python_files/cache_seed/`
- Committed artifacts: parquet dataset (LFS), compact `*_summary.db` / result CSVs
- Gitignored caches: `edgar_facts.db`, `fundamentals_cache.db`, `viability.db`, raw scan xlsx

## Conventions
- Compact SQLite / parquet for committed results; heavy caches gitignored.
- Fundamentals: **point-in-time** (US/EDGAR) vs **current snapshot** (global/yfinance) — noted per module.
- All figures pre-slippage unless stated. Not investment advice.
