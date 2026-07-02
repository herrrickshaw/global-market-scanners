# Performance — bottlenecks & fixes

Where execution time actually goes, ranked, with measured evidence and concrete
fixes. Status: ✅ done · 🔶 partial · ⬜ todo.

## Measurements (this machine)
| Operation | Measured |
|---|---|
| yfinance download, full US universe (~5–6k tickers) | ~5–10 min + frequent **rate-limit / 401 crumb** failures |
| yfinance `get_info` (fundamentals), per ticker | ~0.6–1/s single-thread; **429s** above ~3 workers |
| Whole-market parquet load (TW, 2,204 tickers) | **0.2 s** (+0.1 s pivot) |
| Per-ticker technical/momentum compute | **3.9 ms** → dvm_global 19-market loop ≈ **120 s** |
| Cassandra OHLC read, 1 ticker | ~50 ms steady (4.5 s cold-connect) |

---

## 1. yfinance data acquisition — the dominant cost 🔶
Network-bound and rate-limited; re-downloaded on every run. Seen repeatedly: 8-worker
bursts hit HTTP 401 "Invalid Crumb", full-universe pulls dropped batches, `get_info`
throttled.

**Fixes**
- **Cache once, read locally.** `market_store.cached_download` (Cassandra) eliminates
  re-downloads — ✅ used by `pit_backtest`, `factor_research`, and now **`ml_viability`
  and `screen_viability`** (routed through the cache; verified serving "from Cassandra,
  no network"). `fundamentals_global` has its own resumable cache.
- ✅ **Centralised rate-limit governance — `apiclient.py`.** Every external source now
  goes through one throttle: per-source **min-interval + max-concurrency** caps
  (yfinance 0.4 s/3, EDGAR 0.12 s/5, GLEIF, Wikidata 1/min…), **adaptive backoff**
  (auto-slows on 429 / "crumb" / 401, decays back when healthy), and retry with
  exponential backoff + jitter. yfinance calls are **deduped and chunked** (≤50/batch)
  to avoid the crumb storms that hit above ~3 workers. Wired into `market_store`,
  `fundamentals_global`, `enrich_industries`, and `pit_fundamentals` (EDGAR) — so no
  code path can exceed a source's limits, and call count is minimised (dedup + cache-first).
- **Prefer local parquets for bulk** — `dvm_global`/`dvm_composite` read
  `cleaned_long_*.parquet` (0.2 s/market) instead of yfinance ✅. Biggest single win:
  point every full-universe scan at the parquet/Cassandra layer, not the network.
- **Tame the API when you must fetch**: ≤3 workers + exponential backoff (✅ in
  `enrich_industries`, `fundamentals_global`); pre-warm the cache in one off-hours pass.

## 2. Per-ticker Python compute loops ✅ (vectorised + parallelised)
`dvm_global` looped per ticker in Python. I/O is trivial (0.2 s/market); the cost was
the interpreted loop.

**Fixed — two stacked wins on `dvm_global` (30,785 tickers, 19 markets):**
| version | wall time | speedup |
|---|---|---|
| per-ticker loop, 1 worker | 105.4 s | 1× |
| **columnar-vectorised**, 1 worker | **13.9 s** | **7.6×** |
| vectorised + `ProcessPoolExecutor` (8 cores) | **8.1 s** | **13×** |

RSI/MACD/DMA/MFI/ADX/beta now compute across the pivoted price matrix in a handful of
pandas ops (per-column rolling/ewm) instead of per-series. Scored-count parity restored
with a `>=200`-bar filter; the columnar path computes on the market's shared date index,
so tickers with recent gaps are correctly excluded (~2% fewer screen hits — arguably more correct).

**Further ⬜:** `dvm_composite` (~731 tickers) still loops but is small/fast (~3 s); same
vectorisation applies if it grows.

## 3. ML walk-forward retraining ✅
`pit_backtest`, `ml_viability`, `screen_viability --include-ml` refit a model **per
test-day per stock** — O(stocks × days × fit). The heaviest CPU path.

**Fixed:** `ml_viability` now evaluates tickers in **parallel across cores**
(`--workers`, `ProcessPoolExecutor`), plus **test-day subsampling** (`--step`, weekly).
Speedup scales with universe size — modest on tiny samples (12 tickers: 16.1 s → 12.0 s,
process-overhead-bound), approaching ~Ncores× on full universes.
**Further ⬜:** cache the per-ticker feature matrix (recomputed each fit); consider one
pooled cross-sectional model instead of per-stock refits.

## 4. Cassandra point-reads for bulk analytics 🔶
Great for operational lookups/CDC, but thousands of per-partition reads are slower than
one columnar file for scans (measured: 0.2 s parquet vs ~50 ms × N Cassandra).

**Fix** — keep the two-tier split: Cassandra for writes/CDC/point lookups; **parquet +
DuckDB for bulk filtering/aggregation** (✅ the warehouse already does this). Don't loop
Cassandra reads for full-universe jobs.

## 5. EDGAR fundamentals first-run ✅
Thousands of `companyfacts` fetches (~27 GB transient).
**Fixed:** prune-on-fetch to a compact SQLite (`edgar_facts.db`, 27 GB → ~190 MB),
resumable, negative-cached misses.

## 6. No incrementality — full recompute every run ✅ (data layer)
Each run reprocessed the whole universe/history.
**Fixed (data layer):** `market_store.cached_download` now does **incremental delta
fetches** — a cached-but-stale ticker pulls only the bars since its last cached date
(yfinance `start=last+1`) and upserts them (PK `(ticker,d)`, idempotent), instead of
re-downloading 5 years. With `market_holidays` skipping non-trading days ✅, daily updates
are cheap. **Further ⬜:** materialise results in DuckDB and refresh only changed partitions.

---

## Priority order (impact × effort)
1. ✅ **Route `ml_viability` / `screen_viability` through `cached_download`** — no more re-downloads.
2. ✅ **Vectorise + parallelise `dvm_global`** — 105 s → 8.1 s (13×).
3. ✅ **Subsample + multiprocess the ML walk-forward** — parallel across cores.
4. ✅ **Incremental delta fetches** (only new bars) — done in the data layer.
5. ⬜ Remaining: DuckDB-materialised results refreshed by changed partition only.

Already banked: local-parquet bulk reads, Cassandra cache with incremental updates
(all data scripts), vectorised+parallel `dvm_global`, parallel ML walk-forward,
EDGAR pruning, holiday-day skipping, DuckDB analytical layer.
