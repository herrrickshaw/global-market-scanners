# Darvas × volume-acquisition monitor

[`darvas_volume.py`](darvas_volume.py) is a Darvas-box monitor tuned to spot
**volume acquisition** — stealth accumulation and HFT footprints *inside* the box —
i.e. someone quietly building a position while the price coils, before it breaks out.

## The setup
A **Darvas box** is a consolidation range with a ceiling (box top = a recent high
that held) and a floor (box bottom = a low that held). The classic entry is a
breakout above the top on rising volume. This monitor overlays, inside the box:

**Volume acquisition (is volume being absorbed while price ranges?)**
| Signal | Reads as accumulation when… |
|---|---|
| **OBV trend** (on-balance volume) | rising (buyers lifting on volume) |
| **Chaikin A/D trend** | rising (closes in the upper range on volume) |
| **Chaikin Money Flow (CMF)** | > 0 |
| **Up/Down volume ratio** | > 1 (more volume on up-days) |
| **Volume trend** | rising through the coil |

**HFT / microstructure footprint** (reused from [`hft_selection.py`](hft_selection.py))
| Signal | Reads as stealth work when… |
|---|---|
| **Kaufman efficiency ratio** | **low** — price pinned in the box (mark-time accumulation, not a trend) |
| **Volume autocorrelation** | **positive** — a persistent, *worked* order, not one-off prints |

These combine into a cross-sectional **accumulation score**. High score + tight box +
price pressing the top = the pre-breakout coil.

## The design rule (honoured)
The box is formed **excluding the current bar**. Including it would swallow any
breakout by construction (the box top would always be ≥ today's high). Because the
current bar is excluded, a breakout shows as `position > 1.0` (close *above* the box
top) — which is exactly how the monitor detects volume-confirmed breakouts.

## What it produces
`--state in_box` (default) — **coils with volume being acquired**, e.g. names pressing
the box top (`pos → 1.0`) with rising OBV, up/down volume > 2, CMF > 0, efficiency
ratio ≈ 0 (pinned) and positive volume autocorrelation.

`--state breakout` — **volume-confirmed breakouts**: close above the box top
(`pos 1.1–1.2`) with volume ≥ 1.5× the box average, on strong prior accumulation.

`--state breakdown` — box bottom lost.

## Quick start
```bash
python darvas_volume.py --market US --top 15          # coiling accumulation names
python darvas_volume.py --market US --state breakout  # volume-confirmed breakouts
python darvas_volume.py --all --state in_box          # across all 19 markets
```

## Boundary
Like [`hft_selection.py`](hft_selection.py) this is a **daily-OHLC** monitor: OBV /
A/D / CMF / volume are the accumulation proxies available without a tick/LOB feed. It
detects *that* volume is being acquired and *where* in the box, not the intraday
order-book mechanics of *who* is acquiring it. It pairs naturally with the platform's
existing Darvas breakout scanners (`full_*_market_scan.py`) — those find the breakout;
this tells you which coils are being accumulated *before* it.

Pure cores (`obv`, `chaikin_ad`, `chaikin_money_flow`, `up_down_volume_ratio`,
`trend_corr`, `darvas_box`, `box_state`, `accumulation_score`) are covered by
[`tests/`](tests/test_core.py) and enforced by CI.
