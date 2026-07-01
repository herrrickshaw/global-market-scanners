# Architecture Map

Visual map of the platform — data flow, tiers, and the analytical schema. Rendered
by GitHub (Mermaid). Companion to [ARCHITECTURE.md](ARCHITECTURE.md) (blueprint
mapping) and [SCHEMA.md](SCHEMA.md) (data dictionary).

## Data flow — sources → operational → analytical → outputs

```mermaid
flowchart LR
  subgraph SRC[Data sources]
    YF[yfinance]
    ED[SEC EDGAR XBRL]
    GL[GLEIF]
    NX[nsepython / pykrx / kabupy]
    SD[cleaned_long parquets<br/>19 markets · 7.7M bars]
  end

  subgraph OP[Tier 1 · Operational — Cassandra]
    OB[(ohlc_bars<br/>OHLC cache)]
    CD[(cdc_stream<br/>cdc=true)]
  end

  subgraph ST[Streaming backbone]
    KF[Kafka<br/>work-queue + CDC topic]
    FL[Flink<br/>windowed aggregation]
    DZ[Debezium<br/>Cassandra-5 connector]
  end

  subgraph PROC[Processing]
    SC[market scanners<br/>Darvas+Piotroski+CoffeeCan]
    DV[dvm_global / dvm_engine<br/>Momentum + technicals]
    FU[fundamentals_global<br/>ROE/DE/PE/growth]
    PT[pit_backtest<br/>point-in-time]
    MD[ml_screen_discovery<br/>supervised→unsup→RL]
    FR[factor_research]
  end

  subgraph CA[Tier 3 · PIT caches]
    EF[(edgar_facts.db)]
    FC[(fundamentals_cache.db)]
  end

  subgraph AN[Tier 2 · Analytical / Serving — DuckDB market.duckdb]
    WH[(views: ohlc · dvm_global · fundamentals<br/>dvm_composite · companies · viability)]
  end

  subgraph OUT[Outputs]
    XL[Excel workbooks]
    PQ[parquet dataset<br/>companies / unlisted / segments]
    RS[result DBs + CSVs<br/>viability / backtest]
    NB[Colab notebook]
  end

  YF --> OB
  YF --> DV & FU & PT & SC
  ED --> EF --> PT
  GL --> PQ
  NX --> SC
  SD --> DV & MD & FR

  OB -->|market_store cache| DV & PT & MD
  OB --> CD -->|hard-link CDC| DZ --> KF --> FL
  SC -.publish tickers.-> KF
  FU --> FC

  DV --> WH
  FU --> WH
  PT --> RS
  SC --> XL
  MD --> RS
  FC --> WH
  EF -. attach .- WH

  WH --> OUT
  WH -.query/filter.-> NB
```

## Tiered architecture (Modern Data Architecture Blueprint)

```mermaid
flowchart TB
  A[Ingestion<br/>yfinance · EDGAR · GLEIF · exchange libs · seed parquets]
  B[Tier 1 — Operational store<br/>Cassandra: masterless NoSQL, wide-column, ms writes, CDC]
  C[Tier 3 — PIT caches<br/>edgar_facts.db · fundamentals_cache.db]
  D[Processing engines<br/>scanners · DVM · backtest · ML discovery · factor research]
  E[Streaming — Kafka + Flink + Debezium CDC]
  F[Tier 2 — Analytical / Serving<br/>DuckDB warehouse: SQL filter + aggregate across 7.7M+ rows]
  G[Consumption<br/>Excel · parquet dataset · result DBs · Colab · ad-hoc SQL]
  A --> B --> D --> F --> G
  A --> C --> D
  B --> E --> F
```

## Analytical schema (join model)

```mermaid
erDiagram
  OHLC ||--o{ DVM_GLOBAL : "ticker, market"
  DVM_GLOBAL ||--|| DVM_COMPOSITE : "ticker, market"
  FUNDAMENTALS ||--|| DVM_COMPOSITE : "ticker, market"
  COMPANIES ||--o| DVM_COMPOSITE : "ticker (enrich)"
  EDGAR_FACTS ||--o{ FUNDAMENTALS : "cik via ticker (US PIT)"

  OHLC {
    string ticker PK
    date   Date PK
    float  Close
    bigint Volume
    string market
  }
  DVM_GLOBAL {
    string market
    string ticker
    double M "momentum 0-100"
    double rsi
    double mfi
    double adx
    double beta
  }
  FUNDAMENTALS {
    string ticker PK
    string market
    double roe
    double de
    double pe
    double pb
    string sector
  }
  DVM_COMPOSITE {
    string market
    string ticker
    double D "durability"
    double V "valuation"
    double M "momentum"
    double composite
    string code "GGG/GGB/BBG"
    string label
  }
  COMPANIES {
    string ticker
    string country
    string sector
    string industry
    string segment
  }
  EDGAR_FACTS {
    string cik PK
    string concept
    string filed "PIT key"
    real   val
  }
```

## Two-tier principle
- **Cassandra** (operational) — high-write OHLC cache + CDC, availability-first (AP/BASE).
- **DuckDB** (analytical/serving) — fast ad-hoc filtering/aggregation, the query surface.

Update = re-run any producer; the warehouse views reflect the live files with no ETL.
See [SCHEMA.md](SCHEMA.md) for full column-level detail.
