# Global Market Scanners & Multi-Market Quant Platform

A research platform for **multi-market equity screening, backtesting, and factor
research** — spanning classic technical/fundamental scanners, a point-in-time
backtesting engine, a hybrid ML screen-discovery layer, Trendlyne-style DVM scoring
across 19 markets, and a Cassandra/Kafka/Flink data backbone.

> **Scope:** stock-market research only. Retail-outlet / highway data lives in the
> separate [`retail-outlet-monitoring`](https://github.com/herrrickshaw/retail-outlet-monitoring)
> and [`fuel-retail-outlets`](https://github.com/herrrickshaw/fuel-retail-outlets) repos.

📓 **New here?** Open the zero-install tour in Colab, or read the **[User Guide](USER_GUIDE.md)**.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/herrrickshaw/global-market-scanners/blob/main/notebooks/DVM_Global_Colab.ipynb)

---

## What's inside

| Area | Modules | What it does |
|---|---|---|
| **Scanners** | `full_{us,indian,japan,korea,european}_market_scan.py` | Full-universe Darvas + Piotroski + Coffee-Can scans → styled Excel |
| **ML signal** | `ml_signal_engine.py`, `ml_viability.py` | Ridge directional signal + 5-year cross-market viability |
| **Screen viability** | `screen_viability.py`, `apply_costs.py` | Backtest the screener.in technical screens, net of tax+brokerage |
| **Point-in-time backtest** | `pit_fundamentals.py`, `pit_backtest.py` | Lookahead-free Triple-Hit backtest (SEC EDGAR, filed-date filtered) |
| **Factor research** | `factor_research.py` | Test Markowitz/Sharpe/Fama/Fama-French as falsifiable proposals |
| **Quality factor (QMJ)** | `quality_factor.py` | AFP/QMJ quality factor (IIMA 2022) — profitability/growth/safety/payout, LQ/QMJ portfolios, price-premium test |
| **Literature scout** | `literature_scout.py` | Scouts OpenAlex/Crossref/arXiv for equity research, scores vs implemented themes, flags research gaps |
| **PEAD factor** | `pead_factor.py` | Post-earnings-announcement drift (the scout's top gap) — price-based event study + drift signal |
| **Liquidity factor** | `liquidity_factor.py` | Amihud illiquidity + liquidity premium (scout gap #2) — capacity/tradeability score |
| **ML screen discovery** | `ml_screen_discovery.py` | Supervised → Unsupervised (new screens) → RL-from-screeners |
| **DVM / Trendlyne** | `dvm_engine.py`, `dvm_global.py`, `fundamentals_global.py`, `dvm_composite.py` | Durability/Valuation/Momentum scoring + GGG classification across 19 markets |
| **Decision layer** | `portfolio.py`, `risk.py`, `meta_screen.py`, `sector_rotation.py`, `alerts.py`, `unlisted_valuation.py` | Signals → constrained portfolios, risk/VaR, ensemble conviction, sector rotation, alerts, private-firm comps |
| **Global rigor** | `pit_global.py`, `fx.py` | Price-based point-in-time backtest for 18 non-US markets + FX-normalised cross-market comparison |
| **Serving & observability** | `serve.py`, `dashboard.py`, `incremental.py`, `feature_cache.py`, `data_quality.py` | FastAPI + HTML dashboard over the warehouse, partition-incremental refresh, ML feature cache, data-quality monitor |
| **Dataset** | `build_industry_parquet.py`, `enrich_industries.py`, `unlisted_enrichment.py` | Industry/peer parquet + unlisted-firm enrichment |
| **Data backbone** | `market_store.py` (Cassandra), `stream_pipeline.py` (Kafka), `flink_screens.py`, `cdc/` | Persistent OHLC cache, streaming, CDC |
| **Utilities** | `market_holidays.py`, `market_data_cache.py`, `stock_utils.py` | Trading calendars, caching, helpers |

---

## Quick start

```bash
pip install -r requirements.txt

# 1) Scan a full market  ->  styled Excel workbook
python full_indian_market_scan.py --top 200

# 2) Trendlyne-style technical screen across all 19 markets (local data, no network)
python dvm_global.py --screen trendlyne_technical

# 3) Global DVM composite ranking (GGG/GGB/BBG)
python fundamentals_global.py --top 40      # source fundamentals once (cached)
python dvm_composite.py                       # fuse momentum + fundamentals -> ranking

# 4) Point-in-time US backtest (does the fundamental gate add value?)
python pit_backtest.py --universe scan --years 5
```

See the **[User Guide](USER_GUIDE.md)** for the full workflow and every module.

---

## The core scan pipeline

Each scanner runs the same 5 stages: **universe fetch → bulk OHLC → Darvas Box
classification → fundamentals on breakout candidates → styled Excel export**.

**Triple Hit** = Darvas breakout **+** Piotroski F-Score ≥ 7/9 **+** Coffee-Can pass
(revenue CAGR > 10%, avg ROCE > 15%, D/E < 1, positive earnings every year, positive FCF).

| Scanner | Market | Universe | Data source |
|---|---|---|---|
| `full_us_market_scan.py` | USA (NYSE + NASDAQ) | ~5,400 | yfinance + EDGAR |
| `full_indian_market_scan.py` | India (NSE + BSE) | ~4,600 | nsepython + bseindia + yfinance |
| `full_japan_market_scan.py` | Japan (TSE) | ~3,600 | kabupy + yfinance |
| `full_korea_market_scan.py` | Korea (KOSPI + KOSDAQ) | ~2,600 | pykrx + yfinance |
| `full_european_market_scan.py` | Europe (Euro Stoxx 50) | 50 | yfinance |

---

## Documentation index

**Guides**
- [User Guide](USER_GUIDE.md) — end-to-end workflows for every capability
- [Security & Integrity](SECURITY.md) — signed commits, checksum manifest, branch protection
- [Performance](PERFORMANCE.md) — measured bottlenecks & fixes
- [SAFe delivery](safe/SAFE.md) — the platform as a queryable Scaled-Agile backlog (`safe/safe_backlog.py`)
- [TOGAF architecture](architecture/TOGAF.md) — principles catalog + ADM mapping + executable governance (`architecture/togaf.py`)
- [SDLC](SDLC.md) — life-cycle phase mapping + the test suite (`tests/`) & CI (`.github/workflows/ci.yml`)
- [Architecture](ARCHITECTURE.md) — mapped to the Modern Data Architecture Blueprint
- [Architecture Map](ARCHITECTURE_MAP.md) — data-flow + tier + ER diagrams (Mermaid)
- [Data Schema](SCHEMA.md) — data dictionary for every store
- [Data warehouse (DuckDB)](WAREHOUSE.md) — one SQL surface to filter/depict across everything
- [Data backbone (Cassandra/Kafka/Flink)](INFRA.md) · [CDC deployment](cdc/CDC_DEPLOYMENT.md)

**Screening & scoring**
- [Screener.in coverage](SCREENS.md) · [Global DVM / Trendlyne](GLOBAL_DVM.md)
- [Global fundamental screen](FUNDAMENTAL_SCREEN.md) · [Global DVM composite](DVM_COMPOSITE.md)
- [ML screen discovery](ML_SCREEN_DISCOVERY.md)

**Backtesting & research**
- [Screen viability results](SCREEN_VIABILITY_RESULTS.md) · [Net of cost](NET_OF_COST.md)
- [Point-in-time fundamentals scope](SCOPE_PIT_FUNDAMENTALS.md) · [PIT backtest results](PIT_BACKTEST_RESULTS.md)
- [ML viability](ML_VIABILITY.md) · [Factor research](FACTOR_RESEARCH.md)
- [Quality factor (QMJ)](QUALITY_FACTOR.md) — Asness-Frazzini-Pedersen QMJ per IIMA W.P. 2022-11-01, generalised to 19 markets
- [Literature scout](SCOUT.md) — automated global research scout (OpenAlex/Crossref/arXiv) with research-gap detection
- [PEAD factor](PEAD.md) — post-earnings-announcement drift; the scout→implement→covered loop closed
- [Liquidity factor](LIQUIDITY.md) — Amihud illiquidity + liquidity premium; scout gap #2 closed
- [Data sources](DATA_SOURCES.md) — per-market public factor libraries (AQR/Ken French/IIMA IFFM); what the quality paper names

**Decision layer & consumption**
- [Decision layer, global rigor & observability](DECISION_LAYER.md) — portfolio / risk / meta-screen / rotation / alerts / comps + global PIT + FX + data quality + serving

---

## Install & requirements

```bash
pip install -r requirements.txt
```
Python 3.9+. Core: pandas, numpy, pyarrow, scikit-learn, yfinance, openpyxl. Market
libs: nsepython, bseindia, kabupy, pykrx. Optional data backbone: cassandra-driver,
confluent-kafka, apache-flink (servers via `brew install cassandra kafka apache-flink`).
See [`requirements.txt`](requirements.txt) and [INFRA.md](INFRA.md).

## Notes
Not investment advice — research only. Backtests are pre-slippage unless stated;
fundamentals are point-in-time (US/EDGAR) or current-snapshot (global/yfinance) as
noted per module.
