# Factor Research — four foundational papers as testable proposals

`factor_research.py` tests four classic finance papers as falsifiable hypotheses
on our own liquid US universe, **point-in-time** (features measured at date T,
returns realised after T; size/value from SEC EDGAR as-of T, no lookahead).

| # | Paper | Proposal tested | How |
|---|---|---|---|
| **P1** | Markowitz (1952) | Diversification is the only free lunch — diversified portfolios beat concentrated picking on risk-adjusted return | Build min-variance & max-Sharpe (tangency) portfolios from the candidate set; compare realised return / ex-ante vol vs a single "best" pick and equal-weight |
| **P2** | Sharpe (1964) CAPM | Systematic risk (beta) is priced — return rises with beta | Beta quintile sort + OLS `fwd_ret ~ beta`; CAPM needs a positive, significant slope |
| **P3** | Fama-French (1992) | Beta explains ~nothing; **size** and **value** drive returns | Multivariate OLS `fwd_ret ~ beta + log(size) + earnings_yield`; expect beta insignificant, small-size & high-value significant |
| **P4** | Fama (1991) EMH | Which premiums are real "cracks" vs noise? | Report which factor t-stats survive `|t|>2` — the admitted anomalies |

## Run
```bash
python factor_research.py --limit 500 --min-dollar-vol 2e6
```
Prices via the Cassandra cache (`market_store`); fundamentals via point-in-time
EDGAR (`pit_fundamentals`). Regressions via scipy/numpy.

## Reading the output
- **P1** is the cleanest to confirm: min-variance almost always shows far lower
  ex-ante vol than a concentrated pick for comparable return — Markowitz's free lunch.
- **P2 vs P3** is the famous tension: if the beta coefficient is insignificant while
  `log_size` (negative = small wins) and `value_ey` (positive = cheap wins) are
  significant, that *reproduces Fama-French 1992's overturning of CAPM* on our data.
- **P4** is the summary verdict — the surviving factors are the market's exploitable
  inefficiencies (Fama's own admitted cracks).

## Caveats
- Single cross-section (one T, 12-month forward) on liquid US large-caps — a proper
  study pools many rebalance dates and includes small-caps (where size/value effects
  are strongest). Treat as a directional replication, not a full factor study.
- Survivorship bias (current universe) and pre-cost, as elsewhere in this repo.
