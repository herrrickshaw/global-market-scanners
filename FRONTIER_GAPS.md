# Frontier research gaps — all closed

The [literature scout](SCOUT.md) tracked seven **frontier themes** the platform didn't
implement. This closes all seven; the scout now reports **zero open gaps** (21/21
themes covered). Each is honest about the data it can and can't use.

| Gap | Module | Source | Result / boundary |
|---|---|---|---|
| **seasonality** | [`seasonality.py`](seasonality.py) | prices (offline) | Day-of-week + turn-of-month are estimable on 1y data — US turn-of-month edge **+0.22%**. Monthly / Sell-in-May need multi-year (skipped, documented). |
| **network** | [`peer_network.py`](peer_network.py) | industry peers + prices | Economic-links lead-lag (Cohen-Frazzini): peer basket's lagged return vs stock forward return. In the 1y US sample the relation is **negative (IC −0.08)** — industry-level *reversal*, reported faithfully. |
| **short_crowding** | [`crowding.py`](crowding.py) | prices (offline) | Co-movement crowding proxy (rank-based: market correlation + run-up). Surfaces crowded momentum names (RIOT/BULZ/MTRN). **True short-interest needs a vendor feed** — stated. |
| **text_nlp** | [`news_sentiment.py`](news_sentiment.py) | yfinance `.news` | Headline sentiment via a **Loughran-McDonald finance lexicon**. Live: TSLA +0.30, NVDA +0.20, AAPL −0.10. Full LM dictionary drops in to replace the compact lists. |
| **options_implied** | [`options_iv.py`](options_iv.py) | yfinance option chain | ATM IV, put/call ratio, IV skew ("fear gauge"). Aggregators unit-tested; **yfinance option IV/OI data is thin/inconsistent** — the pure math is correct, live quality varies. |
| **esg_climate** | [`esg_screen.py`](esg_screen.py) | yfinance `.sustainability` | Sustainalytics ESG-risk screener + E/S/G pillars + grade bands. **Yahoo currently 404s the sustainability endpoint** — the normalisation is tested and an alternate ESG source drops in. |
| **microstructure** | [`hft_selection.py`](hft_selection.py), [`darvas_volume.py`](darvas_volume.py) | prices (offline) | Already implemented as the **Tier-1** microstructure screen (efficiency ratio, Corwin-Schultz spread, accumulation). **Tier-2 order-book signals need a tick/LOB feed** — the documented boundary. |

## The recurring pattern
Three gaps were fully closable from data on hand (seasonality, network, crowding —
offline, validated). Three needed a fetch source and got one via yfinance (news,
options, ESG) — with the honest note that two of those Yahoo endpoints are currently
flaky, while the **pure cores are unit-tested** regardless. One (microstructure) was
already covered at the tradeable Tier-1 level, with the tick-data boundary stated.

## The scout loop, complete
```
scout finds gap  ->  implement a module  ->  scout re-classifies it 'covered'
```
Run twice explicitly earlier (PEAD, liquidity), then in one pass for the remaining
seven. `python literature_scout.py --offline` now shows every seed paper mapping to a
module and **no research-gap section** — the frontier list is empty until new themes
emerge.

All pure cores are covered by [`tests/`](tests/test_core.py) (89 tests) and enforced by
CI; the network fetchers degrade gracefully offline so CI never depends on them.
