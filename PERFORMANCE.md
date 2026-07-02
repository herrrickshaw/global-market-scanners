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
- **Cache once, read locally.** `market_store.cached_download` (Cassandra) already
  eliminates re-downloads — ✅ used by `pit_backtest`, `factor_research`;
  `fundamentals_global` has its own resumable cache. ⬜ route `ml_viability` and
  `screen_viability` through it too (currently call `yf.download` directly).
- **Prefer local parquets for bulk** — `dvm_global`/`dvm_composite` read
  `cleaned_long_*.parquet` (0.2 s/market) instead of yfinance ✅. Biggest single win:
  point every full-universe scan at the parquet/Cassandra layer, not the network.
- **Tame the API when you must fetch**: ≤3 workers + exponential backoff (✅ in
  `enrich_industries`, `fundamentals_global`); pre-warm the cache in one off-hours pass.

## 2. Per-ticker Python compute loops ✅ (parallelised) + ⬜ (vectorise)
`dvm_global`/`dvm_composite` loop per ticker in Python. I/O is trivial (0.2 s/market);
the cost is the interpreted loop.

**Fixed:** `dvm_global` now runs markets across cores via `ProcessPoolExecutor`
(`--workers`, default = all cores). **Measured: 105.4 s → 45.2 s (2.3×)** on 8 cores —
sub-linear because US/CN/JP dominate the wall-time and each worker reloads its parquet.

**Further ⬜:** vectorise RSI/MACD/DMA across the pivoted price matrix (or DuckDB window
functions) instead of per-series for another ~3–5×; apply the same pool to `dvm_composite`.

## 3. ML walk-forward retraining 🔶
`pit_backtest`, `ml_viability`, `screen_viability --include-ml` refit a model **per
test-day per stock** — O(stocks × days × fit). The heaviest CPU path.

**Fixes**
- **Subsample test days** (`--step`, ✅) — weekly instead of daily cuts fits 5×.
- **ProcessPoolExecutor** across tickers (CPU-bound; threads don't help under the GIL).
- **Cache the feature matrix** per ticker (recomputed today each fit) and warm-start /
  reuse the scaler; consider a single pooled cross-sectional model vs per-stock refits.

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

## 6. No incrementality — full recompute every run ⬜
Each run reprocesses the whole universe/history.
**Fixes** — incremental updates (append only new bars via CDC / the holiday calendar so
non-trading days are skipped ✅ `market_holidays`); materialise results in DuckDB and
refresh only changed partitions.

---

## Priority order (impact × effort)
1. **Route `ml_viability` / `screen_viability` through `cached_download`** (kills re-downloads) — small change, big win.
2. **Parallelise `dvm_global` with `ProcessPoolExecutor`** — 120 s → ~25 s.
3. **Subsample + multiprocess the ML walk-forward** — the compute-heavy path.
4. **Incremental updates** (CDC + trading-day deltas) — biggest long-run saving.

Already banked: local-parquet bulk reads, Cassandra cache, EDGAR pruning, holiday-day
skipping, DuckDB analytical layer.
