# Data infrastructure — Cassandra · Kafka · Flink

How the three frameworks are wired into the repo, and — honestly — where each
actually speeds things up.

## What each does for us

| Framework | Role here | Real speedup? |
|---|---|---|
| **Cassandra** | Persistent OHLC (and future fundamentals) store — `market_store.py` | **Yes, large.** Our bottleneck was re-downloading 5y history every run (yfinance rate-limits). Cache once → later runs read locally (0.2s vs multi-second per batch, no network). |
| **Kafka** | Distributed work-queue — `stream_pipeline.py` produces the ticker universe, N consumer workers process partitions in parallel | **Yes, when scaled.** Throughput scales with the number of `consume` workers; modest gain on one laptop, real when fanned out. |
| **Flink** | Streaming aggregation of the live signal stream — `flink_screens.py` | **At scale only.** Flink distributes a *continuous* stream across a cluster; on one machine it's the streaming topology, not a batch speedup. |

## Servers (system installs, already running)
```bash
brew install cassandra kafka apache-flink   # already installed on this machine
brew services start cassandra               # :9042  (running)
brew services start kafka                    # :9092  (running, KRaft — no ZooKeeper)
export JAVA_HOME=/opt/homebrew/opt/openjdk@21   # in ~/.zshrc
```
Python clients are in `requirements.txt` (`cassandra-driver`, `confluent-kafka`,
`apache-flink`).

## Cassandra OHLC cache — the drop-in speedup
```python
from market_store import cached_download
ohlc = cached_download(tickers, years=5)   # Cassandra-first, yfinance fallback
```
`pit_backtest.py` already uses it. The same one-line swap gives `ml_viability.py`,
`screen_viability.py`, and the scanners the same benefit. Falls back to plain
yfinance automatically if the Cassandra node is down, so nothing breaks.

Keyspace `market`, table `ohlc_bars(ticker, d, o, h, l, c, v)`.

## Kafka parallel scan
```bash
python stream_pipeline.py consume --group scan1   # launch several of these
python stream_pipeline.py produce --market US --limit 500
python stream_pipeline.py demo                     # single-process round-trip test
```
Producer → `scan.tickers`; workers compute a per-ticker signal (from the Cassandra
cache) → `scan.signals`. Add workers to go faster.

## Flink streaming aggregation
```bash
pip install apache-flink
/opt/homebrew/opt/apache-flink/libexec/bin/start-cluster.sh   # dashboard :8081
flink run -py flink_screens.py                                 # 5s tumbling counts
```
Consumes `scan.signals`, counts signal types in tumbling windows.

## Bottom line
The **Cassandra cache is the change that actually makes every repeated run
faster** and removes the yfinance rate-limit pain. Kafka and Flink are the
scale-out path — genuinely useful when the workload grows beyond one machine,
included and wired but not a magic single-laptop speedup.
