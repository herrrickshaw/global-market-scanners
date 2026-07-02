# Watchlists — fundamentally strong & being accumulated

[`watchlists.py`](watchlists.py) produces two **separate**, ranked shortlists that
answer different questions from different data — plus their intersection.

| List | Question | Signal | Source |
|---|---|---|---|
| **Fundamentally strong** | *Is this a good business?* | AFP/QMJ quality score (profitability/growth/safety/payout) + a ROE>15 & D/E<1 gate | `fundamentals_cache.db` (via [`quality_factor.py`](quality_factor.py)) |
| **Being accumulated** | *Is someone building a position now?* | coiling in a Darvas box while volume is acquired — OBV/CMF/up-down-volume rising, price pinned | daily OHLC (via [`darvas_volume.py`](darvas_volume.py)) |

They come from **different universes** on purpose — fundamentals cover the index
heavyweights; the accumulation scan covers *every* liquid name — so each list stands
on its own. `--both` shows the overlap: good business **and** being accumulated.

## Example (US)
```
FUNDAMENTALLY STRONG            BEING ACCUMULATED              BOTH
APP   qual 100  ROE 266%        OLPX  pos 1.00  U/D 1.43       APP    qual 100  acc 3.94
LLY   qual 97   ROE 108%        DKL   pos 0.45  U/D 1.97       SNDK   qual 94   acc 2.25
NVDA  qual 89   ROE 114%        ATLC  pos 0.83  U/D 2.40       BRK-B  qual 74   pos 0.92
V ★   qual 86   ROE 60% DE .67  ...                            (strong + coiling near breakout)
```
(★ = the classic strong profile: ROE > 15% and D/E < 1.)

## Why keep them separate
A high-quality business that nobody is buying, and a low-quality name being
aggressively accumulated, are **different opportunities with different theses** —
merging them into one score would hide that. The two lists let you act on either
signal, and the intersection is the highest-conviction subset when you want both.

## Quick start
```bash
python watchlists.py --market US --top 20            # the two lists for one market
python watchlists.py --all --both                    # all 19 markets + the overlap
python watchlists.py --market US --csv watch         # -> watch_strong.csv, watch_accum.csv
```

Tunables: `MIN_QUALITY` (strong floor, default 60th pct) and `MIN_ACCUM`
(accumulation floor). Pure filter/merge helpers (`clean_key`, `strong_from_scores`,
`accumulated_from_scan`, `intersect`) are covered by [`tests/`](tests/test_core.py)
and enforced by CI.
