# Accumulation / Chaikin-Money-Flow screener — with forward-return validation

[`accumulation_screener.py`](accumulation_screener.py) ranks stocks by an
**accumulation** and **Chaikin Money Flow (CMF)** signal, and — the point —
**validates** it against realised forward returns at the **1-month (21d)** and
**6-month (126d)** horizons.

## The signal (daily OHLC)
Over a trailing window (default ~2 months), reusing the [`darvas_volume.py`](darvas_volume.py)
primitives:

- **CMF** — Chaikin Money Flow ∈ [−1,1]: closes in the upper part of the daily range,
  weighted by volume (> 0 = accumulation).
- **accum** — composite = `CMF + OBV-trend + A/D-trend + volume-trend + tanh(½·ln(up/down-volume))`
  (all scale-free), a broader "is volume being acquired?" score.

## Validation — point-in-time, look-ahead-free
The signal is measured over the window ending at date **T**; the return is realised
**strictly after T** (T → T+horizon). Names are sorted into quintiles; we report the
**median** forward return per quintile (robust to penny outliers), the **Q5−Q1**
spread, rank **monotonicity**, and the **information coefficient** (signal↔return
correlation). Pooled across the liquid names in all 19 markets.

### Result (all markets, ~6,600 stock-observations per horizon)

| Horizon | Signal | Q1 → Q5 median forward | Q5−Q1 | monotonicity | IC |
|---|---|---|---|---|---|
| **1-month** | CMF | −1.4 → −0.1 | +1.25% | +0.70 | +0.018 |
| **1-month** | accum | −1.5 → −0.3 | +1.27% | +0.10 | +0.002 |
| **6-month** | CMF | −3.4 → +1.1 → +3.4 → +5.8 → +4.4 | **+7.80%** | **+0.90** | +0.059 |
| **6-month** | **accum** | −4.1 → +0.4 → +4.5 → +4.8 → +6.7 | **+10.82%** | **+1.00** | +0.071 |

**Read:** accumulation/CMF is a **weak 1-month signal but a solid 6-month one** — the
6-month quintiles are (near-)perfectly monotone and the top-minus-bottom spread is
+8% to +11%. This matches the thesis: accumulation precedes *multi-month* moves, not
next-week noise. The IC (~0.06–0.07 at 6m) is respectable for a single price/volume
signal.

**Caveats (stated plainly):** the seed parquets hold ~1 trading year, so the 6-month
test uses a single forward window — treat the **ordering** (monotone quintiles) as the
result and the magnitudes as indicative; returns are gross; medians are used so
penny-stock outliers don't distort the means.

## The screen
The CLI also prints the **current** top names by the signal (the actionable screen)
across the liquid universe.

```bash
python accumulation_screener.py --market US            # screen + 1m/6m validation
python accumulation_screener.py --all --signal cmf     # rank by CMF, all markets
python accumulation_screener.py --market US --out accum_screen.csv
```

Pure cores (`accumulation_signal`, `information_coefficient`, `quantile_returns`,
`monotonicity`) are covered by [`tests/`](tests/test_core.py) and enforced by CI. This
complements [`darvas_volume.py`](darvas_volume.py) (which locates accumulation *within
a box*) — here the accumulation signal is screened *and validated* on its own.
