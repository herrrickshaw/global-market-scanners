# Scope — Point-in-Time Fundamentals for the Triple-Hit Backtest

**Goal:** quantify, over 5 years and *net of cost*, how much the fundamental gate
adds on top of a Darvas breakout — i.e. compare three arms:

| Arm | Rule |
|---|---|
| **A** | Darvas breakout only |
| **B** | Darvas breakout **+** Piotroski F-score ≥ 7 |
| **C** | Darvas breakout **+** F ≥ 7 **+** Coffee-Can (the Triple-Hit) |

If C ≫ A, the fundamental gate earns its keep. This needs **point-in-time (PIT)**
fundamentals — the financials *as they were known on each historical trade date*.

## Why PIT matters (the lookahead trap)
A stock's reported financials get **restated** in later filings. Example from SEC
EDGAR for AAPL FY2023 net income ($97.0B): it was first *filed 2023-11-03*, then
re-reported (as a comparative) in the 2024 and 2025 10-Ks. If a 2023 backtest used
the "latest" value, it would be leaking information filed years later. A correct
backtest at date *t* must use **only filings with `filed ≤ t`** (plus a reporting-lag
buffer so we don't trade on numbers before they were public).

## Data sources by market

| Market | Source | PIT? | Cost | Notes |
|---|---|---|---|---|
| **US** | SEC EDGAR XBRL (`companyfacts` / `companyconcept`) | ✅ true PIT via `filed` date | **free** | Already wired: `sec_fundamentals.py` maps ticker→CIK and extracts the concepts the strategies need. Only change: make it filed-date-aware. |
| India | screener.in scrape (10y P&L/BS) | ⚠️ as-restated, not truly PIT | free-ish | Semi-PIT; usable with a reporting-lag proxy. Fragile scrape. |
| India / global | **Sharadar SF1** (Nasdaq Data Link) | ✅ PIT (`datekey` = as-reported) | ~$50–150/mo | Cleanest global PIT; US + many intl. |
| Global | Financial Modeling Prep / EOD Historical Data | ⚠️ partial PIT | ~$20–80/mo | Historical statements w/ filing dates for many exchanges. |
| Japan/Korea/EU | vendor only (Sharadar/FMP/Refinitiv) | ✅/⚠️ | paid | No good free PIT source. |

## Recommended plan — MVP first (free, US-only)
1. **`pit_fundamentals.py`** — extend `sec_fundamentals.py` so every concept query
   returns the *time series* of `(period_end, filed_date, value)`, and add
   `as_of(ticker, date)` → the fundamentals dict using only `filed ≤ date`
   (default 2-day lag). Cache each CIK's `companyfacts.json` locally (SQLite/parquet)
   so the 5-year daily backtest hits disk, not the SEC.
2. **`pit_backtest.py`** — for the US universe, at each monthly rebalance over 5y:
   compute the Darvas signal (from the OHLC we already download) and the PIT
   F-score + Coffee-Can (from `as_of`), form arms A/B/C, hold to next rebalance,
   book **net-of-cost** returns (reuse `apply_costs.py`'s US 0.10%).
3. **Report**: CAGR / hit-rate / Sharpe / max-drawdown per arm → the added value of
   the gate. Store compact results in SQLite like the screen-viability work.

*Effort:* ~medium; **$0** data cost. Fully reproducible.

## Extension — global (paid)
Add a Sharadar (or FMP) adapter behind the same `as_of()` interface, keyed by an
env var (`SHARADAR_API_KEY`), then run arms A/B/C for India/Japan/Korea/Europe with
each market's net-of-cost figure. ~$50–150/mo, larger build.

## Known caveats
- **Survivorship bias:** the universe comes from *current* scan files (delisted names
  are absent) → results skew optimistic. A truly clean study needs a historical
  constituent list (Sharadar has one; EDGAR does not).
- **Coffee-Can needs ~10y history** for revenue CAGR / consistent-earnings tests —
  EDGAR XBRL is reliable from ~2009, so 5y windows are fine, longer ones thin out.
- Reporting-lag buffer and rebalance frequency are assumptions to sensitivity-test.
- Still pre-slippage beyond the spread proxy.

## Decision needed
- **US-only free MVP** (SEC EDGAR, build `pit_fundamentals.py` + `pit_backtest.py`), or
- **Global from the start** (provide a Sharadar/FMP key; larger build, paid).

Recommendation: build the **free US MVP** first — it answers "does the fundamental
gate add value?" rigorously at zero cost; globalise only if the US result is worth it.
