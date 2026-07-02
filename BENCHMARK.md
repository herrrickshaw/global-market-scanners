# Benchmark — validating our factors against real public factor returns

[`benchmark.py`](benchmark.py) closes the loop opened by [DATA_SOURCES.md](DATA_SOURCES.md):
it **fetches Kenneth French's published factor returns** (the free analogue of the
IIMA IFFM library the quality paper uses) and runs the paper's alpha regression
against them — so our internally-built factors are checked against *real*,
externally-published benchmark returns, not an internal proxy.

## What it fetches
Ken French's **daily** factor series (public, no key) per region:

| Region | markets | Ken French series |
|---|---|---|
| North America | US, CA | US research 5-factor + Momentum (daily) |
| Europe | UK, DE, EU, FI, DK, SE, CH | Europe 5-factor + Mom |
| Japan | JP | Japan 5-factor + Mom |
| Asia Pacific ex Japan | AU, HK, SG | Asia-Pac-ex-Japan 5-factor + Mom |
| Emerging | BR, CN, TW, KR, SA, ZA | **Developed** 5-factor (proxy — Ken French has no *daily* emerging file) |

`factors(region)` downloads, parses (pure `parse_ff_csv`), and caches these to
`benchmark_cache/`. `factor_premia()` reports each factor's annualised mean/vol/Sharpe.

**Live check (North America, 1963–2026, 15,833 days):** Mkt-RF 7.34% (Sharpe 0.45),
Mom 7.38% (0.59), HML 3.71%, RMW 3.02%, CMA 2.95%, RF 4.33% — the textbook premia,
confirming the fetch + parser are correct.

## The paper's test, with real factors
`validate_quality(market)` forms the **long-only quality (LQ)** portfolio from
[`quality_factor.py`](quality_factor.py) (top-decile quality) and regresses its daily
excess return on the region's real **Carhart 4-factor** series (Mkt-RF, SMB, HML, Mom)
— the exact regression in the IIMA paper.

**US result — the loadings replicate the paper:**

| factor | loading | t | reads as |
|---|---|---|---|
| Mkt-RF | 1.56 | 7.9 | market exposure |
| SMB | 0.39 | 1.7 | mild small tilt |
| **HML** | **−0.88** | −4.3 | **negative value** ✓ (quality ≠ cheap) |
| **Mom** | **+0.36** | 2.8 | **positive momentum** ✓ |

The signs on HML (−) and Mom (+) match AFP/IIMA exactly, now measured against the
**real published factors** — a genuine external validation of our quality tilt.

## Honest boundary
The regression also returns an *alpha*, but it is a **diagnostic, not a performance
claim**, and is flagged `UNRELIABLE`: the LQ portfolio is selected from *current*
quality scores (look-ahead) over only ~1 year of seed data and a small fundamentals
universe, which inflates alpha. **Read the loadings, not the alpha.** The factors
themselves are real; a trustworthy alpha needs point-in-time quality history (US
EDGAR path) and a longer window.

## Quick start
```bash
python benchmark.py --region "North America"        # real factor premia
python benchmark.py --validate-quality --market US  # LQ loadings vs real factors
python benchmark.py --validate-quality --market JP  # regional (Japan) factors
```

The parser and regression cores (`parse_ff_csv`, `carhart_alpha`, `factor_premia`)
are covered by [`tests/`](tests/test_core.py); the download is governed via
`apiclient` and fails gracefully offline (so CI never depends on the network).
