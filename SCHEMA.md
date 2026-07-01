# Data Schema

Data dictionary for every store in the platform, by tier. Types are as introspected
from the live stores. Join key across the analytical tables is **`(ticker, market)`**
(US tickers have no suffix; others carry yfinance suffixes: `.T .HK .L .KS .DE .AX …`).

---

## Tier 1 — Operational store (Cassandra, keyspace `market`)
Wide-column, query-driven (partition = ticker, clustering = time). Written by
`market_store.py`; CDC source for `cdc_stream`.

**`ohlc_bars`** — daily OHLC cache
| column | type | key |
|---|---|---|
| `ticker` | text | **partition** |
| `d` | date | **clustering** (time-sorted) |
| `o` `h` `l` `c` | double | open/high/low/close |
| `v` | double | volume |

**`cdc_stream`** — dedicated CDC-enabled table (`cdc=true`) for Debezium → Kafka
| column | type | key |
|---|---|---|
| `ticker` | text | **partition** |
| `ts` | timestamp | **clustering** |
| `event` | text | mutation payload |

---

## Tier 2 — Analytical / serving layer (DuckDB `market.duckdb`)
Views over the parquets + attached SQLite. One SQL surface; see [WAREHOUSE.md](WAREHOUSE.md).

**`ohlc`** — 7,739,066 rows (19 markets)
`ticker:VARCHAR, Date:TIMESTAMP, Open/High/Low/Close:FLOAT, Volume:BIGINT, market:VARCHAR`

**`dvm_global`** — 30,785 rows (technical DVM / Trendlyne metrics per stock)
`market, ticker, M(momentum 0-100), rsi, mfi, adx, dist_52w, above_200dma, golden_cross, sma50_above_200, macd_bull, vol_ratio, beta`

**`fundamentals`** — 731 rows (yfinance, numerics coerced)
`ticker, market, pe, pb, roe, roa, de, rev_growth, earn_growth, op_margin, div_yield, mktcap, sector`

**`dvm_composite`** — 726 rows (Trendlyne GGG/GGB/BBG classification)
`market, ticker, D, V, M, composite, code, label, roe, de, pe, sector`

**`companies`** — 17,754 rows (industry / peer dataset)
`company_name, ticker, code, country, exchange, sector, industry, segment, peer_count, global_peers:VARCHAR[]`

**`viability`** — 25 rows (screen-viability summary)
`market, screen, n_tickers, total_signals, avg_fwd5d, avg_hit_pct, avg_edge, pct_tickers_pos_edge, viable`

---

## Tier 3 — Point-in-time fundamentals cache (SQLite)

**`edgar_facts.db` → `facts`** — pruned SEC EDGAR XBRL (filed-date filtered = point-in-time)
| column | type | note |
|---|---|---|
| `cik` | text | zero-padded SEC id |
| `concept` | text | us-gaap concept (NetIncomeLoss, Assets, …) |
| `unit` | text | USD / shares |
| `start` `end` | text | fiscal period bounds |
| `filed` | text | **as-filed date — the PIT key** |
| `val` | real | value |

`edgar_facts.db → fetched(cik)` — negative-cache of processed CIKs.

**`fundamentals_cache.db → fund`** — global yfinance fundamentals (resumable cache)
`ticker(PK), market, pe, pb, roe, roa, de, rev_growth, earn_growth, op_margin, div_yield, mktcap, sector`

---

## Tier 4 — Reference & result artifacts (parquet + SQLite, committed)

| Artifact | Schema |
|---|---|
| `companies_industry.parquet` (LFS) | `company_name, ticker, code, country, exchange, sector, industry, segment, peer_count, global_peers[]` |
| `industry_segments.parquet` (LFS) | `segment, n_companies, countries, company_names` |
| `unlisted_firms.parquet` (LFS) | `company_name, country, segment, source, website, employees, revenue, city, is_listed, lei, legal_form, status, fetched_at` |
| `cleaned_long_*.parquet` (×19, seed) | `Date, Open, High, Low, Close, Volume, Symbol` |
| `viability_summary.db → market_screen_summary` | `market, screen, n_tickers, total_signals, avg_fwd5d, avg_hit_pct, avg_edge, pct_tickers_pos_edge, viable` |
| `pit_backtest.db → arm_summary` | `arm, n_months, avg_picks, avg_mth%, ann_return%, hit_rate%, sharpe, max_dd%` |

---

## Join model
```
ohlc (ticker,market) ──┐
dvm_global (ticker,market) ──┼── all keyed on (ticker, market)
fundamentals (ticker,market) ─┤
dvm_composite (ticker,market)─┘
companies (ticker) ── sector/industry/segment enrichment
edgar_facts (cik) ── ticker→cik via SEC company_tickers (pit_fundamentals)
```
