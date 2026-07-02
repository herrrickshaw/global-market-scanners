# Public data sources — per market

[`data_sources.py`](data_sources.py) is a registry of the **public data sources** for
factor research across the platform's markets. It captures exactly what the IIMA
quality-factor paper (Jacob-Pradeep-Varma, W.P. 2022-11-01) names for India, and
generalises it to all 19 markets.

## What the paper names (India-only)
The paper studies **one market (India)** and cites exactly two data sources:

| Role | Source | Public? |
|---|---|---|
| Raw stock returns **+ firm-level financials** | **CMIE Prowess** | ❌ commercial (subscription) |
| 4-factor benchmark (market/size/value/momentum + risk-free) | **IIMA Indian Fama-French-Momentum (IFFM)** — Agarwalla, Jacob & Varma (2014) | ✅ **public/free** — `faculty.iima.ac.in/~iffm/Indian-Fama-French-Momentum/` |

So the **only public source the paper names is the IFFM factor library**. (`python data_sources.py --paper`.)

## Generalised to the platform's markets
IFFM is India's public benchmark factor library. The registry maps every other market
to its public analogue — the factor-return series you'd use to compute alpha against
(as `factor_research.py` does):

| Library | Coverage | Why |
|---|---|---|
| **AQR Data Sets** (QMJ + BAB) | ~24 developed countries + global | AQR = Asness-Frazzini-Pedersen; the **exact QMJ factor** `quality_factor.py` implements |
| **Kenneth French Data Library** | US + Developed/Emerging + regional (North America / Europe / Japan / Asia-Pacific-ex-Japan / Emerging) | free 3/4/5-factor series covering every region we scan |
| **IIMA IFFM** | India | the paper's own |

Per-market assignment (`python data_sources.py`):
- **Developed** (US, CA, UK, DE, FI, DK, SE, CH, JP, AU, HK, SG) → AQR **country** QMJ + Ken French regional
- **Emerging** (BR, CN, TW, KR, SA, ZA) → AQR **global** QMJ + Ken French Emerging
- **India** → IFFM (country) + AQR + Ken French Emerging

## Raw pipeline sources (what actually feeds the code)
Distinct from the *benchmark* libraries above, these are the **public** raw sources the
pipeline pulls prices/fundamentals from:

| Source | Provides | Markets |
|---|---|---|
| **Yahoo Finance** (`yfinance`) | OHLC + snapshot fundamentals | all |
| **SEC EDGAR** (XBRL) | point-in-time (filed-date) fundamentals | US |
| **NSE/BSE** (`nsepython`, `bseindia`) | OHLC + corporate data | India |

Note the platform deliberately uses **only free/public** raw sources (yfinance/EDGAR/
NSE) — it does **not** depend on the paper's commercial CMIE Prowess.

## How it connects to the code
`factor_research.py` currently builds its factors internally from prices. This registry
records the public library to benchmark each market against — e.g. regress our QMJ
(`quality_factor.py`) on **AQR's published QMJ** per market, or our market/size/value/
momentum on **Ken French** (or **IFFM** for India). It's the "where do the benchmark
returns come from" map, market by market.

## Quick start
```bash
python data_sources.py                 # full per-market table
python data_sources.py --market US     # one market's sources + URLs
python data_sources.py --paper         # exactly what the IIMA paper names
python data_sources.py --public        # the public factor libraries + URLs
```

Pure lookups (`for_market`, `public_factor_sources`, `raw_sources`, `paper_sources`)
are covered by [`tests/`](tests/test_core.py) and enforced by CI.
