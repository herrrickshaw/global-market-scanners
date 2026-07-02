# Decision Layer, Global Rigor & Data Observability (PI-6)

The platform generated *signals* but stopped short of *decisions*, backtested
rigorously only in the US, mixed currencies across markets, and never checked the
data itself for rot. PI-6 closes those gaps with 10 new modules — all with pure,
unit-tested cores (30 tests total, CI-gated) and offline CLIs over the local data.

## Decision & risk layer (E10)

| Module | What it does | Pure core (tested) |
|---|---|---|
| [`portfolio.py`](portfolio.py) | Turns a signal set (e.g. today's GGG names) into a tradeable book: min-variance / max-Sharpe weights with **long-only, per-position cap, per-sector cap, and turnover control** vs an existing book. Reports the book's risk via `risk.py`. | `min_variance_weights`, `max_sharpe_weights`, `cap_weights`, `apply_sector_cap`, `blend_to_turnover` |
| [`risk.py`](risk.py) | The risk numbers a desk gates on for any return series: annualised vol, **historical VaR/CVaR**, max drawdown, Sharpe/Sortino, and a `risk_on/caution/risk_off` regime flag. | `max_drawdown`, `hist_var`, `cvar`, `sharpe`, `sortino`, `regime_flag` |
| [`meta_screen.py`](meta_screen.py) | Fuses the independent screens (Triple-Hit gate + DVM D/V/M + optional ML signal) into **one 0–100 conviction score**; weights renormalise over whichever components are present, plus a gate bonus. A name confirmed by several methods ranks above one confirmed by one. | `fuse`, `rank` |
| [`sector_rotation.py`](sector_rotation.py) | Uses the industry/peer parquet as a *signal*: groups by industry/sector/segment and ranks each by **12-1 month momentum** (skips the last month) + breadth. | `member_momentum`, `rank_groups` |
| [`alerts.py`](alerts.py) | Snapshots a result set (default: GGG list) daily and **diffs vs the prior snapshot** — new entrants / drop-outs as JSON, optional mailer. SMTP creds read from env vars only. | `diff_sets`, `format_alert` |
| [`unlisted_valuation.py`](unlisted_valuation.py) | Comparable-company valuation for **unlisted firms**: peer-median P/E and P/B from the same industry (companies ⋈ fundamentals), applied to a supplied financial → implied value range (IQR band). | `peer_multiple_band`, `value_range`, `implied_value` |

## Multi-market rigor (E11)

| Module | What it does | Notes |
|---|---|---|
| [`pit_global.py`](pit_global.py) | Extends the point-in-time backtest to **all 18 non-US markets** using the only thing that's genuinely PIT outside the US — **prices**: monthly-rebalanced Darvas breakout vs equal-weight benchmark, net of each market's round-trip cost. | The `--overlay` current-durability arm is **explicitly labelled look-ahead** (non-US has no filed-date history), so it's never passed off as clean PIT. |
| [`fx.py`](fx.py) | Maps each market to its currency and **normalises levels/returns to a base currency** (USD default), fixing the silent apples-to-oranges in cross-market rankings. Live rates via the governed apiclient with a dated offline snapshot. | `combine_return` compounds the FX move into a local return. |

## Data observability (E12)

| Module | What it does | Notes |
|---|---|---|
| [`data_quality.py`](data_quality.py) | Governance checks the *code*; the integrity manifest checks *files aren't tampered*; this checks the **data itself** — freshness (staleness), completeness (null rate), sanity (robust-MAD outliers), and per-symbol date monotonicity — with a CLI that **exits non-zero on failure**. | `staleness_days`, `null_rate`, `outlier_rate`, `evaluate` |

## Serving & incremental analytics (E8/E9, now shipped)

| Module | What it does |
|---|---|
| [`serve.py`](serve.py) | FastAPI read-only API over the DuckDB warehouse (`/markets`, `/ggg`, `/screen/{name}`, `/filter`). Predicate is **allow-list validated** against SQL injection. FastAPI/duckdb imported lazily so the pure query-builder is testable with no heavy deps. |
| [`dashboard.py`](dashboard.py) | One self-contained `dashboard.html` rendering the key warehouse views. `render_html` is pure and tested. |
| [`incremental.py`](incremental.py) | Partition-incremental refresh: `partition_diff`, `incremental_merge`, `append_new_dates` (per-symbol high-water mark). Wired into `warehouse.py --refresh-ohlc`. |
| [`feature_cache.py`](feature_cache.py) | ML feature-matrix cache keyed by a **content hash** of the input OHLC — re-runs recompute only genuinely changed series. Wired into `ml_viability.py`. |

## Quick start

```bash
# a constrained, risk-measured portfolio from today's GGG Strong Performers
python portfolio.py --market US --n 20 --method min_var --cap 0.10 --sector-cap 0.30

# fuse the screens into a single conviction ranking
python meta_screen.py --market US --top 25

# which industries to overweight (rotation)
python sector_rotation.py --by industry --market US

# global point-in-time backtest (prices) for Japan
python pit_global.py --market JP --years 5

# FX-normalise and check the data is fresh & sane
python fx.py --rates
python data_quality.py

# serve results over HTTP + build the dashboard
uvicorn serve:app        # needs: pip install fastapi uvicorn duckdb
python dashboard.py --open
```

All cores are covered by [`tests/`](tests/test_core.py) and enforced by CI
(`pytest` + `togaf.py govern` + integrity manifest).
