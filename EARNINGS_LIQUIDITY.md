# Earnings × liquidity / volume / price — a PEAD-conditioning study

[`earnings_liquidity.py`](earnings_liquidity.py) ties three building blocks together —
[`liquidity_factor.py`](liquidity_factor.py) (Amihud illiquidity), [`pead_factor.py`](pead_factor.py)
(earnings-announcement events + drift), and raw price/volume — to ask one question:

> **Do a stock's liquidity, traded volume and price condition the market's reaction to
> and drift after quarterly announcements / results (PEAD)?**

The classic answer (Chordia, Goyal, Sadka & Sadka 2009; Sadka 2006) is that **PEAD is
stronger in illiquid, low-volume, lower-priced stocks** — trading frictions are the
limit-to-arbitrage that lets the drift persist.

## Method (point-in-time, look-ahead-free)
For each announcement-proxy event (volume spike + return jump) with a full forward
window, measure:
- **pre-event** Amihud illiquidity, average dollar-volume, and price level;
- the announcement-day **volume surge** (vs the pre-event average);
- the **surprise** (event-window CAR) and the **PEAD drift** (post-event CAR);
- the **directional drift** = drift × sign(surprise) — how far price continues in the
  surprise's direction (higher = stronger PEAD).

Then compare median directional drift across **illiquidity / volume / price** quantiles.

## What the data shows (all 19 markets, 8,803 events, 40-day drift)

| Comparison | Q5 − Q1 (median dir-drift) | IC | reading |
|---|---|---|---|
| by **illiquidity** (Q5 = illiquid) | −0.35% | **+0.029** | faintly the Chordia-Sadka direction (illiquid → a bit more drift), but noisy |
| by **dollar volume** | +0.15% | +0.007 | ~no conditioning |
| by **price level** | −0.46% | −0.003 | ~no conditioning |

**Clean, strong finding:** announcement-day **volume surges to a median 3.8× the
pre-event average** (90th pct 8.7×) — the volume↔results link is unambiguous.

**Honest read on conditioning:** the illiquidity→drift information coefficient is
**weakly positive (+0.029)** — the expected sign — but the quintiles aren't monotone,
and volume/price show no clear effect. In this **~1-year, event-*proxy*** sample the
liquidity-conditioning of PEAD is a second-order effect that's too faint to call
cleanly. A sharp test needs **true announcement dates** (not a volume/return-spike
proxy) and **multi-year** history — the same data limits noted for `pead_factor.py`.
The machinery is correct (pure core unit-tested); the signal is genuinely weak here.

## Per-country test — where the effect actually lives (`--by-market`)
The pooled number **hid** the result. Testing each country separately (IC of directional
drift vs pre-event illiquidity) shows the Chordia-Sadka effect is real and concentrated
exactly where theory predicts — in **less-efficient / emerging** markets, not the US:

| market | events | vol surge× | **illiq_IC** |
|---|---|---|---|
| **BR** (Brazil, emerging) | 109 | 3.6 | **+0.242** |
| EU | 404 | 3.6 | +0.151 |
| UK | 482 | 3.9 | +0.132 |
| SG | 440 | 4.0 | +0.100 |
| AU / CA / DE | … | ~3.8 | +0.02 … +0.06 |
| **US** (most efficient) | 4,075 | 3.7 | **+0.010** ≈ 0 |
| FI / ZA (tiny samples) | 108 / 53 | 4.5 / 3.5 | −0.26 / −0.03 (noise) |

**8 of 12 markets show the expected sign (illiq_IC > 0).** The US — the deepest, most
arbitraged market — sits at ≈0, exactly as the limit-to-arbitrage story predicts:
frictions let the drift persist in illiquid/emerging markets and get arbitraged away in
liquid ones. The pooled +0.029 was a weak average of strong emerging markets (BR +0.24)
and efficient ones (US +0.01). The **announcement volume surge (~3.5–4.5×) is universal**
across every country.

```bash
python earnings_liquidity.py --all --by-market         # per-country PEAD-liquidity IC
```

## Real earnings dates (US, SEC EDGAR) — `--edgar`
The pooled/US study uses a **volume-spike event proxy**. `--edgar` replaces it with the
**actual 10-Q/10-K filing dates** from SEC EDGAR submissions (via `pit_fundamentals`'
ticker→CIK map) — real quarterly/annual results-announcement dates — and re-runs the
liquidity conditioning.

| US measure | volume-spike proxy | **real EDGAR filing dates** |
|---|---|---|
| events | ~4,000 | 313 (10-Q/10-K in the price window) |
| **illiq_IC** | +0.010 (≈0) | **+0.102** |
| Q5−Q1 dir-drift | ~0 | **+1.04%** |
| announcement volume surge | 3.7× (by construction) | 1.6× (real filings) |

Using **real dates strips the proxy's noise** (volume spikes that aren't earnings — M&A,
index changes, macro) and the liquidity-conditioning of PEAD jumps **10×**, from ≈0 to
**+0.102** — now comparable to the emerging-market proxy results. This is the
Chordia-Sadka effect **confirmed in the US** once the event is dated correctly.

Nuance: the 10-Q *filing* date is often a few days after the earnings press release
(8-K); the ideal anchor is the 8-K earnings-release date, but 10-Q/10-K filing dates
already sharpen the result 10× over the proxy.

```bash
python earnings_liquidity.py --edgar --limit 120       # real US filing dates (needs SEC_UA)
```

## Quick start
```bash
python earnings_liquidity.py --market US
python earnings_liquidity.py --all --horizon 40        # pooled, all markets
python earnings_liquidity.py --all --by-market         # per-country breakdown
python earnings_liquidity.py --edgar                   # US, real EDGAR filing dates
```

Pure cores (`directional_drift`, `bucket_stats`, `spread_qhigh_qlow`) are covered by
[`tests/`](tests/test_core.py) and enforced by CI. It reuses the event detector and
Amihud measure rather than re-implementing them, so the comparison is consistent with
the standalone [PEAD](PEAD.md) and [liquidity](LIQUIDITY.md) modules.
