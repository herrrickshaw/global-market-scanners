# A Reproducible Multi-Market Equity-Factor Platform: Construction, Cross-Sectional Evidence, and the Primacy of Measurement

**Umashankar Triplicane Dwarakanathan** · Independent Researcher

**Working paper — Global Market Scanners project**
*Version 1.0 · generated 2026-07-03 · commit-signed, CI-verified*

---

## Abstract

We build and openly document a reproducible, multi-market equity-research platform
(~40 Python modules, 19 markets, 102 CI-gated unit tests) and use it to test a battery
of classic and modern cross-sectional signals under a common, look-ahead-free protocol.
Rather than mine for alpha, we ask a narrower and more answerable question: **which
signals survive careful measurement, and what does "careful" cost?** We implement the
Asness–Frazzini–Pedersen Quality-Minus-Junk factor (as adapted for India by Jacob,
Pradeep & Varma, 2022), the post-earnings-announcement drift (PEAD), the Amihud (2002)
illiquidity premium, an on-balance-volume/Chaikin-money-flow accumulation screen,
calendar seasonality, co-movement crowding, and a set of price-based microstructure and
event studies. Four results stand out. (i) An **accumulation/CMF** signal is weak at one
month but shows a **monotone +10.8% top-minus-bottom spread at six months**. (ii) **PEAD**
is invisible under a volume-spike event proxy but appears once events are dated with real
SEC EDGAR filing dates (US liquidity-conditioning information coefficient rises **10×,
from +0.010 to +0.102**), and cross-country it concentrates in **less-efficient/emerging
markets** (Brazil +0.24; the US ≈0). (iii) The **liquidity premium** is undetectable in a
single one-year cross-section but is **significant in a ten-year Fama–MacBeth test
(+4.24% per quarter, t = 2.16)**. (iv) Our **quality tilt** loads negatively on value
(−0.88) and positively on momentum (+0.36) against the *real* Kenneth-French factors,
matching the literature. The unifying finding is methodological: in three of four cases
the signal existed but was hidden by measurement — a noisy event proxy, a pooled average
that masked cross-country heterogeneity, or a sample too short for statistical power.
**Measurement quality, not data quantity, was the binding constraint.**

**Keywords:** factor investing, PEAD, liquidity premium, quality (QMJ), reproducible
research, point-in-time backtesting, cross-market equities.

---

## 1. Introduction

Empirical asset pricing is plagued by a replication crisis: many published anomalies fail
out of sample (Hou, Xue & Zhang, 2020), decay after publication (McLean & Pontiff, 2016), or
lose significance under multiple-testing corrections and more careful measurement (Harvey,
Liu & Zhu, 2016). Our contribution is not a new anomaly
but a **reproducible apparatus** — every signal is a small, unit-tested pure function; every
result is regenerable from committed code under continuous integration; and the platform
is governed (TOGAF architecture checks), planned (SAFe backlog), and integrity-verified
(SHA-256 manifest, signed commits). Within that apparatus we re-test well-known signals
across 19 markets and document, honestly, both what works and what the data cannot yet
support.

Three contributions:
1. **An open, tested platform** spanning quality, momentum, value, low-risk, liquidity,
   PEAD, seasonality, crowding, sentiment, options-implied and ESG signals, plus a
   decision layer (constrained portfolios, risk, ensemble conviction) and a versioned
   write surface.
2. **Cross-market evidence** under one protocol, including a per-country decomposition of
   PEAD's liquidity conditioning and a multi-year test of the liquidity premium.
3. **A methodological thesis** — that measurement quality dominates data quantity —
   supported by three cases where a careful re-measurement flipped a null into a
   theory-consistent result.

---

## 2. Data

| Source | Content | Coverage | License |
|---|---|---|---|
| Yahoo Finance (`yfinance`) | Daily OHLCV | 19 markets (US, CA, UK, DE, EU, FI, DK, SE, CH, JP, AU, HK, SG, BR, CN, TW, KR, SA, ZA); ~1 trading year cached + a 10-year US fetch | Public |
| SEC EDGAR | XBRL fundamentals (filed-date) + 10-Q/10-K **filing dates** + 8-K full-text | US | Public |
| Fundamentals snapshot | ROE/ROA/D-E/growth/margin/yield/mktcap | ~731 firms, ~40/market (current snapshot) | via yfinance |
| Kenneth French Data Library | Real FF5 + Momentum + RF factor returns | US/Developed/regional, 1963–2026 | Public |
| OpenAlex / Crossref / arXiv | Scholarly metadata (literature scout) | Global | Public |

**Coverage caveats (material to the results).** (a) The cached price history is ~1 trading
year, which limits long-horizon and monthly tests; the liquidity-premium test therefore
uses a dedicated 10-year fetch. (b) Seven markets (CH, CN, DK, HK, JP, KR, TW) carry
<250 trading days in the cache, so liquidity-gated screeners return no universe for them.
(c) Fundamentals are a **current snapshot**, not a filed-date panel outside the US, so
quality-based results outside the US are contemporaneous, not point-in-time. (d) India —
the market of the source quality paper — is not in the cache (its data lives in a separate
repository); the QMJ method is therefore generalised to the markets present.

---

## 3. Platform and methodology

### 3.1 Architecture
Shared data-plumbing and cross-sectional statistics live in one library
(`marketdata.py`: market enumeration, wide-frame loaders, a liquidity filter, ticker
normalisation, z-scores, information coefficient, monotonicity). Each factor is a module
of small pure functions plus a CLI; the domain logic is separated from the plumbing (see
`GLOSSARY.md`). Reproducibility is enforced by 102 unit tests, an architecture-governance
check (10/10 principles), and a file-integrity manifest, all run in CI on every push.

### 3.2 Signals implemented
- **Quality (QMJ)** — average of four standardised-rank dimensions (profitability,
  growth, safety, payout); deciles; long-only (LQ) and 2×3 size×quality long-short (QMJ);
  a `log(M/B) ~ quality` price-premium regression.
- **PEAD** — market-adjusted cumulative abnormal return (CAR) after an earnings event;
  events dated by a volume-spike proxy *and*, for the US, by real EDGAR 10-Q/10-K filing
  dates.
- **Liquidity** — Amihud (2002) `ILLIQ = mean(|r|/dollar-vol)`; a 0–100 capacity score;
  and a multi-period Fama–MacBeth premium test.
- **Accumulation/CMF** — OBV, Chaikin A/D, Chaikin Money Flow, up/down-volume, combined
  into a composite; validated against forward returns.
- **Microstructure (Tier-1)** — Corwin–Schultz high-low spread, Kaufman efficiency ratio,
  Ornstein–Uhlenbeck half-life, autocorrelations; four HFT-archetype screens; a
  Darvas-box volume-acquisition monitor.
- **Others** — calendar seasonality, co-movement crowding, peer-network lead-lag,
  Loughran–McDonald news sentiment, option-implied IV/skew, ESG risk.

### 3.3 Validation protocol
All tests are **point-in-time**: features are measured over a window ending at date *T*,
returns are realised strictly after *T*. We report, per signal: quantile forward returns,
the information coefficient (IC = corr(signal, forward return)), and rank monotonicity;
for the liquidity premium we use a **Fama–MacBeth** procedure (sort each period, average
quintile returns across periods, t-test the spread). Quality loadings are estimated by
calendar-time regression on the **real** Kenneth-French factors. Returns are gross;
transaction costs are modelled separately (`apply_costs.py`).

---

## 4. Results

### 4.1 Quality (QMJ)
Against real Kenneth-French Carhart factors, the long-only quality portfolio loads
**HML −0.88 (t = −4.3)** and **Mom +0.36 (t = 2.8)** — i.e. quality is *not cheap* and *has
momentum*, matching Asness et al. (2019) and Jacob et al. (2022). The cross-sectional
price premium (`log(M/B) ~ quality + size`, market fixed effects) is **positive and
profitability-driven** (quality coefficient +0.058; profitability the dominant dimension,
corr +0.48 with log M/B), though weaker than the paper's 26-year panel because our
fundamentals are a single snapshot.

### 4.2 Accumulation / Chaikin Money Flow
| Horizon | Signal | Q5−Q1 median forward | monotonicity | IC |
|---|---|---|---|---|
| 1-month | CMF | +1.25% | +0.70 | +0.018 |
| 6-month | CMF | +7.80% | +0.90 | +0.059 |
| 6-month | **accumulation** | **+10.82%** | **+1.00** | +0.071 |

Accumulation is a **multi-month** signal: near-zero at one month, monotone and economically
large at six. This is the strongest tradeable result in the platform.

### 4.3 Post-earnings-announcement drift (PEAD)
Under a **volume-spike event proxy**, the US liquidity-conditioning IC is ≈0 (+0.010). With
**real SEC EDGAR 10-Q/10-K filing dates** (313 events) it rises to **+0.102 — a 10×
improvement** — because real dates strip the proxy's noise (M&A, index changes, macro).
Cross-country, the Chordia–Sadka prediction (PEAD stronger where liquidity is thin) holds
in **8 of 12 markets**, strongest in **Brazil (+0.24)** and ≈0 in the efficient **US (+0.01)**.
Announcement-day volume surges to ~3.8× the pre-event average under the proxy and ~1.6× on
the true filing date.

### 4.4 Liquidity premium — the power of a longer sample
A single one-year cross-section gives a weak, non-monotone premium (Q5−Q1 ≈ +2%). A
dedicated **10-year, Fama–MacBeth** test (199 US names, 35 quarterly cross-sections, 5,723
stock-observations) is decisive:

| 63-day forward | Q1 liquid | Q2 | Q3 | Q4 | Q5 illiquid |
|---|---|---|---|---|---|
| mean return | +4.16% | +2.62% | +4.54% | +3.32% | **+8.40%** |

**Q5−Q1 = +4.24% per quarter, t = 2.16 (|t|>2), avg IC +0.055** — a statistically
significant liquidity premium (Amihud–Mendelson 1986; Amihud 2002). The one-year result
was under-powered, not wrong.

### 4.5 Seasonality, microstructure, and other signals
The **turn-of-the-month** effect is present (US edge +0.22%); day-of-week is estimable
from one year, but monthly/Halloween effects require multi-year data and are withheld. The
microstructure Tier-1 screens produce economically sensible universes (market-making →
tight, low-toxicity ETFs/closed-end funds; latency → high-efficiency-ratio trend names).
Options-implied and ESG modules are implemented but constrained by Yahoo endpoint quality.

### 4.6 Screener-wise cross-market snapshot (illustrative, current)
Fundamental-quality "Strong Performer" (GGG) counts are highest in **Singapore (20),
Brazil (19), South Africa (16), UK (15)** and lowest in the **US (3)** — the same
emerging-market tilt PEAD exhibits. The US dominates technical activity (2,833 Darvas
coils vs <800 elsewhere), reflecting market depth. (Seven short-history markets return no
technical universe.)

---

## 5. Assumptions and limitations

1. **Snapshot fundamentals** outside the US → quality/valuation results are contemporaneous,
   not point-in-time; magnitudes are indicative.
2. **Event proxies** — where real dates are unavailable, earnings events are proxied by
   volume/return spikes, which conflate earnings with other news; §4.3 shows this matters.
3. **Short cache** (~1 trading year) → long-horizon, monthly, and single-market PEAD tests
   are under-powered; the liquidity test therefore uses a separate 10-year fetch.
4. **Survivorship** — universes are *current* liquid names; the 10-year liquidity test
   inherits mild survivorship bias (a point-in-time constituent list would remove it).
5. **Gross returns** — reported spreads are pre-cost; the strongest signals survive modest
   costs, marginal ones do not (`apply_costs.py`).
6. **Data-provider quality** — yfinance options/sustainability endpoints are inconsistent;
   affected modules are validated on their pure logic, not live data.
7. **Not investment advice** — all outputs are research signals.

---

## 6. Reproducibility and governance

Every figure regenerates from committed code. The platform enforces: **102 unit tests**
over deterministic cores; **architecture governance** (`togaf.py govern`, 10/10 principles)
failing CI on regression; a **SHA-256 integrity manifest** and **SSH-signed commits**; a
**SAFe backlog** (77 features) mapping work to strategic themes; and a **literature scout**
that keeps the implemented-vs-frontier map current (currently zero open gaps). Large data
is content-addressed via Git LFS; a versioned CRUD store provides an auditable write
surface. This machinery is the paper's real subject: it is what lets the empirical claims
be checked rather than trusted.

---

## 7. Conclusion

Across quality, drift, and liquidity, the recurring lesson is that **the binding constraint
was measurement, not the signal**. PEAD emerged only with real filing dates; its
cross-country structure emerged only when we stopped pooling; the liquidity premium emerged
only with a decade of data. Each careful re-measurement turned an inconclusive or null
result into a significant, theory-consistent one — the inverse of the replication crisis,
where careless measurement turns noise into "anomalies." A reproducible, tested, governed
platform is not bureaucratic overhead; it is the precondition for trusting any of these
conclusions.

---

## References (methods implemented)

- Amihud, Y. (2002). *Illiquidity and stock returns.* J. Financial Markets.
- Amihud, Y., & Mendelson, H. (1986). *Asset pricing and the bid-ask spread.* JFE.
- Asness, C., Frazzini, A., & Pedersen, L. (2019). *Quality minus junk.* Review of Accounting Studies.
- Bernard, V., & Thomas, J. (1989/1990). *Post-earnings-announcement drift.*
- Carhart, M. (1997). *On persistence in mutual fund performance.* J. Finance.
- Chordia, T., Goyal, A., Sadka, G., Sadka, R., & Shivakumar, L. (2009). *Liquidity and PEAD.*
- Cochrane, J. (2011). *Presidential address: discount rates* (the "factor zoo"). J. Finance.
- Cohen, L., & Frazzini, A. (2008). *Economic links and predictable returns.* J. Finance.
- Corwin, S., & Schultz, P. (2012). *A high-low spread estimator.* J. Finance.
- Fama, E., & French, K. (1992, 1993, 2015). *Cross-section; three- and five-factor models.*
- Fama, E., & MacBeth, J. (1973). *Risk, return, and equilibrium.* JPE.
- Frazzini, A., & Pedersen, L. (2014). *Betting against beta.* JFE.
- Granville, J. (1963). *New Key to Stock Market Profits* (on-balance volume).
- Gu, S., Kelly, B., & Xiu, D. (2020). *Empirical asset pricing via machine learning.* RFS.
- Harvey, C., Liu, Y., & Zhu, H. (2016). *…and the cross-section of expected returns.* RFS.
- Hou, K., Xue, C., & Zhang, L. (2020). *Replicating anomalies.* RFS.
- Jacob, J., Pradeep, K.P., & Varma, J. (2022). *Performance of quality factor in Indian equity market.* IIMA W.P. 2022-11-01.
- Jegadeesh, N., & Titman, S. (1993). *Returns to buying winners and selling losers.* J. Finance.
- Loughran, T., & McDonald, B. (2011). *When is a liability not a liability? Textual analysis…* J. Finance.
- Markowitz, H. (1952). *Portfolio selection.* J. Finance.
- McLean, R. D., & Pontiff, J. (2016). *Does academic research destroy stock return predictability?* J. Finance.
- Novy-Marx, R. (2013). *The other side of value: the gross profitability premium.* JFE.
- Sharpe, W. (1964). *Capital asset prices.* J. Finance.

## Applied and practitioner literature consulted (local corpus)

Applied backtesting and machine-learning studies reviewed while building the platform.
These informed the emphasis on out-of-sample discipline, measurement care, and the
factor-plus-ML framing; they are not the source of any specific reported statistic.

- Preet, S., Gulati, A., Gupta, A., & Aggarwal, A. *Backtesting the Magic Formula on Indian stock markets: an analysis of the Magic Formula strategy.* Dept. of Commerce, SGGSCC, University of Delhi (SSRN 3945468).
- Dhanus, S., & Amutha, G. (2025). *Back-testing Super Trend in the 15-minute time frame among the top 5 contributors of Nifty 50 stocks.* IJARCMSS 8(2-II), 10–14.
- *Backtesting Brilliance: leveraging analytics for comparing buy-&-hold vs. active strategies.* Journal of Informatics Education and Research 4(3), 2024.
- Kargarzadeh, A. *Developing and backtesting a trading strategy using large language models, macroeconomic and technical indicators.* Dept. of Mathematics, Imperial College London.
- Liu, B., & Zhu, H. (2024). *Analysis of market efficiency in main stock markets using the Kalman filter as an approach.* Stern School of Business, New York University (arXiv 2404.16449).
- Palomar, D. P. *Backtesting portfolios* (MAFS5310, Portfolio Optimization with R). HKUST.
- Schumann, E. (2018). *Backtesting* (SSRN 3374195).
- *Comprehensive analysis of machine and deep learning models* (for stock forecasting). IJACSA 16(8), 2025.
- Toichatturat, N. (2025). *Stock-market forecasting with a deep-learning approach: generative adversarial networks (GANs).* SET Research Scholarship Paper 2024/2025, Thammasat University.
- Miao, Y. *A deep-learning approach for stock-market prediction* (CS230). Stanford University.
- Fister, D., Mun, J. C., Jagrič, V., & Jagrič, T. (2019). *Deep learning for stock-market trading: a superior trading strategy?* Neural Network World 29(1), 011.
- *Machine learning and deep learning approaches for stock-market prediction: a comprehensive study.* IJIRTM 9(3), 2025.
- *Deep learning in the stock market — a systematic survey.* Artificial Intelligence Review, 2022 (s10462-022-10226-0).

*Code, tests, and full documentation: the Global Market Scanners repository. Not investment advice.*
