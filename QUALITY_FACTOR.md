# Quality Factor (QMJ) — implementing IIMA W.P. 2022-11-01

Implements the **Quality-Minus-Junk (QMJ)** factor of Asness, Frazzini & Pedersen
(2019) as adapted for India by **Jacob, Pradeep & Varma**, *"Performance of quality
factor in Indian Equity Market"* (IIMA Working Paper 2022-11-01), generalised to the
19 markets in this platform. Module: [`quality_factor.py`](quality_factor.py).

## What the paper says (and what we took from it)

The paper builds a firm's **quality score** as the average of four dimensions from
Gordon's dividend-discount model, each the average of the **standardised ranks**
(z-score of the cross-sectional rank) of its sub-metrics:

| Dimension | Sub-metrics (paper) | What it captures |
|---|---|---|
| **Profitability** | gpoa, roe, roa, cfoa, gmar, −accruals | operating performance |
| **Growth** | 5-yr Δ in gpoa, roe, roa, cfoa, gmar | improving performance |
| **Safety** | −beta, −leverage, −Ohlson-O, +Altman-Z, −roe-vol | low risk to operations |
| **Payout** | −net equity issuance, −net debt issuance, +net payout | shareholder-friendly, low reinvestment need |

`Quality = avg(Profitability, Growth, Safety, Payout)`. Stocks are sorted into
deciles (D10 = quality, D1 = junk); a 2×3 size×quality sort gives the factor:

```
QMJ = ½(small-quality + big-quality) − ½(small-junk + big-junk)     (value-weighted)
LQ  = ½(small-quality + big-quality)                                (long-only)
```

**Headline findings** (which motivated *how* we built our decision layer):
- QMJ earns a four-factor alpha of **~0.92%/month** in India — ~50% higher than the
  US — driven mainly by **profitability and payout** (the emerging-market "tunnelling"
  hypothesis).
- Quality has **low portfolio churn** (fundamentals-based rankings are *sticky*),
  **lower risk**, and **shorter drawdowns** (max ~18 months) than momentum.
- **Long-only quality is viable**, especially restricted to large caps — accessible to
  retail investors.
- A +1 SD increase in quality is associated with a **+23.6% market-to-book premium**
  (Table 8).

## How this platform implements it

`quality_factor.py` computes the four-dimension standardised-rank quality score
**per market** (quality is relative within a market), classifies deciles, and builds
the LQ / QMJ portfolio legs value-weighted — faithfully following Eq. 6–7.

**Data honesty.** This platform's global fundamentals cache is a per-market yfinance
*snapshot* with a subset of AFP's sub-metrics, so each dimension is built from the
available proxies (`DIMENSIONS` in the module), and Safety's beta/volatility are
derived from the local OHLC:

| Dimension | Implemented from |
|---|---|
| Profitability | roe, roa, op_margin |
| Growth | rev_growth, earn_growth |
| Safety | −D/E, −beta, −return-vol (beta/vol from `cleaned_long`) |
| Payout | dividend yield |

It is therefore an AFP-**style** score, not a tick-for-tick CMIE Prowess replication —
and, critically, **India itself is not in this cache** (its 19 markets are
US/JP/KR/EU/CN/…; India lives in the separate India repo), so we generalise the
method to the markets we have.

## What reproduces, and what doesn't

Two of the paper's results are directly checkable from a current cross-section and are
reported by `--premium`:

- **Quality price premium** — a regression of `log(M/B) ~ quality + log(size)` with
  market fixed effects (paper Table 8). On our 731-firm snapshot the quality
  coefficient is **positive (+0.058)** but weaker/insignificant vs the paper's +23.6% —
  expected, because the paper uses a 26-year CMIE panel with firm+year fixed effects
  and the full AFP sub-metrics, whereas we have a single-date snapshot with proxies.
- **Driver breakdown** — correlation of each dimension with `log(M/B)`: **profitability
  dominates (+0.48)**, matching the paper's qualitative finding that profitability is
  the key priced dimension.

The full **QMJ alpha** (four-factor calendar-time regression over 26 years) is *not*
reproducible here — it needs point-in-time fundamentals history we don't have outside
the US (the same honest limitation as [`pit_global.py`](pit_global.py)).

## How it plugs into the platform

- **Meta-screen fusion** — quality is now a component of the conviction score in
  [`meta_screen.py`](meta_screen.py): `python quality_factor.py --all --out q.csv`
  then `python meta_screen.py --quality q.csv`. A name confirmed by quality *and* the
  DVM/ML signals ranks above one confirmed by fewer.
- **Portfolio input** — the LQ long-only quality set is a natural candidate universe
  for [`portfolio.py`](portfolio.py); the paper's *low-churn* finding is exactly why
  `portfolio.py` has turnover control.
- **Risk** — the paper's shorter-drawdown claim is measurable with [`risk.py`](risk.py).

## Quick start

```bash
python quality_factor.py --all --premium              # score all markets + price-premium test
python quality_factor.py --market US --portfolios     # LQ / QMJ legs for the US
python quality_factor.py --all --out quality.csv      # export for meta_screen --quality
```

Pure cores (`z_rank`, `dimension_score`, `quality_score`, `qmj_combo`, `lq_combo`,
`assign_deciles`, `value_weight`, `price_premium`) are covered by
[`tests/`](tests/test_core.py) and enforced by CI.
