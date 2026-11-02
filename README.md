# Global Market Scanners and Multi-Market Quant Platform

Full-universe equity scanners (Darvas box, Piotroski F-Score and Coffee Can) for the US, Europe,
India, Japan and Korea, plus a research platform around them: point-in-time backtests, factor
studies, DVM scoring across 19 markets and a derived industry/peer parquet dataset. The scanners
write styled Excel workbooks; the research modules write Markdown reports and parquet/DuckDB tables.

> Status: active research code. Research and education only, not investment advice. Backtests are
> pre-slippage unless stated; fundamentals are point-in-time (US/EDGAR) or a current snapshot
> (yfinance) as noted per module. Retail-outlet and highway data live in the separate
> [`retail-outlet-monitoring`](https://github.com/herrrickshaw/retail-outlet-monitoring) and
> [`fuel-retail-outlets`](https://github.com/herrrickshaw/fuel-retail-outlets) repos.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/herrrickshaw/global-market-scanners/blob/main/notebooks/DVM_Global_Colab.ipynb)

New here? Read the [User Guide](USER_GUIDE.md) or
[How to use this repo with AI](HOW_TO_USE_WITH_AI.md).

## What it does
- Scans a whole exchange universe (US ~5,400, India ~4,600, Japan ~3,600, Korea ~2,600, Euro Stoxx 50)
  for Darvas-box breakouts, then scores breakout candidates with Piotroski and Coffee Can.
- Flags a "Triple Hit": Darvas breakout, Piotroski F-Score of at least 7/9, and a Coffee Can pass
  (revenue CAGR > 10%, average ROCE > 15%, D/E < 1, positive earnings every year, positive FCF).
- Runs a lookahead-free point-in-time backtest on SEC EDGAR filings (filed-date filtered).
- Scores Durability/Valuation/Momentum (Trendlyne-style) and classifies GGG/GGB/BBG across 19 markets.
- Builds `companies_industry.parquet` and `industry_segments.parquet`: company names by industry with
  global peers attached.
- Serves the warehouse through FastAPI (`serve.py`) and a Cassandra/Kafka/Flink data backbone.

## Quick start
```bash
git clone https://github.com/herrrickshaw/global-market-scanners
cd global-market-scanners
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python full_indian_market_scan.py --top 200      # headline output: styled Excel workbook
```

Other entry points (unchanged):
```bash
python dvm_global.py --screen trendlyne_technical    # 19 markets, local data, no network
python fundamentals_global.py --top 40 && python dvm_composite.py
python pit_backtest.py --universe scan --years 5
```

## How it works
Each scanner runs the same five stages:

| Stage | Module | Output |
|---|---|---|
| 1. Universe fetch | `full_<market>_market_scan.py` (NSE/BSE, NASDAQ Trader/NCDS, JPX/kabupy, KRX/pykrx, fixed Euro Stoxx list) | symbol list, cached |
| 2. Bulk OHLC | same module (`bulk_download_ohlc`, yfinance batches) | per-ticker frames |
| 3. Darvas box | `scanner_core/darvas.py` | BREAKOUT_BUY / BREAKDOWN_SELL / IN_BOX / NO_BOX |
| 4. Fundamentals on candidates | `fundamental_scan()` in each scanner (Piotroski, Coffee Can) | scores per candidate |
| 5. Export | `save_excel()` / `style_sheet()` | `<market>_full_scan/` workbooks |

Design notes:
- **One Darvas algorithm, five profiles.** `scanner_core.darvas.locate_box` is the single pure
  implementation; `DarvasProfile` records the per-market differences (column lookup, rounding, result
  keys) and `PROFILES` is a registry keyed by market name. Each scanner's `compute_darvas_box` is a
  one-line delegate, so existing imports and CLI behaviour are unchanged.
- **The current bar is excluded from box formation** to avoid lookahead contamination.
- **Shared statement helpers** (`first_df`, `row`, `series`) live in `stock_utils.py`.
- Output shapes differ per market on purpose (for example Japan/Korea round to whole currency units
  and use `upside_pct`/`pos_in_box`); `tests/golden_darvas.json` pins every variant.

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
| **Data sources & benchmark** | `data_sources.py`, `benchmark.py` | Per-market public factor libraries; fetches real Ken French factors + validates our quality tilt |
| **HFT selection** | `hft_selection.py` | 4 HFT archetypes (market-making/stat-arb/latency/ETF-arb) as a Tier-1 picker from daily OHLC |
| **Darvas × volume** | `darvas_volume.py` | Darvas-box monitor for volume acquisition (OBV/CMF/up-down vol) + HFT footprint; coils & breakouts |
| **Watchlists** | `watchlists.py` | Two separate ranked lists — fundamentally strong (QMJ) and being accumulated (Darvas volume) — + intersection |
| **Accumulation screener** | `accumulation_screener.py` | CMF + accumulation screen, validated vs 1m/6m forward returns (6m Q5−Q1 +10.8%, monotone) |
| **Corporate actions** | `corporate_actions.py` | Stock-split screener (yfinance-authoritative) + rights-issue/dilution candidate screener |
| **Frontier factors** | `seasonality.py`, `peer_network.py`, `crowding.py`, `news_sentiment.py`, `options_iv.py`, `esg_screen.py` | The scout's 7 frontier gaps closed: calendar effects, peer lead-lag, crowding, news sentiment, option-implied, ESG |
| **Earnings × liquidity** | `earnings_liquidity.py` | Conditions PEAD on liquidity/volume/price; announcement-day volume surge (median 3.8×) |
| **ML screen discovery** | `ml_screen_discovery.py` | Supervised → Unsupervised (new screens) → RL-from-screeners |
| **DVM / Trendlyne** | `dvm_engine.py`, `dvm_global.py`, `fundamentals_global.py`, `dvm_composite.py` | Durability/Valuation/Momentum scoring + GGG classification across 19 markets |
| **Decision layer** | `portfolio.py`, `risk.py`, `meta_screen.py`, `sector_rotation.py`, `alerts.py`, `unlisted_valuation.py` | Signals → constrained portfolios, risk/VaR, ensemble conviction, sector rotation, alerts, private-firm comps |
| **Global rigor** | `pit_global.py`, `fx.py` | Price-based point-in-time backtest for 18 non-US markets + FX-normalised cross-market comparison |
| **Serving & observability** | `serve.py`, `dashboard.py`, `incremental.py`, `feature_cache.py`, `data_quality.py` | FastAPI + HTML dashboard over the warehouse, partition-incremental refresh, ML feature cache, data-quality monitor |
| **Dataset** | `build_industry_parquet.py`, `enrich_industries.py`, `unlisted_enrichment.py` | Industry/peer parquet + unlisted-firm enrichment |
| **Data backbone** | `market_store.py` (Cassandra), `stream_pipeline.py` (Kafka), `flink_screens.py`, `cdc/` | Persistent OHLC cache, streaming, CDC |
| **Utilities** | `market_holidays.py`, `market_data_cache.py`, `stock_utils.py`, `marketdata.py` | Trading calendars, caching, shared reusable blocks |
| **Write surface (CRUD)** | `watchlist_store.py` (+ `serve.py` REST) | Versioned CRUD for watchlists — append-only, full history, soft-delete/restore/time-travel |


## Repository layout
| Path | Purpose |
|---|---|
| `full_{us,indian,japan,korea,european}_market_scan.py` | The five scanner CLIs |
| `scanner_core/` | Shared, pure scanner logic (Darvas engine and profile registry) |
| `stock_utils.py`, `marketdata.py`, `market_data_cache.py` | Shared helpers, parquet access, OHLC cache |
| `build_industry_parquet.py`, `enrich_industries.py` | Industry/peer dataset build and yfinance enrichment |
| `*.parquet` | Dataset outputs, stored with Git LFS (clone with LFS to get real content) |
| `tests/` | pytest suite, including golden Darvas characterisation (`tests/golden_darvas.json`) |
| `architecture/`, `safe/`, `cdc/`, `docs/` | TOGAF governance, SAFe backlog, CDC deployment, process docs |
| `*.md` (root) | One design/results document per module, indexed below |

## Data sources
| Source | What is taken | Access route | Licence / caveat |
|---|---|---|---|
| Yahoo Finance via yfinance | OHLC and fundamentals, all markets | public API, rate limited | personal/research use; terms apply |
| NSE / BSE | India universe | `nsepython`, `bseindia` | exchange terms apply |
| NASDAQ Trader symbol files, SEC EDGAR | US universe, point-in-time filings | public HTTP | SEC fair-access policy |
| JPX / kabupy | Japan universe | public download | exchange terms apply |
| KRX via pykrx | Korea universe | scraped; KRX login env vars may be required | check KRX terms |

See [DATA_SOURCES.md](DATA_SOURCES.md) for the per-market public factor libraries.

## Configuration
- `~/.nasdaq/ncds_auth.json`: optional Nasdaq Data Link NCDS credentials for the US universe
  (read from your home directory; never committed). Without it the scanner falls back to public
  NASDAQ Trader files.
- `KRX_ID`, `KRX_PW`: environment variables read by `pykrx` for KRX access (optional).
- Output directories (for example `./us_full_scan`) are relative to the working directory and are
  gitignored, as are `*.xlsx`.
- No API keys are required for the default scans.

## Testing
```bash
pip install pytest ruff pandas numpy scipy scikit-learn requests openpyxl yfinance nsepython bseindia pykrx lxml beautifulsoup4
ruff check .
pytest -q          # 149 tests, offline
./verify_integrity.sh   # tracked-file hashes vs CHECKSUMS.sha256 (regenerate after edits)
```
CI (`.github/workflows/ci.yml`) runs lint, tests, TOGAF governance and the integrity manifest.

## Limitations and known issues
- The Piotroski/Coffee Can `fundamental_scan()` logic is still duplicated across the five scanners and
  entangled with yfinance calls; only the Darvas stage has been consolidated so far.
- Fundamentals are a current yfinance snapshot except in the US point-in-time backtest.
- Tracked `*.parquet` files are LFS pointers; without `git lfs pull` they are 130-byte text stubs.

## Documentation index

**Guides**
- [User Guide](USER_GUIDE.md) — end-to-end workflows for every capability
- [Research paper](RESEARCH_PAPER.md) — full write-up: approach, data, assumptions, abstract & cross-market results
- [Glossary of reusable blocks](GLOSSARY.md) — the shared `marketdata.py` library + each module's pure functions
- [Watchlist store](WATCHLIST_STORE.md) — application-level versioned CRUD for watchlists (append-only) behind the serve.py REST endpoints
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
- [Liquidity multi-year](LIQUIDITY.md#multi-year-test--the-premium-is-significant-liquidity_multiyearpy) — 10y Fama-MacBeth: liquidity premium +4.24%/qtr, t=2.16 (significant)
- [Data sources](DATA_SOURCES.md) — per-market public factor libraries (AQR/Ken French/IIMA IFFM); what the quality paper names
- [Benchmark](BENCHMARK.md) — fetches real Ken French factors and validates our quality tilt against them (−HML, +Mom)
- [HFT selection](HFT_SELECTION.md) — the four HFT archetypes as a Tier-1 stock picker from daily high-low-close-volume
- [Darvas × volume](DARVAS_VOLUME.md) — Darvas-box monitor for stealth volume acquisition + HFT footprint (coils & volume-confirmed breakouts)
- [Watchlists](WATCHLISTS.md) — separate fundamentally-strong & being-accumulated lists (+ intersection)
- [Accumulation screener](ACCUMULATION_SCREENER.md) — CMF/accumulation screen validated against 1-month & 6-month forward returns
- [Corporate actions](CORPORATE_ACTIONS.md) — stock-split (yfinance) & rights-issue/dilution candidate screeners
- [Frontier gaps](FRONTIER_GAPS.md) — the scout's 7 frontier themes all closed (seasonality/network/crowding/NLP/options/ESG/microstructure)
- [Earnings × liquidity](EARNINGS_LIQUIDITY.md) — does liquidity/volume/price condition PEAD? (announcement volume surge + drift study)

**Decision layer & consumption**
- [Decision layer, global rigor & observability](DECISION_LAYER.md) — portfolio / risk / meta-screen / rotation / alerts / comps + global PIT + FX + data quality + serving


## Install and requirements
Python 3.9+. Core: pandas, numpy, pyarrow, scikit-learn, yfinance, openpyxl. Market libs:
nsepython, bseindia, kabupy, pykrx. Optional data backbone: cassandra-driver, confluent-kafka,
apache-flink (servers via `brew install cassandra kafka apache-flink`). See
[`requirements.txt`](requirements.txt) and [INFRA.md](INFRA.md).

## Related repositories
- [`retail-outlet-monitoring`](https://github.com/herrrickshaw/retail-outlet-monitoring) and
  [`fuel-retail-outlets`](https://github.com/herrrickshaw/fuel-retail-outlets): split out of this
  repo's earlier scope.

## Licence
No licence file yet: all rights reserved.
