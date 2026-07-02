# TOGAF Implementation — Enterprise Architecture

Implements **TOGAF®** for the platform: an Architecture Principles catalog (TOGAF's
Name / Statement / Rationale / Implications template), the four architecture domains,
the ADM cycle mapped to real artifacts, and **Architecture Governance made executable**
(`togaf.py` verifies every principle against the actual repo).

```bash
python architecture/togaf.py principles   # the principles catalog
python architecture/togaf.py adm          # ADM phase -> platform mapping
python architecture/togaf.py domains      # the four BDAT domains
python architecture/togaf.py govern       # run compliance checks (ADM Phase G)
```

## Architecture domains (BDAT)
- **Business** — signal-to-decision: multi-market screening, backtesting, research.
- **Data** — query-driven Cassandra (operational) + DuckDB warehouse (analytical) +
  point-in-time EDGAR + 19-market parquet seed. See [SCHEMA.md](../SCHEMA.md).
- **Application** — modular apps (scanners, DVM/Trendlyne, backtest, ML discovery,
  warehouse, apiclient). See [ARCHITECTURE_MAP.md](../ARCHITECTURE_MAP.md).
- **Technology** — Python · Cassandra/Kafka/Flink · DuckDB · yfinance/EDGAR/GLEIF ·
  Git+LFS. See [INFRA.md](../INFRA.md), [ARCHITECTURE.md](../ARCHITECTURE.md).

## Architecture Principles (10)
Full catalog in `principles.json` / `togaf.py principles`. Summary:

| # | Domain | Principle |
|---|---|---|
| P1 | Business | Primacy of Principles |
| P2 | Business | Research Integrity (no lookahead, honest caveats) |
| P3 | Data | Data is a Shared, Query-Driven Asset |
| P4 | Data | Cache-First Data Access |
| P5 | Data | Point-in-Time Data |
| P6 | Data | Data Quality at the Boundary |
| P7 | Application | Reuse Over Rebuild |
| P8 | Application | Loose Coupling — Operational vs Analytical |
| P9 | Technology | Controlled Interoperability (Rate Governance) |
| P10 | Technology | Security & Reproducibility |

## ADM cycle → platform
Each phase maps to a real artifact (run `togaf.py adm`):
Preliminary→this catalog · A Vision→README · B Business→value stream/USER_GUIDE ·
C Data→SCHEMA · C Application→ARCHITECTURE_MAP · D Technology→INFRA/ARCHITECTURE ·
E Opportunities→SAFe epics/PERFORMANCE · F Migration→SAFe PI plan · **G Governance→
`togaf.py govern` + branch protection + signed commits** · H Change→signed history +
integrity manifest · Requirements Mgmt→SAFe backlog.

## Architecture Governance (Phase G) — executable
`togaf.py govern` verifies each principle against the repo (no hardcoded secrets;
data scripts cache-first; fetchers rate-governed; point-in-time filtering; quality
guards; shared building blocks; warehouse present; core docs present). Current status:
**10/10 principles compliant.** This is the differentiator — governance is a passing
check, not a promise.

## Relationship to SAFe
TOGAF answers *how the architecture is structured and governed*; [SAFe](../safe/SAFE.md)
answers *how the work is planned and delivered* (ADM Phases E/F/Requirements ↔ the SAFe
backlog/PIs). Together: governed architecture, planned delivery.
