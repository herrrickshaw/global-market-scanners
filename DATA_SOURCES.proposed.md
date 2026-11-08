
```markdown
# DATA_SOURCES.md - global-market-scanners

Last reviewed 2026-11-08. This repo is mostly code. It stores few datasets; most inputs are fetched live or read from sibling repos (global-market-data, global-ticker-universe, global-stock-screener). Terms marked UNVERIFIED were not read from the publisher.

## A. Datasets committed here

| File | Publisher / provenance | Terms | Fields | Coverage | Refresh | Caveats |
|---|---|---|---|---|---|---|
| companies_industry.parquet (LFS) | Built by build_industry_parquet.py from scan outputs (US: yfinance industry/sector; JP/EU: scan sector fields; IN/KR: yfinance via enrich_industries.py) | Yahoo terms https://legal.yahoo.com/us/en/yahoo/terms/otos/ apply to labels | company_name, ticker, code, country, exchange, sector, industry, segment, peer_count, global_peers[] | 17,754 rows; USA 6,218 / India 5,314 / Japan 3,566 / Korea 2,606 / Europe 50 | on demand (last 2026-07-13) | 50% null sector/industry; 727 duplicate tickers; LFS pointer |
| industry_segments.parquet (LFS) | derived from the above | same | segment, n_companies, countries, company_names | 190 segments | with parent | "Unclassified" 4,539 |
| unlisted_firms.parquet (LFS) | GLEIF LEI reference data https://www.gleif.org/en/about/open-data | CC0 | company_name, country, segment, source, website, employees, revenue, city, is_listed, lei, legal_form, status, fetched_at | 727 India/other LEI records, fetched 2026-06-30 | on demand | website/employees/revenue empty |
| pit_backtest.db | own output of pit_backtest.py (SEC EDGAR filed-date fundamentals + yfinance prices) | own work; inputs per source terms | arm_summary(arm, n_months, avg picks, ann_return%, hit%, sharpe, max_dd%) | US large-cap, 5y monthly | on rerun | survivorship: current large caps only |
| pit_backtest_full.db | same code, larger run | - | same | - | - | contains inf values; treat as invalid until rerun |
| viability_summary*.db, net_viability_*.csv, ml_viability_5y.csv | own output of screen_viability.py / apply_costs.py / ml_viability.py | own work | market, screen, gross_edge, cost%, net_edge, hit% | Europe, India, Japan, Korea, US x 5 screens | on rerun | gross edges pre-significance testing; see EVIDENCE.md |
| reports/signal_research/*.csv, reports/intraday_india/*.csv | own output of signal_research.py / intraday_india.py | own work | IC, Fama-MacBeth, walk-forward, PBO/CSCV, cost tables | India+global | on rerun | cost model assumptions in NET_OF_COST.md |

## B. External sources fetched at run time

| Source | Publisher & URL | Terms | Used for | Cadence |
|---|---|---|---|---|
| Daily prices, snapshot fundamentals | Yahoo Finance via yfinance, https://finance.yahoo.com | personal use; no scraping/DB building (https://legal.yahoo.com/us/en/yahoo/terms/otos/) | prices/sector for all markets | on demand |
| US PIT fundamentals | SEC EDGAR XBRL https://data.sec.gov/api/xbrl/ | US public information; fair-access policy at https://www.sec.gov/developer (UNVERIFIED, 403 to probe) | pit_fundamentals.py | on demand |
| India exchange data | NSE https://www.nseindia.com, BSE https://www.bseindia.com via nsepython/bseindia | NSE/BSE terms UNVERIFIED | India OHLC, corporate data | daily |
| Regulatory filings registry | `data_sources.REGULATORY_FILINGS`: SEC, MCA/BSE-NSE XBRL, EDINET, DART, CVM, ESMA ESEF, etc. | each regulator's terms | documented gap map (not all wired) | n/a |
| Benchmark factors | Ken French (https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html), AQR (https://www.aqr.com/Insights/Datasets), IIMA IFFM (https://faculty.iima.ac.in/~iffm/Indian-Fama-French-Momentum/) | cite originals; AQR/IFFM terms UNVERIFIED | benchmark.py, factor_research.py | monthly |
| Literature | OpenAlex, Crossref, arXiv APIs | open metadata | literature_scout.py | on demand |
| Optional enrichment | Wikidata (CC0), GLEIF (CC0), D&B/Lusha/Apollo (paid; do not redistribute) | see terms | unlisted_enrichment.py | on demand |
| Streaming backbone | Cassandra, Kafka, Flink, Debezium | open-source licences | infrastructure only, no data | n/a |

## C. Not used on purpose
CMIE Prowess (commercial) is named in IIMA paper and registry `RAW_SOURCES` but is not required.

## D. Known issues
Parquet LFS pointers; 50% null industry; pit_backtest_full.db has inf; "19 markets" excludes India in `PLATFORM_MARKETS`; EVIDENCE.md quotes "10 years / 44M rows" and "25 markets" figures that do not reproduce from the committed data of the sibling repos.
```
