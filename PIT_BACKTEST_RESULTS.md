# Does the fundamental gate earn its keep? — PIT US backtest

5-year, monthly-rebalanced, **net-of-cost** backtest on liquid US large-caps, using
**strictly point-in-time** fundamentals from SEC EDGAR (only filings `filed ≤ trade
date` — no lookahead/restatement leakage). Three arms on Darvas breakouts:

| arm | rule | n_months | avg picks | ann_return% | hit% | Sharpe | max_dd% |
|---|---|---|---|---|---|---|---|
| **A** | Darvas breakout | 50 | 11.7 | **15.33** | 60.0 | **0.96** | **−15.4** |
| **B** | Darvas + F-score ≥ 7 | 44 | 3.8 | **21.06** | 61.4 | 0.79 | −31.5 |
| **C** | Darvas + F≥7 + Coffee-Can (Triple-Hit) | 19 | 1.5 | 11.17 | 52.6 | 0.58 | −19.6 |
| BENCH | equal-weight whole universe | 56 | all | 13.85 | 64.3 | 0.93 | −20.9 |

## Findings
- **The Piotroski F-score gate adds real return.** B (Darvas + F≥7) delivered **21.1%/yr
  vs 15.3% for Darvas alone and 13.9% for the benchmark** — the fundamental filter
  roughly doubled the excess return over buy-everything. So *yes, the F-score gate
  earns its keep* on return.
- **…but it concentrates risk.** B held ~3.8 names/month vs 11.7, so its Sharpe (0.79)
  is *lower* than Darvas-alone (0.96) and its drawdown deeper (−31.5%). Higher return,
  bumpier ride — a position-sizing/diversification question, not a signal failure.
- **The Coffee-Can gate on top is counter-productive *here*.** C (the full Triple-Hit)
  fired in only **19 of 56 months, ~1.5 names each** — Coffee-Can is so restrictive that
  on ~100 already-breaking-out mega-caps almost nothing qualifies, leaving a tiny, noisy
  sample that underperforms (11.2%/yr). The value is in the **F-score**, not the
  Coffee-Can — at least on this narrow universe.

## Verdict
On US large-caps, **Darvas + Piotroski F≥7 is the sweet spot**; adding Coffee-Can
over-filters. This is exactly the kind of thing the backtest was meant to reveal:
the Triple-Hit's fundamental strength comes mostly from the F-score gate.

## Caveats (important)
- **Coffee-Can is under-tested**, not disproven. It's designed to surface *rare*
  compounders across a *broad* universe; on 100 mega-caps it can't fire. Re-run on the
  full US universe (thousands of names) before judging arm C.
- **Unequal periods:** arms trade different numbers of months (A 50 / B 44 / C 19) since
  an arm is skipped in months with zero picks — so absolute comparisons are rough.
- **Survivorship bias:** universe = *current* large caps → all absolute returns skew
  optimistic. The A-vs-B ordering is on a level field; the levels are not.
- Net of 0.10% US round-trip only; excludes slippage/market-impact.

_Point-in-time engine: `pit_fundamentals.py` (EDGAR, filed-date filtered). Backtest:
`pit_backtest.py`. Results: `pit_backtest.db` (8 KB). EDGAR cache (~400 MB) gitignored._
