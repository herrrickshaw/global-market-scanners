# SAFe Implementation — Global Market Quant Platform

Implements the **Scaled Agile Framework** (SAFe® Big Picture) for this platform:
the work is organised as Strategic Themes → Epics → Features across Program
Increments (PIs), and made **operational** as a queryable backlog (`backlog.json`
+ `safe_backlog.py`) rather than a static poster.

```bash
python safe/safe_backlog.py portfolio     # Lean Portfolio view (themes -> epics)
python safe/safe_backlog.py roadmap       # PI-by-PI delivery
python safe/safe_backlog.py burnup        # story-point completion
python safe/safe_backlog.py pi PI-3       # one PI's objectives + features
python safe/safe_backlog.py kpis          # Measure & Grow
```

## SAFe layer → platform mapping

| SAFe construct | Here |
|---|---|
| **Portfolio** (Strategic Themes, Lean Budget, KPIs) | 4 themes: global screening · rigorous research · data backbone · secure/documented |
| **Value Stream** | VS1 *Signal-to-decision*: raw market data → screens/scores → validated, ranked candidates |
| **Epic** (portfolio initiative + hypothesis) | 9 epics (E1–E9), each with a testable hypothesis |
| **Agile Release Train (ART)** | "Global Market Quant Platform ART" — the one train delivering all epics |
| **Program Increment (PI)** | 5 PIs (foundation → research/ML → global scoring/backbone → hardening → serving) |
| **Feature** (delivers in a PI) | 35 features, each mapped to the concrete module(s) that realise it |
| **Story / Team backlog** | the commits under each feature (git history is the team backlog) |
| **Continuous Delivery Pipeline** | Continuous Exploration→Integration→Deployment→Release below |
| **Non-Functional Requirements (NFRs)** | performance, rate-limit safety, security/integrity — Epic E6/E7 |

## Continuous Delivery Pipeline (mapped)
- **Continuous Exploration** — factor research, ML screen discovery, viability backtests (E3/E4).
- **Continuous Integration** — signed commits, `verify_integrity.sh`, branch protection (E7).
- **Continuous Deployment** — cache-first incremental data, `apiclient` rate governance (E6).
- **Release on Demand** — the DuckDB warehouse + (planned) serving layer (E5/E8).

## Program Increment plan
| PI | Theme focus | Status |
|---|---|---|
| **PI-1** Scanner foundation & dataset | ST1 | ✅ 100% |
| **PI-2** Research & ML | ST2 | ✅ 100% |
| **PI-3** Global scoring & data backbone | ST1/ST3 | ✅ 100% |
| **PI-4** Hardening (perf, security, docs) | ST3/ST4 | ✅ 100% |
| **PI-5** Serving & incremental analytics | ST1/ST3 | ⬜ planned |

**Overall: 173/196 story points (88%).** The remaining 23 pts are PI-5 (serving API,
dashboard, partition-incremental results, ML feature cache).

## Lean Portfolio Management
- **Guardrails:** point-in-time correctness (no lookahead), pre-cost caveats stated,
  env-var-only secrets, signed history.
- **KPIs (Measure & Grow):** 19 markets · 30,785 tickers scored · dvm_global 13× faster
  · centralised rate-limit governance · signed+verified repo · 17 docs. Run
  `safe_backlog.py kpis` for the live list.

## Why "implement" = a working backlog
SAFe's value is turning strategy into a tracked flow of work. Here that flow is data:
epics/features carry status, size, PI, and the module that delivers them, so
`safe_backlog.py` gives Lean-Portfolio, roadmap, and burnup views on demand — a real
implementation of the framework, not a diagram.
