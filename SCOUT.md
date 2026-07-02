# Literature Scout — global equity-market research

[`literature_scout.py`](literature_scout.py) continuously scouts the world's open
scholarly literature for equity-market research, scores each paper against what
this platform already implements, maps it to the module that covers it, and — most
usefully — flags **research gaps**: active research areas the platform does *not*
implement yet.

## Seeded from the papers we've already implemented
The scout's relevance model is seeded from the sample literature this platform has
already built on:

Markowitz (1952) · Sharpe (1964) · Jegadeesh-Titman (1993) · Fama-French (1992) ·
Piotroski (2000) · Novy-Marx (2013) · Frazzini-Pedersen (2014) · Asness-Frazzini-
Pedersen QMJ (2019) · Gu-Kelly-Xiu ML (2020) · Jacob-Pradeep-Varma IIMA (2022).

These define the **covered themes** (`COVERED_THEMES` in the module) — each a set of
keywords mapped to the module(s) that implement it (quality→`quality_factor.py`,
value→`factor_research.py`, momentum→`dvm_global.py`, low-risk→`risk.py`, PIT
bias→`pit_*`, costs→`apply_costs.py`, ML pricing→`ml_*`, …). They double as the
built-in offline corpus (`SEED_PAPERS`), so the scout is fully runnable with no
network.

## How it works
1. **Fetch** — queries open scholarly APIs through the governed `apiclient`
   (polite-pool rate limits + adaptive backoff):
   - **OpenAlex** (keyless, abstracts + citation counts),
   - **Crossref** (keyless — the reliable fallback when OpenAlex is overloaded),
   - **arXiv** (q-fin preprints).
   Each fetch **fails fast** to the next source if one is down, so an OpenAlex 503
   or arXiv 429 never stalls the run. Set `SCOUT_MAILTO` (env) to join the APIs'
   polite pools — no credentials are stored in the repo.
2. **Score** (pure, unit-tested) — each paper gets a 0–1 relevance score blending
   keyword match, recency, and citations, and is tagged:
   - `covered` — an implemented theme (mapped to its module),
   - `extends` — implemented, but with a frontier angle,
   - **`gap`** — a **frontier theme not yet in the platform**,
   - `unmapped` — off-topic.
3. **Report** — writes `LITERATURE_SCOUT.md` (ranked papers + a research-gap table +
   a per-theme coverage summary) and `LITERATURE_SCOUT.json`.

## Frontier themes it watches (the opportunity radar)
Areas known to be active in the literature that this platform doesn't cover yet
(`FRONTIER_THEMES`): textual/NLP & LLM signals, option-implied measures, **post-
earnings-announcement drift / analyst revisions**, short interest & factor crowding,
liquidity factors, seasonality, supply-chain/network effects, ESG & climate risk,
and market microstructure. A hit against any of these is a candidate new
factor/module — the natural feed into the SAFe backlog.

_Example (live run):_ a query on earnings/short-interest surfaced **7 papers on
post-earnings-announcement drift (PEAD)** — a well-documented anomaly with no module
here — as the top research-gap opportunity.

## Quick start
```bash
python literature_scout.py                       # scout the seed themes (network, with fallback)
python literature_scout.py --query "option-implied volatility factor" --limit 25
python literature_scout.py --gaps                # only the research-gap opportunities
python literature_scout.py --offline             # score the built-in seed corpus (no network)
export SCOUT_MAILTO="you@example.com"             # join the APIs' polite pools (optional)
```

The pure core (`reconstruct_abstract`, `match_themes`, `score_paper`, `dedup`,
`rank`, `coverage_summary`, `render_report`) is covered by
[`tests/`](tests/test_core.py) and enforced by CI; the network layer degrades
gracefully and is never on the CI path.
