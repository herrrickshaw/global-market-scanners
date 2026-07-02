# Corporate-action screeners — stock splits & rights issues

[`corporate_actions.py`](corporate_actions.py) provides two screeners, each honest
about what daily OHLC can and can't tell you.

## Stock-split screener — authoritative (yfinance)
Real splits are **adjusted out** of the close, so you *cannot* reliably detect them
from adjusted prices. The screener therefore fetches the **actual split history** from
yfinance `.splits` for the most-liquid names per market and lists splits within the
data window.

```bash
python corporate_actions.py --action split --market US --limit 40
```
```
=== STOCK SPLIT SCREENER (yfinance, authoritative) — 1 splits in window ===
  mkt ticker      date           ratio  type
  US  NFLX        2025-11-17      10:1  forward
```
That's the real Netflix 10:1 split — the only one among the top-40 US liquid names in
the ~1-year window (splits are rare, so a short list is the correct result).

**Offline heuristic** (`--heuristic`) — a price-gap detector for **unadjusted** feeds:
flags a close ratio within a tight tolerance of a clean split factor (2:1, 3:1, 5:1,
10:1, reverse) with volume confirmation, excluding zero-price glitches. On *adjusted*
data it is unreliable — a −33% / −50% crash collides with the 3:2 / 2:1 factors — so
it's clearly labelled and secondary.

## Rights-issue screener — candidates (honest limit)
A rights issue dilutes holders: on the ex-rights date the price gaps **down** by a
discount, idiosyncratically (the market didn't fall with it), on heavy volume, then
**settles** at the diluted level. The screener flags days matching that signature
(drop in the −8% … −35% band, not a split ratio, stock fell ≥8% more than the market,
volume spike, price stabilises within ±15% three days later).

```bash
python corporate_actions.py --action rights --market US
```

**Honest caveat (stated in the output):** from daily OHLC a rights issue is
**indistinguishable** from an earnings repricing or a secondary offering — they share
the "idiosyncratic persistent gap-down" signature. So this is a *"sharp idiosyncratic
dilution / repricing candidate"* list, **not** a clean rights-issue list. A true
rights issue needs the exchange's corporate-action feed (NSE/BSE announcements, SEC
8-K) to confirm — which the platform's free pipeline doesn't carry. The screener
narrows the field; it can't make the final call from price alone.

## From news & public announcements (SEC EDGAR) — `--news`
The most reliable source for *both* actions is the company's own filing. `--news`
queries **SEC EDGAR full-text search** for 8-K filings that announce the action —
authoritative public announcements, not a price inference:

```bash
python corporate_actions.py --action both --news --since 2025-01-01
```
```
STOCK SPLIT — SEC EDGAR public announcements (13 filings)
  BOXL  2026-06-22  8-K  Boxlight Corp          · reverse stock split
  CLMB  2026-03-02  8-K  Climb Global Solutions · forward stock split
RIGHTS ISSUE — SEC EDGAR public announcements (11 filings)
  DOLE  2026-04-07  8-K  Dole plc               · rights issue
  RGR   2026-03-04  8-K  STURM RUGER & CO INC   · rights issue
  REEMF 2026-01-06  8-K  RARE ELEMENT RESOURCES · rights offering
```
This is what makes the **rights-issue** screener trustworthy — the price heuristic
only narrows the field, but the 8-K announcement confirms it. (US only; EDGAR needs a
contact-style User-Agent — set `SEC_UA="you your@email"` — and fails gracefully
offline.)

## Design
- **Pure detectors** — `nearest_split_ratio`, `label_ratio`, `detect_splits`,
  `detect_rights` — are unit-tested (a clean 2:1 is caught, a glitch and an ordinary
  move are not; a rights-like drop is flagged only when idiosyncratic *and* it
  stabilises, so market-wide falls and continuing crashes are rejected).
- **Data layer** — scans the liquid universe (top 40% by dollar-volume) from the
  cleaned_long parquets; the split path additionally queries yfinance (governed via
  `apiclient`, fails gracefully offline).

```bash
python corporate_actions.py --action both --market US        # both screeners
```

Covered by [`tests/`](tests/test_core.py) and enforced by CI.
