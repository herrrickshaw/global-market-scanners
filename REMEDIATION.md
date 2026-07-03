# Remediation roadmap — implementation status

Maps each limitation from the paper's §6.1 remediation roadmap
(`RESEARCH_PAPER_DETAILED.md`) to the code that implements the fix. Every logic
core here is a pure function with unit tests in `tests/test_core.py`; the parts
that need external data are marked, with the public source registered in
`data_sources.REGULATORY_FILINGS`.

| # | Limitation | Fix — implemented | Module · function | Tests |
|---|---|---|---|---|
| **L8** | Gross (pre-cost) returns | Net-of-cost spreads + break-even cost, with an Amihud-linked price-impact term | `net_of_cost.py` · `round_trip_cost`, `net_spread`, `break_even_cost`, `survives_costs`, `net_report` | `test_net_of_cost_*` |
| **L4** | Survivorship (current names only) | Point-in-time universe incl. delisted names; splice delisting returns; quantify the bias | `survivorship.py` · `point_in_time_universe`, `apply_delisting_returns`, `survivorship_gap` | `test_point_in_time_universe_*`, `test_apply_delisting_returns_and_gap` |
| **L1** | Snapshot (not point-in-time) fundamentals outside the US | Market-agnostic filed-date panel: use only figures filed on/before T (the EDGAR discipline, generalised) | `pit_panel.py` · `as_of`, `leaks_lookahead`, `is_point_in_time` (US engine already in `pit_fundamentals.py`) | `test_pit_panel_as_of_no_lookahead` |
| **L5** | Short history under-powers some markets | Minimum-history gate — withhold rather than guess | `pit_panel.py` · `min_history_ok`, `gate_markets` | `test_pit_panel_history_gate` |
| **L1, L2, L3** | No filing dates outside the US | Registry of primary regulatory filing systems per market (with filing dates → PIT fundamentals **and** real event dates) | `data_sources.py` · `REGULATORY_FILINGS`, `filing_source`, `filing_coverage` | `test_filing_source_registry` |
| **L6** | India (source market) absent | Reproduce QMJ on India's deep-history panel | uses `quality_factor.py` on the India repo's PIT panel — **needs that data**; logic in place | (covered by existing quality tests) |

## What is code vs what needs data

- **Fully implemented & tested (pure logic):** L8, L4, L5, and the L1 point-in-time
  core. These run today with no network.
- **Wired, pending data ingestion:** L1/L2/L3 for non-US markets — the *discipline*
  (`as_of`) and the *source registry* (`REGULATORY_FILINGS`, 13/19 markets carry
  filing dates, 6 with a public API) are in place; a per-market fetcher that pulls
  each regulator's filings into a filed-date panel is the remaining ingestion work.
- **Pending external data:** L6 needs the separate India repository's ten-year
  point-in-time panel; `quality_factor.py` already accepts such a panel.

## Coverage snapshot

`python data_sources.py` and `python -c "import data_sources as d; print(d.filing_coverage())"`
report the per-market filing-source coverage. `python net_of_cost.py`,
`python survivorship.py`, and `python pit_panel.py` print worked examples.

*Not investment advice.*
