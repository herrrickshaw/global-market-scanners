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
| **L1-BR** | Concrete non-US filed-date fetcher (proof of the generalisation) | **Brazil CVM** open-data fetcher: pulls DFP/ITR ZIPs, reads the `DT_RECEB` filing date, emits a point-in-time fundamentals panel (revenue/NI/assets/equity/ROE). Equity matched by description so it works for BOTH the industrial (2.03) and financial-sector (2.07) taxonomies | `brazil_cvm.py` · `parse_header`, `parse_statement`, `parse_equity`, `build_panel`, `filed_panel`, `plausible_roe` | `test_cvm_*` (6) |
| **L1-BR test** | End-to-end point-in-time quality test on Brazil (the pay-off) | Join the CVM filed-date ROE to B3 prices via a **name-verified** CD_CVM→ticker map; quality measured only from filings received before the return window; quintile IC / spread / monotonicity | `brazil_quality.py` · `quality_asof`, `to_tickers`, `forward_returns`, `quality_quintiles` | `test_bq_*` (4) |
| **L5** | Short history under-powers some markets | Minimum-history gate — withhold rather than guess | `pit_panel.py` · `min_history_ok`, `gate_markets` | `test_pit_panel_history_gate` |
| **L1, L2, L3** | No filing dates outside the US | Registry of primary regulatory filing systems per market (with filing dates → PIT fundamentals **and** real event dates) | `data_sources.py` · `REGULATORY_FILINGS`, `filing_source`, `filing_coverage` | `test_filing_source_registry` |
| **L6** | India (source market) absent | Reproduce QMJ on India's deep-history panel | uses `quality_factor.py` on the India repo's PIT panel — **needs that data**; logic in place | (covered by existing quality tests) |

## What is code vs what needs data

- **Fully implemented & tested (pure logic):** L8, L4, L5, and the L1 point-in-time
  core. These run today with no network.
- **Brazil L1 end-to-end and validated:** `brazil_cvm.py` ingests CVM open data
  (real DFP 2023: ~475 companies, genuine filing dates — Petrobras ROE 32.7%,
  Vale 20.4%, Banco do Brasil 19.1% after the financial-sector equity fix), and
  `brazil_quality.py` runs an actual **point-in-time quality test**: quality (ROE)
  known as of 2025-06-26 vs the following 6-month B3 return over 15 name-verified
  companies gives IC +0.26, Q5−Q1 +13% (short-window demonstration, not a powered
  magnitude — the paper's L5 caveat). Remaining L1/L2/L3 work is the *other*
  markets' fetchers (registry + discipline already in place).
- **Pending external data:** L6 needs the separate India repository's ten-year
  point-in-time panel; `quality_factor.py` already accepts such a panel.

## Coverage snapshot

`python data_sources.py` and `python -c "import data_sources as d; print(d.filing_coverage())"`
report the per-market filing-source coverage. `python net_of_cost.py`,
`python survivorship.py`, and `python pit_panel.py` print worked examples.

*Not investment advice.*
