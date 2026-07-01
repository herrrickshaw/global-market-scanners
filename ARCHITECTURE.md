# Architecture — mapped to the Modern Data Architecture Blueprint

This repo's data layer is designed against *The Modern Data Architecture Blueprint*
(distributed storage → processing engine → real-time ops/CDC). Below is the honest
mapping: what aligns, what's deliberately scaled down for a single laptop, and the
path to the full reference.

## The blueprint's synthesis → our stack

| Blueprint stage | Prescription | Our implementation | Status |
|---|---|---|---|
| **[1] Foundation** | Scale horizontally on commodity HW; **model data by query patterns, not entities** | `market_store.py` tables keyed by the actual read: `ohlc_bars(ticker, d)` = "bars for a ticker, time-sorted" | ✅ query-driven; single node (no horizontal scale) |
| **[2] Storage** | Masterless NoSQL (Cassandra), tunable consistency, ms writes | Cassandra 5.0 keyspace `market`, wide-column time-series (partition=ticker, clustering=date) | ✅ pattern matches; **RF=1** on one node (no real HA) |
| **[3] Event Streaming** | Activate **CDC** → mutations to **Kafka** without touching the write path | App-level CDC bridge: `market_store` emits `ohlc.cdc` events to Kafka on new-bar writes; `stream_pipeline.py` work-queue on Kafka | ⚠️ **application-level** CDC, not commit-log CDC (see below) |
| **[4] Processing** | Unified engine (**Flink**) ingests CDC/event streams for real-time analytics | `flink_screens.py` (PyFlink) consumes the Kafka stream, windowed aggregation | ✅ topology in place; scale-out only |

## Query-driven data modelling (blueprint p.5)
The blueprint's core storage lesson — *denormalise, design one table per query, write
wide rows partitioned by key and sorted by a clustering column* — is exactly the
time-series shape we use:

```
CREATE TABLE ohlc_bars (            -- query: "give me a ticker's history"
    ticker text, d date,           -- partition = ticker, clustering = date (time-sorted)
    o double, h double, l double, c double, v double,
    PRIMARY KEY (ticker, d));
```
Same pattern as the blueprint's `heart_rate` example (entity partition, time-sorted
columns). Joins live in the application layer, not the DB.

## CAP positioning (blueprint p.4)
Cassandra is an **AP / BASE** store (Availability + Partition-tolerance, eventual
consistency) — the right choice for a high-write market-data cache where we prefer
"always writable" over strict consistency. On a single node CAP is moot (no
partitions); at scale we'd run **RF=3** with tunable read/write consistency
(`LOCAL_QUORUM`), which then also mandates **downstream dedup** (same mutation
captured on 3 nodes — blueprint p.12).

## Lambda architecture (blueprint p.9)
- **Batch layer (accurate, immutable history):** the backtests — `pit_backtest.py`,
  `screen_viability.py`, `ml_viability.py` — computing over the full stored history.
- **Speed layer (low-latency, newest data):** the Kafka→Flink path (`stream_pipeline`
  + `flink_screens`) for live/intraday signals.
- **Serving layer:** the parquet/SQLite result artifacts and (future) an API that
  merges batch + speed views.

## Honest gaps vs the full blueprint (single-laptop reality)
1. **CDC is application-level, not commit-log CDC.** The blueprint's true CDC uses
   Cassandra's `cdc_raw_directory` hard-links + Debezium (p.10–11). That needs
   `cdc_enabled=true`, a consumer that deletes segments, and `cdc_total_space`
   backpressure management — overkill for one node. Our `market_store` instead emits
   a Kafka event on write (same *intent*: mutations → Kafka → Flink, no rescans),
   which is the pragmatic single-node equivalent. Enabling real CDC is a config flip
   + Debezium connector when this moves to a cluster.
2. **No horizontal scale / HA** — one node, RF=1. Foundation principle honoured in
   modelling, not in deployment.
3. **Flink is the scale-out path, not a laptop speedup** (blueprint's own note: Spark
   in-memory can beat Flink on static batch). Value appears with a continuous stream
   across a cluster.

## Bottom line
The **storage model and dataflow shape follow the blueprint**; the deployment is
deliberately single-node. The genuine, already-realised win is the blueprint's
Foundation+Storage layer — query-driven Cassandra as the market-data cache that
kills the re-download bottleneck. Kafka/Flink/CDC are wired to the blueprint's shape
so scaling out is a deployment change, not a rewrite.
