# SDLC — how the platform maps to the Software Development Life Cycle

Applies the classic SDLC phases (Lemke 2018; ISO/IEC 12207 lineage) to this platform,
and — the key learning — closes the one phase this project had been skipping:
**Integration & Testing**.

| SDLC phase | On this platform |
|---|---|
| **Initiation / Concept** | Vision & scope — README, SAFe Strategic Themes, the signal-to-decision value stream |
| **Planning & Requirements** | SAFe backlog (`safe/backlog.json`), PERFORMANCE roadmap, TOGAF Requirements Mgmt |
| **Design** | TOGAF architecture (`architecture/`), `ARCHITECTURE.md`/`ARCHITECTURE_MAP.md`, `SCHEMA.md` |
| **Development** | ~40 modules — scanners, DVM, backtest, ML discovery, warehouse, apiclient, decision layer (portfolio/risk/meta/rotation/alerts/comps), global PIT, FX, serving, data quality, AFP/QMJ quality factor |
| **Integration & Testing** | **NEW: `tests/` (pytest) + `.github/workflows/ci.yml`** — runs on every push |
| **Implementation / Deployment** | `git push` + LFS dataset; private `market-data-artifacts` backup |
| **Operations & Maintenance** | Cassandra cache + incremental delta fetches, `apiclient` rate governance, `market_holidays` run-gating |
| **Disposition** | Signed, immutable-forward git history + integrity manifest; nothing deleted, superseded via new commits |

## The learning applied: testing & CI (the gap)
The project was built iteratively (SDLC's iterative/agile model — each feature delivered
and smoke-tested), but had **no automated test suite or CI** — the classic weak spot.
Now:

- **`tests/test_core.py`** — 37 unit tests over the deterministic core logic, no network
  or DB needed: trading calendars, the rate limiter (interval + adaptive penalty),
  the net-of-cost model, **point-in-time filing-date filtering** (regression-guards the
  quarterly-vs-annual bug that was fixed earlier), feature engineering, the factor OLS,
  and DVM durability scoring — plus the PI-6 decision layer (portfolio caps, risk
  metrics, meta-screen fusion, FX, incremental refresh, feature-cache keys, serve
  query/injection guard, data-quality rules) plus the AFP/QMJ quality factor
  (standardised ranks, dimension scoring, QMJ/LQ combination, price-premium test).
  `pytest -q` → **83 passed** (incl. the literature scout, the PEAD event-study core,
  the Amihud liquidity core, the public data-source registry, the Ken French factor
  parser + Carhart alpha regression, the HFT-archetype proxies, and the Darvas ×
  volume-acquisition monitor — OBV/CMF/up-down volume, box formation with the current
  bar excluded, breakout/breakdown state).
- **`.github/workflows/ci.yml`** — on every push/PR to `main`:
  1. **Unit tests** (SDLC Integration & Testing)
  2. **Architecture governance** — `togaf.py govern` (10/10 principles must stay compliant)
  3. **Integrity check** — tracked-file hashes vs the committed `CHECKSUMS.sha256`

So a regression, a broken principle, or a tampered file now **fails CI** instead of
slipping through.

## Run locally
```bash
pytest -q                              # unit tests
python architecture/togaf.py govern    # architecture compliance
./verify_integrity.sh                  # file integrity
```

## Why this completes the picture
- **SDLC** — the life-cycle *phases* (this doc), with Testing/CI now real.
- **SAFe** — how *work* is planned & delivered (`safe/`).
- **TOGAF** — how the *architecture* is structured & governed (`architecture/`).
Three lenses, all operational: tested code, planned delivery, governed architecture.
