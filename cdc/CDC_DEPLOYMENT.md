# Reference deployment — true commit-log CDC → Debezium → Kafka

Implements the blueprint's **real** Change Data Capture (p.10–12): Cassandra
commit-log CDC (`cdc_raw` hard-links) streamed to Kafka by the Debezium
Cassandra-5 connector — the production-grade version of the app-level CDC bridge
in `market_store.py`.

## What's deployed & verified ✅
1. **Cassandra commit-log CDC enabled** — `cdc_enabled: true` in `cassandra.yaml`,
   `cdc_raw_directory` active; `market.ohlc_bars` altered `WITH cdc=true`.
2. **Hard-link mechanic confirmed** (blueprint p.11): writes produce
   `cdc_raw/CommitLog-*.log` with **link count 2** (shared inode with the live
   commit log — zero extra disk), and a `_cdc.idx` byte-offset index on completed
   segments.
3. **Debezium Cassandra-5 connector running** (`3.0.1.Final`) — connected to both
   Cassandra and Kafka, survives Cassandra restarts (auto-reconnect).
4. **Data path proven end-to-end** — the connector snapshotted `market.ohlc_bars`
   and published **6,470 rows** to Kafka topic **`stockcdc.market.ohlc_bars`**
   (`<topic.prefix>.<keyspace>.<table>`).

## Files
- `cassandra-cdc.properties` — connector config (Cassandra yaml path, Kafka broker,
  topic prefix, JSON converters, relocation/offset dirs).
- `run_debezium_cassandra.sh` — launcher. Resolves the JDK `--add-opens/--add-exports`
  flags Cassandra's commit-log reader needs, sets `-Dcassandra.storagedir`, and runs
  `io.debezium.connector.cassandra.CassandraConnectorTask`.

## One-time setup (connector plugin)
```bash
VER=3.0.1.Final
curl -L -o dbz.tgz \
 https://repo1.maven.org/maven2/io/debezium/debezium-connector-cassandra-5/$VER/debezium-connector-cassandra-5-$VER-plugin.tar.gz
mkdir -p ~/debezium && tar xzf dbz.tgz -C ~/debezium
# the plugin ships without core jackson — add them:
cd ~/debezium/debezium-connector-cassandra-5
for a in annotations core databind; do
  curl -sLO https://repo1.maven.org/maven2/com/fasterxml/jackson/core/jackson-$a/2.16.2/jackson-$a-2.16.2.jar; done
export DBZ_HOME=~/debezium/debezium-connector-cassandra-5
```
Then: `./run_debezium_cassandra.sh` (needs Kafka + Cassandra up, JDK 17+).

## Operational notes (blueprint p.12) ⚠️
- **Live-delta latency:** the connector only processes *completed* commit-log
  segments (those with `_cdc.idx`). On a low-write single node, a segment fills
  slowly (32 MiB), so incremental deltas lag until a segment completes / the node
  is drained. High-write or clustered deployments don't see this.
- **Backpressure:** CDC hard-links prevent commit-log recycling. If the consumer
  stops while writes continue, `cdc_raw` grows until `cdc_total_space`, then
  Cassandra **rejects writes** to CDC-enabled tables. Keep the connector running,
  or set `cdc=false` on `market.ohlc_bars` when not demoing.
- **Dedup:** at RF≥3 the same mutation is captured on multiple nodes — downstream
  (Flink) must dedup by mutation digest.

## Relation to the app-level bridge
`market_store.py`'s `MARKET_STORE_CDC=1` Kafka emit is the lightweight equivalent
(no commit-log machinery); this Debezium deployment is the production path. Both
feed the same downstream shape: mutations → Kafka → Flink.
