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

## Quick start
```bash
python earnings_liquidity.py --market US
python earnings_liquidity.py --all --horizon 40        # pooled, all markets
```

Pure cores (`directional_drift`, `bucket_stats`, `spread_qhigh_qlow`) are covered by
[`tests/`](tests/test_core.py) and enforced by CI. It reuses the event detector and
Amihud measure rather than re-implementing them, so the comparison is consistent with
the standalone [PEAD](PEAD.md) and [liquidity](LIQUIDITY.md) modules.
