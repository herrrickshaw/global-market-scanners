# Evidence map — where each headline claim is backed up

Every quantitative claim made about this research stack in applications and
summaries, mapped to the file that produced it and to an independent re-test
where one exists. If a number here and its source disagree, the source wins;
if a re-test and an original claim disagree, the **re-test** is the more
conservative statement and should be the one quoted.

_Last reconciled: 2026-10-26._

## Data and infrastructure

| Claim | Evidence | Status |
|---|---|---|
| 10 years of daily data, 20 equity markets, ~44M OHLCV rows | [global-market-data README](https://github.com/herrrickshaw/global-market-data) (`cache_seed/ltm/*.parquet`, `warehouse/`); 20-market list in `market-pipeline/screener_kit.py` `MARKETS` | verified 2026-10-26: 48.3M rows in the 6-market warehouse (IN/US/JP/KR/EU/CN, 2016-01-01..2026-10-26, 31,354 symbols) + 2.7M in 14 extended `ltm` panels ≈ 51M; "~44M" is conservative |
| Point-in-time backtest, no look-ahead, 21-day edge vs equal-weight benchmark | `global-market-data/BACKTEST_FINDINGS.md`; [pit_backtest.py](pit_backtest.py) here | source located |
| Walk-forward ML overlay (TimeSeriesSplit), ~0.60 OOS directional accuracy, 22,102 India samples | `global-market-data/AUTO_SCREENER.md` (line 58) | source located |
| 60,971 validated tickers across 25 markets | [global-ticker-universe README](https://github.com/herrrickshaw/global-ticker-universe) | source located |
| Eurex single-stock derivatives universe from Deutsche Börse T7 API | global-ticker-universe README, "German Market — Eurex Derivatives Universe" (1,497 listings) | source located |
| 252-day correlation matrices, top 3,000 names, 6 markets; storage 24x smaller, ~2 min rebuild | [market-correlation-matrices README](https://github.com/herrrickshaw/market-correlation-matrices) | source located |
| karz: cross-source checks, no discrepancy >2%, 36/40 sources above 80% quality | [karz README](https://github.com/herrrickshaw/karz); `global-stock-screener/DATA_VALIDATION_IMPLEMENTATION_ROADMAP.md` | source located |
| 17,869 stocks scanned across five markets, 21 passed all three stages (Darvas → Piotroski → Coffee Can) | [claude-stock-tools README](https://github.com/herrrickshaw/claude-stock-tools), funnel table total row | source located |
| Options pricing library: Black-Scholes, Greeks, IV, Asian and barrier options, 23 unit tests | [financial-analysis-toolkit-public](https://github.com/herrrickshaw/financial-analysis-toolkit-public) `tests/test_options.py` (23 `test_` functions) | verified 2026-10-26 |
| 22.4% blended-return claim traced to a 272-stock sample and retracted | Retraction banners in [stock-screener-platform](https://github.com/herrrickshaw/stock-screener-platform) and [global-market-research-platform](https://github.com/herrrickshaw/global-market-research-platform) READMEs | source located |

## Signal edges — original claim vs desk-grade re-test

The original edges are mean 21-day excess returns from
`global-market-data/BACKTEST_FINDINGS.md`, with no t-statistics, no
multiple-testing correction and no costs. [signal_research.py](signal_research.py)
re-tests the same ideas on a liquid universe with HAC t-stats, BH-FDR across
every test, and net-of-cost Deflated Sharpe
([method](SIGNAL_RESEARCH.md), [results](SIGNAL_RESEARCH_RESULTS.md)).

Re-test universe: top 600–1,500 names per market by trailing dollar volume,
weekly formation 2017-07..2026-10, next-close entry, 21-day horizon.

| Original claim (BACKTEST_FINDINGS) | Re-test, 21d rank IC (HAC t, BH q) | Verdict |
|---|---|---|
| RSI-oversold is the only screen with a positive edge in all five markets (US +1.85, KR +2.04, CN +1.06, JP +0.93pp) | `rsi_os`: **KR 0.043 (t 4.9, q<0.001)**; IN 0.001 (0.1), US 0.001 (0.1), JP 0.007 (0.7), EU 0.009 (1.2) | **Holds in Korea only.** In liquid US/IN/JP/EU stocks it is noise; net of costs it loses everywhere at 73% turnover. The original means came with win rates below 50% (US 46%), i.e. a few large winners in a broad, less liquid sample. Quote it as a Korea result. |
| 50/200-day golden **crossover** is flat or negative in every market | Crossover *events* not re-tested. The continuous EMA50/EMA200 spread (`golden`) is **positive in IN (t 3.6, q 0.002) and EU (t 2.7, q 0.02)**, negative in KR (t −2.9) | **Keep the claim narrow:** the crossover *event* failed; trend *state* did not fail in India or Europe (consistent with the original's own `golden_state` IN +0.68pp). |
| India rewards momentum: stocks near their 52-week high, +1.33pp | `hi52` IN **0.054 (t 3.9, q 0.001)**, also 63d 0.083 (t 4.0); net-of-cost long-short +6.8%/yr | **Confirmed**, and it is the strongest timing signal in India's bull regime. |
| US, Korea and China mean-revert | 5-day reversal (`rev5`) significant in **KR** (t 3.6 @10d) and **IN** (t 4.0 @10d); not in US (t 0.6) or JP (t −0.3); CN not in this study | **Partly.** Korea yes; US not on liquid names. |
| Walk-forward ML overlay ~0.60 OOS directional accuracy (IN) | Purged walk-forward ridge over 11 signals, 10d OOS rank IC: IN 0.045 (vs 0.028 best single), KR 0.106 (vs 0.091) | **Supported in spirit:** combining signals beats the best single signal out of sample in IN and KR; not in US/EU/JP. |

**Multiple-testing note.** 240 market x signal x horizon tests were run; 89
survive BH-FDR at 5% (94 in the 2026-10-26 run, before one more European
trading day). No long-short strategy clears a Deflated Sharpe of 0.95
once all ~165 cost-adjusted variants are counted as trials. The Probability of
Backtest Overfitting is low (0.02–0.18), so picking the best variant is not
pure luck, but its out-of-sample Sharpe is a quarter to a half of its
in-sample one, and the profitable long-short books have a median max drawdown
of −34%. That is the honest
ceiling for daily-bar, retail-cost signals in these universes.
