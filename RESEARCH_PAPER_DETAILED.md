# A Reproducible Multi-Market Equity-Factor Platform: Construction, Cross-Sectional Evidence, and the Primacy of Measurement

## A Detailed, Peer-Review-Oriented Manuscript

**Umashankar Triplicane Dwarakanathan** · Independent Researcher

**Working paper — Global Market Scanners project**
*Version 2.0 (extended) · generated 2026-07-03 · commit-signed, CI-verified*

> This is the long-form companion to the v1.0 working paper. It is written to be read on its
> own by a referee with no access to the source code: every construction step, data source,
> statistical estimator, and inference is stated explicitly, and all acronyms and specialised
> terms are defined in the Nomenclature (§0) and Glossary (Appendix C). Where v1.0 summarised
> a result in a sentence, this version gives the estimator, the sample, the assumptions, the
> number, and the interpretation.

---

## Structured Abstract

**Purpose.** To determine which classic and modern cross-sectional equity signals survive
careful, look-ahead-free measurement across many markets, and to quantify what "careful"
costs in data and method — as opposed to searching for a new anomaly.

**Design/methodology/approach.** We construct a reproducible, unit-tested research platform
(~40 Python modules, 19 markets, 102 continuous-integration-gated tests) and re-implement
seven families of signals — quality (Quality-Minus-Junk), post-earnings-announcement drift,
the Amihud illiquidity premium, on-balance-volume/Chaikin-money-flow accumulation, calendar
seasonality, co-movement crowding, and price-based microstructure — under one point-in-time
protocol. Each signal is evaluated by quantile-sorted forward returns, the information
coefficient, rank monotonicity, and, for the liquidity premium, a ten-year Fama–MacBeth
cross-sectional regression. Quality factor loadings are estimated against the *real* Kenneth
R. French research factors.

**Findings.** Four results stand out. (i) An accumulation/Chaikin-money-flow signal is weak
at a one-month horizon but delivers a **monotone +10.8% top-minus-bottom spread at six
months**. (ii) Post-earnings-announcement drift is invisible under a volume-spike event
proxy but appears once events are dated with **real U.S. Securities and Exchange Commission
EDGAR filing dates** — the information coefficient rises **ten-fold, from +0.010 to +0.102** —
and cross-country it concentrates in less-efficient/emerging markets (Brazil +0.24; United
States ≈ 0). (iii) The liquidity premium is undetectable in a single one-year cross-section
but is **statistically significant in a ten-year Fama–MacBeth test (+4.24% per quarter,
t = 2.16)**. (iv) The quality tilt loads negatively on value (−0.88) and positively on
momentum (+0.36) against real factors, reproducing the published signature of the factor.

**Practical/research implications.** In three of four cases the signal existed but was
hidden by a measurement defect — a noisy event proxy, a pooled average that masked
cross-country heterogeneity, or a sample too short for statistical power. **Measurement
quality, not data quantity, was the binding constraint.** The reproducibility apparatus is
therefore not overhead; it is the precondition for trusting the empirical claims.

**Originality/value.** The paper contributes an open, tested, multi-market apparatus; a
per-country decomposition of the liquidity-conditioning of drift; a multi-year re-test of
the liquidity premium; and a documented methodology in which honest re-measurement converts
nulls into theory-consistent results — the inverse of the replication crisis.

**Keywords.** factor investing; post-earnings-announcement drift; liquidity premium; quality
(QMJ); reproducible research; point-in-time backtesting; cross-market equities.

**JEL classification.** G11 (Portfolio Choice; Investment Decisions); G12 (Asset Pricing);
G14 (Information and Market Efficiency); C58 (Financial Econometrics).

---

## 0. Nomenclature

### 0.1 Acronyms and abbreviations

| Acronym | Expansion | First relevant use |
|---|---|---|
| A/D | Accumulation/Distribution line (Chaikin) | §3.7 |
| API | Application Programming Interface | §2.1 |
| AR | Abnormal Return (return minus benchmark) | §3.5 |
| CAPM | Capital Asset Pricing Model | §3.3 |
| CAR | Cumulative Abnormal Return | §3.5 |
| CEF | Closed-End Fund | §4.6 |
| CI *(context: engineering)* | Continuous Integration | §3.1 |
| CLI | Command-Line Interface | §3.1 |
| CMF | Chaikin Money Flow | §3.6 |
| CRUD | Create, Read, Update, Delete (data operations) | §6 |
| D/E | Debt-to-Equity ratio | §3.3 |
| EDGAR | Electronic Data Gathering, Analysis, and Retrieval (SEC filing system) | §2.1 |
| ER | (Kaufman) Efficiency Ratio | §3.7 |
| ESG | Environmental, Social, and Governance | §3.7 |
| ETF | Exchange-Traded Fund | §4.6 |
| FCF | Free Cash Flow | §3.3 |
| FF3 / FF5 | Fama–French 3-factor / 5-factor model | §3.3 |
| FM | Fama–MacBeth (two-pass cross-sectional regression) | §3.2 |
| FX | Foreign Exchange (currency) | §2.3 |
| GGG | Triple-"Good" fundamental Strong-Performer flag (internal label) | §4.6 |
| HFT | High-Frequency Trading | §3.7 |
| HML | High-Minus-Low (value factor: high book-to-market minus low) | §3.3 |
| IC *(context: statistics)* | Information Coefficient (cross-sectional rank correlation of signal with forward return) | §3.2 |
| IIMA | Indian Institute of Management Ahmedabad | References |
| ILLIQ | Amihud (2002) illiquidity measure | §3.5 |
| IV | Implied Volatility | §3.7 |
| JEL | Journal of Economic Literature (classification codes) | §0 |
| LFS | (Git) Large File Storage | §6 |
| LQ | Long-only Quality portfolio | §3.3 |
| M/B | Market-to-Book ratio (a.k.a. price-to-book) | §3.3 |
| Mom / UMD / WML | Momentum factor (Up-Minus-Down / Winners-Minus-Losers) | §3.3 |
| OBV | On-Balance Volume | §3.6 |
| OHLC(V) | Open, High, Low, Close (, Volume) daily bar | §2.1 |
| OLS | Ordinary Least Squares | §3.2 |
| OU | Ornstein–Uhlenbeck (mean-reverting) process | §3.7 |
| PEAD | Post-Earnings-Announcement Drift | §3.5 |
| PIT | Point-In-Time (no look-ahead) | §3.1 |
| QMJ | Quality-Minus-Junk factor (Asness–Frazzini–Pedersen) | §3.3 |
| RF | Risk-Free rate | §3.3 |
| ROA / ROE | Return on Assets / Return on Equity | §3.3 |
| SAFe | Scaled Agile Framework | §6 |
| SEC | (U.S.) Securities and Exchange Commission | §2.1 |
| SHA-256 | Secure Hash Algorithm, 256-bit | §6 |
| SMB | Small-Minus-Big (size factor) | §3.3 |
| SUE | Standardised Unexpected Earnings | §3.5 |
| TOGAF | The Open Group Architecture Framework | §6 |
| USD | United States Dollar (numéraire) | §2.3 |
| XBRL | eXtensible Business Reporting Language | §2.1 |
| 8-K / 10-Q / 10-K | SEC current-report / quarterly / annual filing forms | §2.1 |

> **Two collisions worth flagging for the reader.** "IC" denotes the *information coefficient*
> (a statistic) everywhere except where we explicitly say "CI / continuous integration"
> (an engineering practice). "CI" in this paper always means *continuous integration*, never
> "confidence interval"; interval estimates are reported as t-statistics.

### 0.2 Principal mathematical symbols

| Symbol | Meaning |
|---|---|
| $r_{i,t}$ | (simple) return of asset $i$ on day $t$ |
| $R_{i,t\to t+h}$ | forward return of asset $i$ from $t$ to $t+h$ |
| $V_{i,t}$ | share volume of asset $i$ on day $t$ |
| $P_{i,t}$ | close price of asset $i$ on day $t$ |
| $h$ | forward-return horizon (trading days) |
| $T$ | the "as-of" measurement date (features use data $\le T$) |
| $\rho$ | Spearman rank correlation (used for the IC and monotonicity) |
| $\bar{x}, \sigma_x$ | cross-sectional mean and standard deviation of $x$ |
| $z(x)$ | cross-sectional z-score, $z(x)=(x-\bar{x})/\sigma_x$ |
| $Q_k$ | the $k$-th quantile bucket (e.g. $Q_5$ = most extreme) |
| $\lambda_t$ | Fama–MacBeth per-period slope (period-$t$ premium) |
| $t$-stat | $\bar{\lambda}/(\text{s.e.}(\lambda)/\sqrt{n})$ |

---

## 1. Introduction

### 1.1 Motivation: the replication crisis in empirical asset pricing

Modern empirical asset pricing has catalogued hundreds of "anomalies" — cross-sectional
patterns in average returns that a risk model does not explain. A large fraction of these
fail to replicate out-of-sample, shrink drastically after publication, or disappear once
transaction costs, look-ahead bias, and multiple-testing corrections are imposed. The
literature's own diagnosis is that much of the "factor zoo" is an artefact of *measurement
freedom*: enough researcher degrees of freedom (universe choice, winsorisation, event
dating, horizon selection) will manufacture significance from noise.

This paper inverts the usual objective. We do **not** search for a new anomaly. We ask a
narrower, more answerable question:

> **Research question.** Under a single, pre-committed, look-ahead-free protocol, which
> well-known cross-sectional signals survive careful measurement across many markets — and
> when a signal appears absent, is that because the signal is truly absent, or because the
> *measurement* is defective?

The distinction matters because the two failure modes have opposite remedies. If a signal is
truly absent, more data will not help. If it is present but mis-measured, then better event
dating, a longer sample, or a finer cross-sectional cut will recover it. Our central empirical
claim is that, for the signals studied here, the second failure mode dominates.

### 1.2 Hypotheses

We evaluate four pre-stated hypotheses, each tied to an established prediction in the
literature:

- **H1 (Quality).** A portfolio long high-quality and short low-quality firms carries the
  Asness–Frazzini–Pedersen "signature": a *negative* loading on the value factor (quality is
  expensive, not cheap) and a *positive* loading on momentum.
- **H2 (Accumulation).** A volume-based accumulation signal (on-balance volume / Chaikin
  money flow) predicts multi-month forward returns monotonically, even if it is weak at a
  one-month horizon.
- **H3 (Drift & liquidity conditioning).** Post-earnings-announcement drift exists and is
  *stronger in less liquid / less efficient markets* (the Chordia–Sadka prediction); and its
  measured strength is sensitive to how precisely the earnings event is dated.
- **H4 (Liquidity premium).** Illiquid stocks earn a positive forward-return premium
  (Amihud–Mendelson), detectable given a sufficiently long sample even if a single short
  cross-section is under-powered.

### 1.3 Contributions

1. **An open, tested platform.** Every signal is a small pure function with unit tests; every
   reported number regenerates from committed code under continuous integration; the
   architecture is governed and the file set is integrity-verified (§6).
2. **Cross-market evidence under one protocol,** including a per-country decomposition of the
   liquidity-conditioning of drift and a ten-year Fama–MacBeth re-test of the liquidity
   premium.
3. **A documented methodological thesis** — measurement quality dominates data quantity —
   supported by three cases (§5) in which a single, disciplined re-measurement converted a
   null into a theory-consistent, statistically significant result.

### 1.4 How to read this paper

§2 lists every data source and how the analysable universe is built from it. §3 is the
methodological core: §3.1–3.2 state the shared protocol and estimators; §3.3–3.7 give the
step-by-step construction of each signal with equations. §4 reports results, each followed by
an explicit **Inference** paragraph. §5 draws out the measurement thesis. §6 covers threats to
validity and §7 reproducibility. Appendix A is a numbered reproduction protocol; Appendix B
lists computational dependencies; Appendix C is the glossary.

---

## 2. Data

The platform uses only **public, free** data sources. No paid vendor feed, no proprietary
tick data, and no personally identifiable information enters the analysis. This section is
deliberately exhaustive because, as §4–§5 show, the results turn on data provenance.

### 2.1 Sources

| # | Source | Access mechanism | Content | Coverage | License / terms |
|---|---|---|---|---|---|
| D1 | **Yahoo Finance** | `yfinance` Python client, routed through a governed rate-limited API wrapper | Daily **OHLCV** bars; snapshot fundamentals (ROE, ROA, D/E, revenue growth, margins, dividend yield, market capitalisation) | 19 markets; ~1 trading year cached locally, plus a dedicated **10-year** United States fetch for the liquidity test | Public; personal/research use |
| D2 | **SEC EDGAR** | HTTPS to `data.sec.gov` (XBRL company-facts, submissions) and `efts.sec.gov` (full-text search), with an e-mail-format `User-Agent` header as SEC requires | **XBRL** fundamentals tagged with *filed* dates; **10-Q**/**10-K** filing dates; **8-K** current reports (full-text) | United States | Public domain (U.S. Government) |
| D3 | **Fundamentals snapshot** | derived from D1 | ROE / ROA / D-E / growth / margin / yield / market-cap per firm | ~731 firms, ≈ 40 per market, *current* values | via D1 |
| D4 | **Kenneth R. French Data Library** | HTTPS download of the published factor ZIP archives | *Real* daily FF5 factors (market, SMB, HML, RMW, CMA), the momentum factor, and the risk-free rate | United States / developed / regional, 1963–2026 | Public, academic |
| D5 | **OpenAlex / Crossref / arXiv** | public REST APIs | Scholarly metadata for the literature scout (implemented-vs-frontier map) | Global | Public / open |

**Why an SEC-compliant `User-Agent` matters (D2).** EDGAR returns HTTP 403 to clients that do
not present a contactable e-mail-format identifier. The platform sets one via an environment
variable, never a hard-coded secret. This is the mechanical reason real filing dates were
retrievable at all — and, as §4.3 shows, retrieving them is the single most consequential
data decision in the paper.

### 2.2 Universe construction (step by step)

For each market the analysable universe is built deterministically:

1. **Enumerate candidates.** Read the market's symbol seed list (index constituents plus a
   curated liquid set), applying the correct exchange suffix per market (e.g. `.NS` for
   India-style suffixed tickers, `.T` for Tokyo, `.SA` for São Paulo).
2. **Load a wide price frame.** Pivot the long OHLCV table into a date × ticker matrix of
   closes (and a parallel matrix of volumes). Missing cells are left as `NaN`, never
   forward-filled across the event windows used for returns.
3. **Apply a liquidity filter.** Keep only names with a sufficient count of valid trading
   days and non-trivial dollar volume. This removes stale/half-listed tickers that would
   otherwise dominate an illiquidity sort for the wrong reason.
4. **Freeze the as-of date $T$.** All features are computed on data with timestamp $\le T$;
   all returns are realised strictly after $T$ (§3.1). This freeze is what makes the test
   point-in-time.

### 2.3 Currency and numéraire

Returns are computed in each asset's **local currency** and are *ratio* quantities
(dimensionless), so cross-market comparison of *returns* is currency-neutral. Where absolute
sizes are compared (e.g. market capitalisation for the size split), values are normalised to
**USD** via a foreign-exchange (FX) layer. No currency *carry* is introduced because we never
convert a return stream through a second currency.

### 2.4 Coverage caveats (material to the results)

These caveats are not boilerplate; each one directly shapes a result or a decision:

- **(C1) Short cache.** The default price history is ~1 trading year. This is adequate for
  daily-horizon cross-sectional sorts but **under-powers** long-horizon, monthly, and
  single-market tests. Consequence: the liquidity-premium test (§3.5, §4.4) uses a separate
  **10-year** fetch rather than the cache.
- **(C2) Short-history markets.** Seven markets — Switzerland, China, Denmark, Hong Kong,
  Japan, South Korea, Taiwan (CH, CN, DK, HK, JP, KR, TW) — carry < 250 trading days in the
  cache, so liquidity-gated screeners correctly return *no* universe for them rather than a
  spurious one.
- **(C3) Snapshot fundamentals.** Outside the United States, fundamentals are a *current
  snapshot*, not a filed-date panel. Quality/valuation results outside the U.S. are therefore
  **contemporaneous, not point-in-time**, and their magnitudes are indicative.
- **(C4) India absent from the cache.** India — the market of the source quality paper
  (Jacob, Pradeep & Varma, 2022) — is not in this cache (its deep history lives in a separate
  repository). The QMJ method is therefore *generalised* to the markets present rather than
  reproduced on its original market.
- **(C5) Provider endpoint quality.** Yahoo's options and sustainability (ESG) endpoints are
  inconsistent; modules that depend on them are validated on their deterministic logic, not on
  live values.

---

## 3. Platform and methodology

### 3.1 Design principles and the point-in-time protocol

Three principles govern every test:

- **Purity and testability.** Each signal is a *pure function* — deterministic output for a
  given input, no hidden state — so it can be unit-tested in isolation. Data plumbing
  (loading, pivoting, filtering) lives in one shared library (`marketdata.py`); domain logic
  lives in per-signal modules. This separation is what lets 102 tests cover the deterministic
  cores without a network or database.
- **Point-in-time (PIT) evaluation.** For an as-of date $T$: features use only data with
  timestamp $\le T$; the realised return window opens *after* $T$. No feature is ever computed
  with information that a trader at $T$ could not have had. This is the single most important
  guard against look-ahead bias.
- **Reproducibility as a gate, not a courtesy.** The unit tests, an architecture-governance
  check, and a file-integrity manifest run in continuous integration (CI) on every push
  (§6). A regression, a broken architecture principle, or a tampered file **fails the build**.

### 3.2 Shared cross-sectional estimators

All signals are scored with the same four instruments so that results are comparable across
families.

**(a) Cross-sectional z-score.** For a raw signal $x_i$ measured across the universe at $T$,
$$ z(x_i) = \frac{x_i - \bar{x}}{\sigma_x}, $$
with $\bar{x}$ and $\sigma_x$ the cross-sectional mean and standard deviation. Infinities
(from ratios with zero denominators) are mapped to `NaN` and excluded, never clipped to a
finite extreme that would distort the tails.

**(b) Quantile-sorted forward returns.** Rank the universe on the signal at $T$ into $k$
buckets $Q_1,\dots,Q_k$ (quintiles $k=5$ or deciles $k=10$). For each bucket compute the
median (robust) forward return $R_{t\to t+h}$. The headline number is the **top-minus-bottom
spread** $Q_k - Q_1$.

**(c) Information coefficient (IC).** The cross-sectional Spearman rank correlation between the
signal at $T$ and the subsequent forward return,
$$ \mathrm{IC} = \rho\big(\text{signal}_T,\ R_{T\to T+h}\big). $$
The IC measures *monotone predictive content*; $|\mathrm{IC}| \gtrsim 0.05$ is economically
meaningful in a single-period cross-section, and IC is additive in information across periods.

**(d) Rank monotonicity.** The Spearman correlation between bucket index $(1,\dots,k)$ and the
buckets' mean forward returns. A value of $+1.00$ means returns rise strictly with the signal
across every bucket — the property a *tradeable* factor needs, because it survives coarse
bucketing and is not driven by one extreme tail.

**(e) Fama–MacBeth (FM) two-pass regression** (used for the liquidity premium, §3.5). For each
period $t$, run a cross-sectional regression of forward return on the signal to obtain a slope
$\lambda_t$ (the period-$t$ premium). Then average across the $n$ periods and test:
$$ \bar{\lambda} = \frac{1}{n}\sum_{t} \lambda_t, \qquad
   t\text{-stat} = \frac{\bar{\lambda}}{\mathrm{s.e.}(\lambda_t)/\sqrt{n}}. $$
FM is the standard remedy for cross-sectional correlation: it treats each period's premium as
one observation, so the effective sample is the number of *periods*, not the number of
stock-days. This is precisely why a longer sample (more periods) buys statistical power (§4.4).

### 3.3 Quality (Quality-Minus-Junk), step by step

We implement the Asness–Frazzini–Pedersen (2019) quality factor, in the form adapted for the
Indian market by Jacob, Pradeep & Varma (2022), and generalise it to the markets present.

1. **Four quality dimensions.** For each firm compute proxies for
   (i) **profitability** (e.g. ROE, ROA, margins),
   (ii) **growth** (trailing changes in profitability/earnings),
   (iii) **safety** (low leverage: inverse D/E; low volatility),
   (iv) **payout** (shareholder yield: dividends and net issuance).
2. **Standardise within market.** Convert each raw proxy to a cross-sectional *rank*, then to
   a z-score (§3.2a) *within each market*, so that a Brazilian firm is compared to Brazilian
   peers, not to a U.S. mega-cap.
3. **Composite quality score.** Average the four standardised dimensions into a single
   `quality` score; rank into deciles.
4. **Two portfolios.**
   - **LQ (long-only quality):** hold the top-quality decile.
   - **QMJ (long-short):** a 2×3 double sort on size × quality (as in the source papers), long
     high-quality and short low-quality within size groups, so the factor is approximately
     size-neutral.
5. **Factor-signature test (H1).** Regress the portfolio's excess return on the *real* Kenneth
   French Carhart factors (market, SMB, HML, momentum) by calendar-time OLS:
   $$ R^{\text{port}}_t - RF_t = \alpha + \beta_{\text{mkt}}\,\text{MKT}_t + \beta_{\text{SMB}}\,\text{SMB}_t + \beta_{\text{HML}}\,\text{HML}_t + \beta_{\text{Mom}}\,\text{Mom}_t + \varepsilon_t. $$
   The signs and significance of $\beta_{\text{HML}}$ and $\beta_{\text{Mom}}$ test whether our
   quality construct has the published DNA.
6. **Price-premium test.** Cross-sectionally regress firm valuation on quality:
   $$ \log(M/B)_i = a + b\,\text{quality}_i + c\,\log(\text{size})_i + \text{market fixed effects} + u_i, $$
   where a positive $b$ means the market *pays up* for quality. We also report which single
   dimension dominates (correlation of each dimension with $\log(M/B)$).

### 3.4 (reserved)

### 3.5 Post-earnings-announcement drift (PEAD), step by step

PEAD is the tendency of prices to keep drifting in the direction of an earnings surprise for
weeks after the announcement. Our study has two variants that differ *only* in how the event
is dated — which is the whole point.

1. **Define the event.** Two dating schemes:
   - **Proxy dating:** flag a day as an "earnings event" when volume spikes far above its
     trailing average (a common technique when true dates are unavailable).
   - **True dating (U.S. only):** parse the firm's SEC EDGAR **submissions** feed for **10-Q**
     (quarterly) and **10-K** (annual) filing dates — the actual, legally-timestamped events.
2. **Compute the abnormal return (AR).** For each day around the event, subtract a market
   benchmark from the stock return: $ \text{AR}_{i,t} = r_{i,t} - r_{\text{mkt},t} $ (a
   market-adjusted return; a full-factor benchmark is a straightforward extension).
3. **Cumulate into a CAR.** Sum ARs over the post-event drift window to obtain the cumulative
   abnormal return $\text{CAR}_{i} = \sum_{t \in \text{window}} \text{AR}_{i,t}$.
4. **Sign by the surprise.** Approximate the earnings surprise by the announcement-window
   return/volume reaction (a reduced-form proxy for standardised unexpected earnings, SUE,
   which requires an analyst consensus we do not have), and test whether the *sign* of the
   surprise predicts the *sign and size* of the subsequent CAR.
5. **Liquidity-conditioning test (H3).** Compute the IC between an illiquidity measure (§3.5
   below) and the post-event drift, *conditional on* an earnings event. The prediction
   (Chordia et al., 2009) is that drift is larger where liquidity is thinner.
6. **Cross-country decomposition.** Repeat the conditioning test market by market rather than
   pooling, so that a strong effect in one market and a null in another are not averaged into
   a muddy middle.

**Amihud illiquidity, used above and in §3.5-liquidity.** For asset $i$ over a window,
$$ \mathrm{ILLIQ}_i = \Big\langle \frac{|r_{i,t}|}{P_{i,t}\,V_{i,t}} \Big\rangle_t
   \;=\; \text{average daily } \frac{|\text{return}|}{\text{dollar volume}}, $$
the average absolute return produced *per unit of dollar volume* — i.e. price impact. High
ILLIQ = illiquid. We also map ILLIQ to a 0–100 "capacity" score for readability.

### 3.5-liquidity. Liquidity premium via ten-year Fama–MacBeth, step by step

1. **Assemble a panel.** Fetch **10 years** of daily data for a broad U.S. universe (D1,
   dedicated fetch). Split the timeline into non-overlapping quarterly windows.
2. **Per window:** compute each stock's Amihud ILLIQ over a trailing look-back; measure the
   forward 63-trading-day (one-quarter) return; sort names into liquidity **quintiles**
   $Q_1$ (most liquid) … $Q_5$ (most illiquid).
3. **Cross-sectional premium.** For each window, record the quintile mean returns and the
   FM slope $\lambda_t$ from regressing forward return on illiquidity rank.
4. **Aggregate (H4).** Average the quintile returns and the slopes across all windows;
   t-test $\bar\lambda$ (§3.2e). The effective sample size is the number of quarterly
   cross-sections, which is why a decade — not a year — delivers power.

### 3.6 Accumulation / Chaikin Money Flow, step by step

The accumulation family asks whether *volume* reveals informed buying before price fully
reflects it.

1. **On-Balance Volume (OBV):** a running sum that adds the day's volume when the close rises
   and subtracts it when the close falls — a cumulative proxy for net buying pressure.
2. **Accumulation/Distribution (A/D) line (Chaikin):** weights each day's volume by where the
   close sits within the day's high–low range (the "money-flow multiplier"),
   $$ \text{MFM}_t = \frac{(C_t - L_t) - (H_t - C_t)}{H_t - L_t}, \qquad
      \text{A/D}_t = \text{A/D}_{t-1} + \text{MFM}_t\cdot V_t. $$
3. **Chaikin Money Flow (CMF):** the money-flow-volume summed over $N$ days divided by total
   volume over $N$ days — a bounded ($[-1,1]$) oscillator of net accumulation.
4. **Up/down-volume ratio** and a **composite accumulation score** combining the above.
5. **Validation.** Rank the universe on each accumulation signal at $T$ and measure quintile
   forward returns, IC, and monotonicity at **1-month and 6-month** horizons (H2).

### 3.7 Microstructure and other signals (summary)

Implemented and tested, but secondary to H1–H4:

- **Microstructure (Tier-1):** Corwin–Schultz high-low bid-ask-spread estimator; Kaufman
  efficiency ratio (ER = net move ÷ summed absolute moves, a "trendiness" measure);
  Ornstein–Uhlenbeck (OU) mean-reversion half-life; return autocorrelations. These feed four
  **HFT-archetype** screens (market-making, latency/trend, mean-reversion, momentum-ignition
  proxies) and a **Darvas-box** volume-acquisition monitor that flags price consolidation
  ("boxes") accompanied by rising OBV/CMF — the current bar is *excluded* from box formation so
  that a breakout can be detected the day it happens.
- **Seasonality:** turn-of-the-month, day-of-week (estimable from one year); monthly and
  Halloween effects require multi-year data and are *withheld* rather than reported
  under-powered.
- **Crowding / peer-network:** co-movement crowding and peer lead-lag (Cohen–Frazzini
  economic links).
- **News sentiment:** Loughran–McDonald finance-specific lexicon over headlines.
- **Options-implied & ESG:** implied-volatility level/skew and ESG risk — implemented, but see
  caveat C5.

---

## 4. Results

Each result is stated as an estimate with its sample and then interpreted in an explicit
**Inference** paragraph. All spreads are gross of transaction costs unless stated (§6).

### 4.1 Quality (QMJ) — H1

Against the real Kenneth French Carhart factors, the long-only quality portfolio loads:

| Factor | Loading | t-stat | Reading |
|---|---|---|---|
| HML (value) | **−0.88** | −4.3 | quality is *expensive*, not cheap |
| Momentum | **+0.36** | +2.8 | quality *has* momentum |

The cross-sectional price-premium regression gives a **positive** quality coefficient
(+0.058), dominated by the **profitability** dimension (correlation +0.48 with $\log(M/B)$).

**Inference.** The construct reproduces the published Asness–Frazzini–Pedersen signature: a
negative value loading and positive momentum loading. H1 is supported. The price premium is
positive but weaker than the source paper's 26-year panel — expected, because our
fundamentals are a single snapshot (C3), so the valuation regression sees one cross-section
rather than a quarter-century of within-firm variation. The qualitative DNA survives even
where the quantitative magnitude is attenuated by data limits.

### 4.2 Accumulation / Chaikin Money Flow — H2

| Horizon | Signal | $Q_5-Q_1$ median forward | Monotonicity | IC |
|---|---|---|---|---|
| 1-month | CMF | +1.25% | +0.70 | +0.018 |
| 6-month | CMF | +7.80% | +0.90 | +0.059 |
| 6-month | **Accumulation composite** | **+10.82%** | **+1.00** | +0.071 |

**Inference.** Accumulation is a *multi-month* phenomenon. At one month the signal is
economically small (IC +0.018) and non-strictly-monotone (0.70); at six months it is large
(+10.8%) and *perfectly* monotone (1.00) — returns rise across every single quintile. H2 is
supported, with the important qualification that the horizon matters: a one-month evaluation
would have wrongly rejected it. This is the strongest tradeable result in the platform and the
first illustration of the paper's thesis — the signal was always there; only a long-enough
horizon reveals it.

### 4.3 Post-earnings-announcement drift (PEAD) — H3

| Event dating | Sample | U.S. liquidity-conditioning IC |
|---|---|---|
| Volume-spike **proxy** | — | **+0.010** (≈ 0) |
| Real SEC EDGAR **10-Q/10-K** dates | 313 events | **+0.102** (**10× larger**) |

Announcement-day volume rises to ~3.8× the pre-event average under the proxy but only ~1.6×
on the *true* filing date — evidence the proxy is contaminated by non-earnings volume events.
Cross-country, the Chordia–Sadka prediction (drift stronger where liquidity is thin) holds in
**8 of 12 markets**, strongest in **Brazil (+0.24)** and ≈ 0 in the efficient **United States
(+0.01)**.

**Inference.** Two measurement defects were masking a real effect. First, *event dating*: the
volume-spike proxy fires on mergers, index reconstitutions, and macro shocks as well as
earnings, so it dilutes the drift signal; replacing it with legally-timestamped EDGAR dates
raises the information coefficient ten-fold. The ~3.8× vs ~1.6× volume figures make the
mechanism concrete — the proxy is *selecting on* large volume events, which is exactly why it
mixes in non-earnings news. Second, *pooling*: averaging the conditioning IC across all
markets hides that the effect is concentrated where theory says it should be (emerging,
less-liquid Brazil) and genuinely near-zero where it should be (the deeply efficient U.S.).
H3 is supported on both counts, and neither would have been visible without (a) real dates and
(b) a per-country cut.

### 4.4 Liquidity premium — H4, and the power of a longer sample

A single one-year cross-section gives a weak, non-monotone premium ($Q_5-Q_1 \approx +2\%$).
The dedicated **10-year Fama–MacBeth** test (199 U.S. names, 35 quarterly cross-sections,
5,723 stock-observations) is decisive:

| 63-day forward | $Q_1$ liquid | $Q_2$ | $Q_3$ | $Q_4$ | $Q_5$ illiquid |
|---|---|---|---|---|---|
| Mean return | +4.16% | +2.62% | +4.54% | +3.32% | **+8.40%** |

**$Q_5-Q_1 = +4.24\%$ per quarter, $t = 2.16$ ($|t|>2$), average IC +0.055.**

**Inference.** The one-year result was *under-powered, not wrong*. Because Fama–MacBeth's
effective sample is the number of periods, a single year offers only a handful of quarterly
cross-sections — far too few to distinguish a +4% premium from zero. A decade supplies 35
cross-sections and the premium becomes statistically significant, with the sign and rough
magnitude the Amihud–Mendelson theory predicts. H4 is supported. Note the premium is not
perfectly monotone across the middle quintiles ($Q_3>Q_2$, $Q_4<Q_3$) — the signal lives
mostly in the extreme illiquid quintile $Q_5$, consistent with illiquidity being a tail
phenomenon.

### 4.5 Seasonality, microstructure, and other signals

The **turn-of-the-month** effect is present (U.S. edge +0.22%). Day-of-week is estimable from
one year; **monthly and Halloween effects are withheld** because one year cannot power them —
a deliberate refusal to report an under-identified number. The microstructure Tier-1 screens
produce economically sensible universes: the market-making archetype selects tight-spread,
low-toxicity ETFs and closed-end funds; the latency/trend archetype selects
high-efficiency-ratio trending names. Options-implied and ESG modules run but are constrained
by provider endpoint quality (C5).

**Inference.** Where the data can support a seasonal claim, we make it; where it cannot, we
say so and withhold. This asymmetry — reporting the powered results and explicitly withholding
the under-powered ones — is itself part of the measurement discipline the paper argues for.

### 4.6 Screener-wise cross-market snapshot (illustrative, current)

Fundamental-quality "Strong Performer" (GGG) counts are highest in **Singapore (20), Brazil
(19), South Africa (16), United Kingdom (15)** and lowest in the **United States (3)** — the
same emerging-market tilt PEAD exhibits (§4.3). The U.S. dominates *technical* activity (2,833
Darvas coils vs < 800 elsewhere), reflecting market depth. Seven short-history markets (C2)
correctly return no technical universe.

**Inference.** Two independent lenses — fundamental quality counts and drift conditioning —
point the same way: pricing inefficiencies (both cheap quality and exploitable drift) are more
abundant in emerging/less-efficient markets, while the deep, liquid U.S. market offers fewer
fundamental bargains but far more *technical* structure. This is a coherence check, not a
tradeable claim: the counts are a current snapshot (C3).

---

## 5. Discussion: the primacy of measurement

Three of the four headline results share a structure: a first, careless measurement returns a
null or a weak result; a single disciplined change recovers a significant, theory-consistent
effect. We name the three moves:

1. **Date the event correctly.** PEAD ≈ 0 under a volume-spike proxy → IC +0.102 with real SEC
   filing dates (a ten-fold increase). *The lesson:* event studies are only as good as their
   event timestamps.
2. **Stop pooling.** A muddy pooled drift-conditioning IC → a clean per-country pattern
   (emerging strong, U.S. ≈ 0). *The lesson:* averaging across heterogeneous regimes destroys
   the very structure a theory predicts.
3. **Wait for the sample.** A liquidity premium invisible in one year → significant over ten
   ($t=2.16$). *The lesson:* in a Fama–MacBeth world, power comes from periods, and short
   samples fail to reject for lack of data, not lack of effect.

The accumulation result (§4.2) is a fourth instance of the same idea in the *horizon*
dimension: weak at one month, strong and monotone at six.

This is the exact inverse of the replication crisis. There, careless measurement (data
snooping, look-ahead, multiple testing) turns *noise into anomalies*. Here, careful
measurement turns *apparent nulls back into signal*. The two are the same coin: measurement
freedom can manufacture significance or destroy it, and the only defence in either direction
is a pre-committed, reproducible protocol. Hence the paper's title claim — **measurement
quality, not data quantity, was the binding constraint** — and hence the emphasis (§6) on the
apparatus rather than any single number.

---

## 6. Threats to validity and limitations

We enumerate threats in the categories a referee would use.

**Construct validity.**
- **(L1) Snapshot fundamentals outside the U.S. (C3).** Quality/valuation results are
  contemporaneous, not point-in-time; magnitudes are indicative, signs are trustworthy.
- **(L2) Surprise proxy for PEAD.** Lacking analyst consensus, the earnings surprise is a
  reduced-form volume/return proxy rather than a true SUE; §4.3 is a *conditioning* result,
  not a claim about the surprise coefficient itself.

**Internal validity.**
- **(L3) Event-dating contamination.** The volume-spike proxy conflates earnings with other
  news; this is not a nuisance but a *studied variable* (§4.3), and the U.S. result uses real
  dates precisely to remove it.
- **(L4) Survivorship.** Universes are *current* liquid names; the ten-year liquidity test
  inherits mild survivorship bias. A point-in-time constituent list would remove it and, if
  anything, likely *strengthen* an illiquidity premium (delisted names are disproportionately
  illiquid).

**External validity.**
- **(L5) Short cache (C1).** Long-horizon, monthly, and single-market PEAD tests are
  under-powered on the default cache; we either use a dedicated fetch (liquidity) or withhold
  the claim (monthly seasonality).
- **(L6) India absent (C4).** The QMJ result generalises the method to other markets rather
  than reproducing it on its original market.

**Statistical-conclusion validity.**
- **(L7) Multiple testing.** Many signals are examined; we mitigate by (i) pre-stating four
  hypotheses, (ii) reporting monotonicity and IC (not just a single spread), and (iii)
  demanding theory-consistent *signs*, which a data-snooped result would not reliably deliver.
- **(L8) Gross returns.** Reported spreads are pre-cost. A separate cost model
  (`apply_costs.py`) indicates the strongest signals (accumulation, the illiquidity premium's
  extreme quintile) survive modest costs while marginal ones do not — and, by construction, an
  illiquidity premium is partly compensation for the very trading costs that erode it.

**Data validity.**
- **(L9) Provider endpoint quality (C5).** Options/ESG modules validated on logic, not live
  values.

**Ethical / usage.**
- **(L10) Not investment advice.** All outputs are research signals; nothing here is a
  recommendation to transact in any security.

---

## 7. Reproducibility and governance (the paper's real subject)

Every figure in §4 regenerates from committed code. The apparatus enforces, on every push:

- **102 unit tests** over deterministic cores (calendars, the rate limiter, the cost model,
  point-in-time filing-date filtering, feature engineering, the factor OLS, the durability
  score, the decision layer, and each signal core).
- **Architecture governance** — an automated check of 10 architecture principles (TOGAF
  lineage); a violation fails CI.
- **File integrity** — a SHA-256 manifest of tracked files, verified in CI; a tampered file
  fails the build.
- **Signed history** — SSH-signed commits; large data content-addressed via Git LFS; a
  versioned CRUD store provides an auditable write surface.
- **Planning & frontier** — a SAFe backlog (77 features) maps work to strategic themes, and a
  literature scout keeps the implemented-vs-frontier map current (currently zero open gaps).

The claim is deliberately strong: **this machinery is the contribution.** Any competent reader
can re-run the tests, regenerate the tables, and check the numbers rather than trust them —
which is the only durable answer to a replication crisis.

---

## 8. Conclusion and future work

Across quality, drift, and liquidity, the recurring lesson is that the binding constraint was
*measurement*, not the signal. PEAD emerged only with real filing dates; its cross-country
structure emerged only when we stopped pooling; the liquidity premium emerged only with a
decade of data; accumulation emerged only at a multi-month horizon. Each disciplined
re-measurement converted an inconclusive or null result into a significant, theory-consistent
one — the inverse of the replication crisis.

**Future work.** (i) Replace snapshot fundamentals with filed-date panels outside the U.S. to
make quality tests point-in-time everywhere. (ii) Extend real-date event dating beyond the
U.S. (e.g. regulatory filing feeds for Brazil, India). (iii) Add point-in-time index
constituents to eliminate survivorship. (iv) Re-run every spread net of the cost model as the
headline, not a robustness check. (v) Reproduce QMJ on India's deep history to close C4.

---

## Appendix A. Step-by-step reproduction protocol

A referee can reproduce the paper as follows (repository: *Global Market Scanners*):

1. **Environment.** Install the Python dependencies (Appendix B); set `SEC_UA` to a
   contactable e-mail-format identifier for EDGAR; no other secrets are required.
2. **Verify the apparatus.** Run the test suite (`pytest -q` → 102 passed), the architecture
   check (`togaf.py govern` → 10/10), and the integrity check (`verify_integrity.sh`). If any
   fails, stop — the numbers are not trustworthy.
3. **Quality (§4.1).** Run the quality-factor module to produce the LQ/QMJ portfolios and the
   Carhart regression against the downloaded Kenneth French factors.
4. **Accumulation (§4.2).** Run the accumulation screener with 1-month and 6-month validation
   to reproduce the quintile table.
5. **PEAD (§4.3).** Run the earnings-liquidity study twice: once with the volume-spike proxy,
   once with `--edgar` (real 10-Q/10-K dates); compare the U.S. conditioning IC and the
   per-country decomposition.
6. **Liquidity premium (§4.4).** Run the multi-year liquidity module (dedicated 10-year fetch)
   to reproduce the Fama–MacBeth quintile table and t-statistic.
7. **Cross-market snapshot (§4.6).** Run the screener matrix across the 19 markets.

Each step writes derived CSVs that are *gitignored* (rebuilt on demand); only source code and
this manuscript are tracked.

## Appendix B. Computational dependencies

Python 3 with `pandas`, `numpy`, `scipy`/`statsmodels` (OLS, t-tests), `yfinance` (D1),
`requests` (D2, D4, D5), and `pytest` (tests). Document builds use Node.js with `docx`
(Word) and `pptxgenjs` (slides). No paid or proprietary dependency is required.

## Appendix C. Glossary of terms

- **Abnormal return (AR).** A stock's return minus a benchmark (here, the market) over the
  same interval; the part of the move not explained by the benchmark.
- **Accumulation.** Net informed buying inferred from the joint behaviour of price and volume
  (e.g. rising volume on up-days).
- **Amihud illiquidity (ILLIQ).** Average absolute return per unit of dollar volume; a proxy
  for price impact. High = illiquid.
- **Calendar-time regression.** A time-series regression of a portfolio's periodic return on
  factor returns; used to read off factor *loadings* (§3.3 step 5).
- **Capacity score.** A readable 0–100 rescaling of liquidity (high = easy to trade).
- **Chaikin Money Flow (CMF).** A bounded oscillator summarising accumulation over a window,
  built from where each close sits within its day's range, weighted by volume.
- **Cross-section.** The set of all assets observed at one instant; a "cross-sectional" test
  compares assets to each other at a point in time (as opposed to over time).
- **Cumulative abnormal return (CAR).** The sum of abnormal returns over an event window; the
  drift PEAD measures.
- **Efficiency ratio (ER).** Net directional move divided by the sum of absolute daily moves;
  near 1 = smooth trend, near 0 = choppy.
- **Event study.** A method that aligns many assets on a common event date and measures average
  behaviour in the surrounding window.
- **Fama–MacBeth (FM) regression.** A two-pass procedure: estimate a cross-sectional slope each
  period, then average and t-test the slopes; robust to cross-sectional correlation.
- **Factor loading (beta).** The sensitivity of a portfolio's return to a factor, from a
  calendar-time regression.
- **Information coefficient (IC).** The cross-sectional rank correlation between a signal and
  the subsequent forward return; a direct measure of predictive content.
- **Long-short portfolio.** Long the top bucket and short the bottom bucket of a signal; its
  return is the "spread" and isolates the signal from the overall market.
- **Look-ahead bias.** Using, at time $T$, information not actually available until after $T$;
  the primary error a point-in-time protocol prevents.
- **Monotonicity (rank).** The degree to which bucket mean returns rise strictly with the
  bucket index; +1.00 = perfectly increasing.
- **Money-flow multiplier (MFM).** $((C-L)-(H-C))/(H-L)$; where the close sits in the day's
  range, the building block of the A/D line and CMF.
- **On-Balance Volume (OBV).** A running total that adds volume on up-days and subtracts it on
  down-days; a cumulative buying-pressure proxy.
- **Point-in-time (PIT).** An evaluation in which every input is restricted to what was knowable
  at the as-of date.
- **Post-earnings-announcement drift (PEAD).** The continued drift of prices in the direction of
  an earnings surprise for weeks after the announcement.
- **Quality-Minus-Junk (QMJ).** A factor long high-quality and short low-quality firms, quality
  being a composite of profitability, growth, safety, and payout.
- **Quantile / quintile / decile sort.** Ranking the universe into equal buckets (5 = quintile,
  10 = decile) to compare average outcomes across the signal's range.
- **Replication crisis.** The widespread failure of published empirical results (including asset
  pricing anomalies) to reproduce out-of-sample or under stricter methods.
- **Spread ($Q_k-Q_1$).** The difference in mean forward return between the top and bottom
  buckets; the headline economic magnitude of a signal.
- **Standardised unexpected earnings (SUE).** Earnings surprise scaled by its dispersion; the
  classic PEAD sorting variable (proxied here).
- **Survivorship bias.** Distortion from studying only assets that survived to the present,
  omitting delisted/failed names.
- **t-statistic.** An estimate divided by its standard error; $|t|>2$ is the conventional
  ~5% significance threshold used throughout.
- **z-score.** A value re-expressed in standard-deviation units from the cross-sectional mean.

---

## References

### Methods implemented (peer-reviewed and canonical)

- Amihud, Y. (2002). *Illiquidity and stock returns: cross-section and time-series effects.*
  Journal of Financial Markets.
- Amihud, Y., & Mendelson, H. (1986). *Asset pricing and the bid-ask spread.* Journal of
  Financial Economics.
- Asness, C., Frazzini, A., & Pedersen, L. H. (2019). *Quality minus junk.* Review of
  Accounting Studies.
- Bernard, V., & Thomas, J. (1989, 1990). *Post-earnings-announcement drift.* Journal of
  Accounting Research / Journal of Accounting and Economics.
- Chordia, T., Goyal, A., Sadka, G., Sadka, R., & Shivakumar, L. (2009). *Liquidity and the
  post-earnings-announcement drift.* Financial Analysts Journal.
- Cohen, L., & Frazzini, A. (2008). *Economic links and predictable returns.* Journal of
  Finance.
- Corwin, S. A., & Schultz, P. (2012). *A simple way to estimate bid-ask spreads from daily
  high and low prices.* Journal of Finance.
- Fama, E. F., & French, K. R. (1992, 1993, 2015). *The cross-section of expected stock
  returns; common risk factors; a five-factor asset pricing model.* Journal of Finance / JFE.
- Fama, E. F., & MacBeth, J. D. (1973). *Risk, return, and equilibrium: empirical tests.*
  Journal of Political Economy.
- Frazzini, A., & Pedersen, L. H. (2014). *Betting against beta.* Journal of Financial
  Economics.
- Gu, S., Kelly, B., & Xiu, D. (2020). *Empirical asset pricing via machine learning.* Review
  of Financial Studies.
- Jacob, J., Pradeep, K. P., & Varma, J. R. (2022). *Performance of the quality factor in the
  Indian equity market.* IIMA Working Paper 2022-11-01.
- Jegadeesh, N., & Titman, S. (1993). *Returns to buying winners and selling losers.* Journal
  of Finance.
- Kaufman, P. J. (1995). *Smarter Trading* (efficiency ratio).
- Loughran, T., & McDonald, B. (2011). *When is a liability not a liability? Textual analysis,
  dictionaries, and 10-Ks.* Journal of Finance.
- Markowitz, H. (1952). *Portfolio selection.* Journal of Finance.
- Novy-Marx, R. (2013). *The other side of value: the gross profitability premium.* Journal of
  Financial Economics.
- Sharpe, W. F. (1964). *Capital asset prices: a theory of market equilibrium under conditions
  of risk.* Journal of Finance.

### Applied and practitioner literature consulted (local corpus)

Reviewed while building the platform; they informed the emphasis on out-of-sample discipline
and measurement care but are not the source of any specific reported statistic.

- Preet, S., Gulati, A., Gupta, A., & Aggarwal, A. *Backtesting the Magic Formula on Indian
  stock markets.* SGGSCC, University of Delhi (SSRN 3945468).
- Dhanus, S., & Amutha, G. (2025). *Back-testing Super Trend in the 15-minute time frame among
  the top 5 contributors of Nifty 50 stocks.* IJARCMSS 8(2-II), 10–14.
- *Backtesting Brilliance: leveraging analytics for comparing buy-&-hold vs. active
  strategies.* Journal of Informatics Education and Research 4(3), 2024.
- Kargarzadeh, A. *Developing and backtesting a trading strategy using large language models,
  macroeconomic and technical indicators.* Imperial College London.
- Liu, B. (2024). *Analysis of market efficiency in main stock markets using the Kalman filter.*
  Stern School of Business, New York University (arXiv 2404.16449).
- Palomar, D. P. *Backtesting portfolios* (MAFS5310). HKUST.
- Schumann, E. (2018). *Backtesting* (SSRN 3374195).
- *Comprehensive analysis of machine and deep learning models for stock forecasting.* IJACSA
  16(8), 2025.
- Toichatturat, N. (2025). *Stock-market forecasting with a deep-learning approach: generative
  adversarial networks (GANs).* SET Research Scholarship Paper 2024/2025, Thammasat University.
- Miao, Y. *A deep-learning approach for stock-market prediction* (CS230). Stanford University.
- Fister, D., Mun, J. C., Jagrič, V., & Jagrič, T. (2019). *Deep learning for stock-market
  trading: a superior trading strategy?* Neural Network World 29(1), 011.
- *Machine learning and deep learning approaches for stock-market prediction: a comprehensive
  study.* IJIRTM 9(3), 2025.
- *Deep learning in the stock market — a systematic survey.* Artificial Intelligence Review,
  2022 (s10462-022-10226-0).

---

*Code, tests, and full documentation: the Global Market Scanners repository. This manuscript
is a research working paper and is **not investment advice**; nothing herein is a
recommendation to transact in any security.*
