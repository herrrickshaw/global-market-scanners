# Signal research — the mailer's signals, validated the way a quant desk would

`signal_validation.py` (pure statistics, unit-tested) + `signal_research.py`
(driver) re-test every signal the daily watchlist mailer acts on, across
**10 years x 5 markets** (India, US, Japan, Korea, Europe), with the validation
standard that systematic-equities research roles ask for. Results:
[SIGNAL_RESEARCH_RESULTS.md](SIGNAL_RESEARCH_RESULTS.md) (regenerated on every run).

```bash
python signal_research.py                    # all 5 markets, ~5 min, writes reports
python signal_research.py --markets IN KR    # subset
pytest -q tests/test_signal_validation.py    # 17 tests, synthetic planted-answer panels
```

## Key findings (run of 2026-10-27, data through 2026-10-26)

1. **Only India and Korea have robust daily-bar technical signals on liquid
   names.** In the US, Japan and Europe almost nothing survives HAC t-stats
   plus BH-FDR. Of 240 tests, 89 survive, and they come mostly from IN and KR.
   (The 2026-10-26 run had 94; the difference is one more complete European
   trading day.)
2. **Since 2026-10-26 the mailer pins `hi52` as the IN and KR bull rule**,
   and both score OK and HEALTHY (10d IC 0.047, t 4.3; 0.054, t 5.7). The
   IN and KR bear rule (`revert`) is also OK (t 3.5 and 4.6). The US, Japan and
   Europe rules (`mom126` bull, `revert` bear) are all WEAK/NOISE.
3. **India momentum is real.** 52-week-high proximity has an IC of 0.04–0.08
   across every horizon (t 3.5–4.0) and clears costs (+6.8%/yr net,
   long-short). It is the best bull-regime signal for India; the mailer
   currently uses `trend` there (t 1.0).
4. **RSI-oversold works in Korea only** (21d t 4.9). It turns over 73% a period
   and loses after costs everywhere else.
5. **Signals are not orthogonal.** In the joint Fama-MacBeth regression,
   `golden` and `mom126` (the mailer's bull rule in US/JP/EU) add nothing in any
   market (no |t| >= 2), and `trend` adds value only in Korea, where it is negative. The
   orthogonal survivors are `hi52`, `lowvol`, `rev5` and `mom12_1`. A purged walk-forward ridge over all
   11 signals beats the best single signal out of sample in India (0.045 vs
   0.028) and Korea (0.106 vs 0.091), but not in the US, Japan or Europe.
6. **Nothing clears a Deflated Sharpe of 0.95** once the 165 cost-adjusted
   variants are counted as trials. Daily bars and retail costs are the ceiling.
7. **The ranking itself is not overfit, but the edge is small.** Probability
   of Backtest Overfitting (CSCV over every market x signal) is 0.02 at 5d,
   0.13–0.18 at 21d and 0.03–0.13 at 63d, so the in-sample best usually stays
   above the out-of-sample median. Its out-of-sample Sharpe is roughly half to a
   quarter of the in-sample one, though (21d: 0.40 → 0.09 per period), and it
   loses out of sample in 25–28% of 21d splits. That is consistent with
   finding 6: the selection is real, but the edge is too small to clear the
   deflated hurdle.
8. **Where the spread is earned differs by market.** In the US, 12-1 momentum
   and the golden spread are earned **overnight** (mom12_1 21d: +1.33%
   overnight, t 4.6; −0.19% intraday), as Lou, Polk & Skouras (2019) find. In
   India, Korea and Europe, momentum, hi52 and low-vol are earned
   **intraday** against a losing overnight leg (IN hi52 21d: −1.97% overnight,
   t −7.0; +3.29% intraday, t 6.1). Reversal and RSI-oversold go the other way
   in IN/EU/JP: they gain overnight and give it back intraday. See the India
   caveat under Limits before reading India's split literally.
9. **The surviving long-short books have deep drawdowns.** Of the 69
   long-short rows that make money after costs, the median max drawdown is
   −34%, and 59% have negative skew. India `mom12_1` 21d (+14.6%/yr net) has
   a CVaR95 of −15.9% per period and a −43% max drawdown. India `hi52` 21d
   (+6.8%/yr) has −19.0% and −47%.

Mailer changes: the proposed IN and KR bull rule `trend` → `hi52` is now
pinned in `zone_regime.json`. Still open, for review in `market-pipeline`:
the US/JP/EU rules have no signal with a real edge, so either drop them or
replace them with that market's strongest signal in section 3 of the results;
and read `signal_health.json` each morning so a FLIPPED rule is surfaced
before it trades.

## Why: what the job descriptions ask for

Requirements were read from the full text of 22 open quant-researcher postings
(pulled 2026-10-26 from each firm's own Greenhouse/Workable board). The same few
asks recur almost everywhere:

| Requirement (recurring wording) | Who asks | Where it lives here |
|---|---|---|
| "statistically robust signals", "rigorous statistical methods" | IMC (Mumbai, London, NY, HK), QRT, WorldQuant, Squarepoint, DRW | `rank_ic_series` + `newey_west_t`: per-date rank IC with HAC t-stats (overlapping windows otherwise overstate t by ~sqrt(overlap)) |
| "risks of overfitting", "hypothesis testing, experimental design" | IMC Lead Alpha (London), IMC HK, Trexquant | `bh_fdr` across every market x signal x horizon test; Harvey-Liu-Zhu \|t\|>=3 hurdle; `deflated_sharpe` (Bailey & López de Prado 2014) across all strategies tried |
| "market-neutral signals" | Trexquant | `neutralize`: IC after removing liquidity and volatility exposure |
| "value-adding and orthogonal features across multiple families" | IMC London, IMC Mumbai (HFT), IMC Lead Alpha | `fama_macbeth`: joint coefficients, so a signal that only repeats another shows up as redundant |
| "transaction costs, liquidity, portfolio construction" | DRW Singapore, BestEx (TCA), Squarepoint long-term equities | `quantile_spread_turnover`: turnover, break-even round-trip cost, net-of-cost return using `apply_costs.COSTS_PCT` |
| "robust model validation", "backtest hypotheses through rigorous simulations" | IMC Lead Alpha, Trexquant, Da Vinci | `purged_walk_forward`: expanding window with an embargo, so training labels never overlap the test fold; compares the best single signal, an IC-weighted blend and a ridge model, each chosen on training data only |
| "monitor signal behaviour and model performance over time", "drift and degradation" | QRT, BestEx, Squarepoint, DRW | `ic_health` → `reports/signal_research/signal_health.json` (HEALTHY / DECAYING / FLIPPED / NOISE per market x signal) |
| "point-in-time data cleaning, ticker mapping" | DRW London/Singapore | Already strong in this repo (`pit_fundamentals.py`, `survivorship.py`, `corporate_actions.py`); this study adds a trailing-only liquidity gate and India's corporate-action guard |
| NSE/BSE, Indian market structure | IMC Mumbai, WorldQuant IPM, Squarepoint Bangalore | India is a first-class market in every table |

### The 22 profiles

India: [IMC QR (Mumbai)](https://job-boards.eu.greenhouse.io/imc/jobs/4983672101) ·
[IMC Research Analyst Equities](https://job-boards.eu.greenhouse.io/imc/jobs/4872624101) ·
[WorldQuant QR](https://job-boards.greenhouse.io/worldquant/jobs/4673030006) ·
[WorldQuant QR – IPM](https://job-boards.greenhouse.io/worldquant/jobs/4715001006) ·
[QRT QR](https://job-boards.greenhouse.io/quberesearchandtechnologies/jobs/5953097002) ·
[Graviton QR](https://job-boards.greenhouse.io/gravitonresearchcapital/jobs/4004918002) ·
[Squarepoint Sr QR Equity Mid/Low Freq](https://www.squarepoint-capital.com/open-opportunities?id=253411&gh_jid=253411) ·
[Squarepoint Sr QR Intraday Stat Arb](https://www.squarepoint-capital.com/open-opportunities?id=253413&gh_jid=253413) ·
[Quantbox Experienced QR](https://job-boards.eu.greenhouse.io/quantboxresearchpte/jobs/4001033101) ·
[Da Vinci Experienced Quant](https://job-boards.eu.greenhouse.io/davinciderivatives/jobs/4046811101) ·
[Trexquant QR Experienced](https://apply.workable.com/j/962349D5DB) ·
[BestEx Senior QR](https://apply.workable.com/j/532E061164)

Global equities: [IMC Lead Alpha Researcher, Systematic Equities (London)](https://job-boards.eu.greenhouse.io/imc/jobs/4904726101) ·
[IMC QR Equities (London)](https://job-boards.eu.greenhouse.io/imc/jobs/4588833101) ·
[IMC QR Equities (NY)](https://job-boards.eu.greenhouse.io/imc/jobs/4382558101) ·
[IMC QR Equities/Futures (HK)](https://job-boards.eu.greenhouse.io/imc/jobs/4754254101) ·
[Squarepoint Jr QR ML Alpha](https://www.squarepoint-capital.com/open-opportunities?id=6069464&gh_jid=6069464) ·
[Squarepoint QR Equity Mid-Freq (NY)](https://www.squarepoint-capital.com/open-opportunities?id=8257365&gh_jid=8257365) ·
[HRT QR Mid-Freq](https://www.hudsonrivertrading.com/careers/job/?gh_jid=3903145) ·
[DRW QR Equities (London/SG)](https://job-boards.greenhouse.io/drweng/jobs/7228276) ·
[DRW QR Equities (SG)](https://job-boards.greenhouse.io/drweng/jobs/7439743)

Postings change; links were live on 2026-10-26.

## What is tested

**Signals.** The mailer's own (from `market-pipeline/.../watchlist_digest.py` and
`zone_rules.json` / `zone_regime.json`), plus four families this repo's
literature covers but the mailer does not use:

| signal | definition | mailer rule |
|---|---|---|
| `rev5` | −(5-day return) | `revert` |
| `mom21` | 21-day return | `mom_st` |
| `mom126` | 126-day return | `mom126` |
| `trend` | close / EMA50 − 1 | `trend` |
| `zone` | +1 close>EMA20>EMA50, −1 close<EMA50 | the digest's BUY/SELL zone |
| `golden` | EMA50 / EMA200 − 1 | golden_cross screen |
| `hi52` | close / 252-day high | Darvas / breakout proximity (George & Hwang 2004) |
| `mom12_1` | 12-month return skipping the last month | academic momentum (Jegadeesh & Titman 1993) |
| `lowvol` | −(63-day return volatility) | — (low-risk anomaly, Ang et al. 2006) |
| `illiq` | Amihud (2002) \|r\| / dollar volume, 63d | — (ties to the Piotroski "edge = illiquidity" finding) |
| `volsurge` | log(20d / 126d average volume) | Darvas volume confirmation |

**Universe.** Every Friday, the top-N names by trailing 63-day median dollar
volume with ≥1 year of history (IN 750, US 1,500, JP 1,000, KR 750, EU 600).
The gate is strictly trailing, and delisted names stay in the sample until
they stop trading.

**Forward returns.** Entry at the **next** close (the mailer is read in the
morning), held 5 / 10 / 21 / 63 trading days. 10 days is the horizon the
mailer's zone rules are scored on.

**Regime.** Bear = fewer than 45% of the liquid universe above their 200-day
average. This is the mailer's breadth definition (`strategy_regime_survival.py`).

**Overfitting, tail risk and return timing.** Section 5 of the results adds a
CVaR95 (mean of the worst 5% of net periods) and a max drawdown (compounded
net series) to every long-short row. Section 5b computes the Probability of
Backtest Overfitting by combinatorially symmetric cross-validation (Bailey,
Borwein, López de Prado & Zhu 2017): per horizon, all market x signal net
series are aligned by holding bucket, cut into 16 blocks (8 at 63d), and every
half of the blocks is in turn in-sample. It is run on the reported sign only,
and again with the opposite trade of every signal added, since the reported
sign was picked on the full sample. Section 5c splits each quintile spread's
forward log return into its close→open and open→close legs (Lou, Polk &
Skouras 2019), at 5d and 21d.

**India data hazard.** The India panel is raw bhavcopy close: splits and
bonuses look like −50% to −99% crashes. Any signal or forward window that
contains a 1-day move outside [−50%, +100%] is dropped, and ETF-like symbols
are excluded. This is a heuristic: it also removes some real crashes and
multi-baggers. The proper fix is adjusted closes.

## Limits

- No sector neutralisation: the warehouse has no PIT sector map for all five
  markets. Neutral IC removes liquidity and volatility only.
- `lowvol` has no neutral IC. It is `-vol63` and the volatility exposure is
  `vol63`, so its rank is an exact linear function of the exposure rank and the
  neutralised residual is floating-point round-off (median |residual| ≈ 2e-16,
  against ≈ 0.24 for `rev5`). Earlier runs printed a number there; it was the IC
  of round-off noise and depended on the OLS solver (numpy `lstsq` and the
  kdb+/q port's normal equations disagreed on it). `neutralize` now leaves any
  date NaN when the residual's dispersion is below 1e-12 of the signal rank's,
  so the cell reads n/a. `lowvol` is the volatility factor itself, so its raw
  IC is the number to read.
- Costs are retail round-trip estimates from `apply_costs.py`, with no market
  impact. Break-even bps is the honest number to compare against your own broker.
- Weekly formation, daily bars. Nothing here speaks to intraday or HFT roles
  (Quantbox, DeepFin, Graviton's HFT desks); those need tick or order-book data.
- **Overnight/intraday split, India:** NSE's official close is the
  volume-weighted price of the last 30 minutes, not the last trade, so
  "intraday" ends on a VWAP while the next "overnight" leg starts from it. That
  blurs the split most for illiquid names (`illiq` shows −3.98% overnight vs
  +4.28% intraday at 21d). Opens equal the prior close on 3% (US) to 12%
  (Korea) of liquid-name days; tick size and opening auctions explain part of
  that, but a stale open would push return out of the overnight leg.
- PBO candidates are correlated: the same signal appears in five markets, and
  one market's signals overlap. With 37 periods at 63d there are only 70 splits.
- The loader drops a trailing date with under half the usual row count, which
  is a still-forming vendor bar (the US bar for 2026-10-26 had 1,075 universe
  rows against a median of 2,850).
- Europe mixes currencies. Rank IC is unaffected, but dollar-volume ranks lean
  towards larger-currency listings.
