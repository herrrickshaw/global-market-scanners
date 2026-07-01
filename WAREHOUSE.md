# Data Warehouse (DuckDB)

`warehouse.py` is the **analytical layer** — one DuckDB SQL surface to update,
process, filter, and depict results across the whole platform, without copying
data. DuckDB reads the parquets directly and *attaches* the SQLite result DBs, so
views always reflect the latest files.

Two-tier design (per the Modern Data Architecture Blueprint):
- **Cassandra** (`market_store`) — operational store: OHLC cache, CDC, streaming.
- **DuckDB** (this) — analytical / serving layer: fast filtering & aggregation.

## Unified views (one `market.duckdb`)

| View | Rows | Source |
|---|---|---|
| `ohlc` | **7,739,066** | 19 markets' daily OHLC (`cleaned_long_*.parquet`, market column derived from filename) |
| `dvm_global` | 30,785 | technical DVM/Trendlyne metrics per stock |
| `companies` | 17,754 | industry/peer dataset |
| `fundamentals` | 731 | yfinance fundamentals (numerics coerced) |
| `dvm_composite` | 726 | global GGG/GGB/BBG classification |
| `viability` | 25 | screen-viability summary |

## Update
Just re-run the producers (`dvm_global.py`, `fundamentals_global.py`, scans…) — the
warehouse views point at the live files, so `warehouse.py` sees new data on the next
call. No import/ETL step. (SQLite attachments are per-session, so views are rebuilt
each invocation — cheap.)

## Process / filter / depict
```bash
python warehouse.py --show markets              # OHLC coverage per market
python warehouse.py --show ggg_global           # global GGG Strong Performers
python warehouse.py --show momentum_by_market    # high-momentum counts per market
python warehouse.py --show high_roe_low_de       # quality names across markets

# ad-hoc filter across momentum ⋈ fundamentals ⋈ classification, all markets:
python warehouse.py --filter "c.code='GGG' AND f.roe>15 AND f.de<1 AND c.M>=75"

# any SQL over 7.7M+ rows:
python warehouse.py --sql "SELECT market, count(*) FROM ohlc WHERE Close>Volume GROUP BY 1"
```

Example — the ad-hoc filter returns a ranked global list in ~a second:
`MTM.JO (ZA, ROE 18.8, M 87.6)`, `QBE.AX (AU)`, `000660.KS / SK Hynix (KR, ROE 61)`,
`ZURN.SW (Zurich Insurance)` …

## Notes
- `market.duckdb` is rebuildable (views over external files) and gitignored — the
  data lives in the parquets/SQLite, not the warehouse file.
- Python API: `import duckdb; con=duckdb.connect('market.duckdb')` then query the views
  (call `warehouse.build(con)` first to (re)attach in a fresh session).
