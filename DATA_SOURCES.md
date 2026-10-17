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

## Open keyless sources added 2026-10-17 (verified by curl before adding)

| Source | Gap it fills | Verified | Collector | Output |
|---|---|---|---|---|
| **FINRA consolidated short interest** (`cdn.finra.org/equity/otcmarket/biweekly/shrtYYYYMMDD.csv`) | [`crowding.py`](crowding.py) / [FRONTIER_GAPS.md](FRONTIER_GAPS.md) say true short interest "needs a vendor feed" | HTTP 200, 22,581 rows for settlement 2026-09-30 (AAPL, TSLA present; NYSE/NNM/ARCA/OTC classes), days-to-cover field populated; 2026-09-15 file also HTTP 200 | [`finra_short_interest.py`](finra_short_interest.py) | `data/finra_short_interest_20260930.csv.gz` (668 KB) |
| **ECB euro reference rates** (`ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml`) | `fx.py` rates come from yfinance with a hand-edited offline snapshot; no official source | HTTP 200, 1,885 rows, 65 dates (2026-07-20..2026-10-16), 29 currencies incl. USD/JPY/KRW/INR/CNY/GBP | [`ecb_fx.py`](ecb_fx.py) | `data/ecb_fx_hist_90d.csv` (42 KB) |

Caveats: short interest is a settlement-date snapshot published with a lag (add a
publication lag before any backtest use); not wired into `crowding.py` or `fx.py` yet.
ECB rates are reference-only, EUR-based, TARGET business days. The collectors were run
once at low volume; no existing data was modified. A side observation: FINRA's *daily*
Reg SHO short-sale-volume file (`CNMSshvolYYYYMMDD.txt`) also returned HTTP 200, but its
volume columns contain fractional share counts (e.g. 343590.219355), which is odd for
share volumes, so it was NOT adopted and is unexplained.

### Candidates checked and rejected
- **Nager.Date public holidays** (`date.nager.at/api/v3/PublicHolidays/{year}/{CC}`): keyless
  and responds for US/JP/KR/DE/GB, but returns nothing parseable for IN, and these are
  *public* holidays, not exchange calendars (e.g. NYSE trades on some US public holidays),
  so it cannot replace the curated `market_holidays.HOLIDAYS`. Not added.
- **SEC fails-to-deliver** (`sec.gov/files/data/fails-deliver-data/cnsfails*.zip`): responded
  HTTP 200 (1.1 MB zip) but only the header bytes were inspected, content not parsed; not
  added.
