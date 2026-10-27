# India open-to-close quintile picks — not tradeable after costs

**Question.** The signal re-test ([SIGNAL_RESEARCH_RESULTS.md](SIGNAL_RESEARCH_RESULTS.md) §5c) found that India's `hi52`, `lowvol` and `mom12_1` spreads are earned intraday and lose overnight. Can a day trader take just that leg: enter at the open, square off at the close, never hold overnight?

**Answer: no.** The gross intraday spread is real but small, has decayed since 2019, and sits below even the statutory fees. Code: [`intraday_india.py`](intraday_india.py) (method in its docstring; tests in `tests/test_intraday_india.py`). Full table: [INTRADAY_INDIA_RESULTS.md](INTRADAY_INDIA_RESULTS.md).

Setup:
- Universe: daily top 750 NSE names by trailing rupee volume, July 2017 to October 2026.
- Signals are computed from the prior close; quintile books are entered at the open and exited at the close.
- Bars locked at a price band all day (High = Low) are dropped.
- Indian intraday shorting is allowed in the cash market (MIS, squared off the same day), so the long-short book needs no SLB or futures.

## Findings

1. **A gross edge exists.** The long-short spread earns 13.0 (`hi52`), 17.4 (`lowvol`) and 8.5 (`mom12_1`) bps a day before costs, which matches the re-test's intraday leg.
2. **Fees alone take all of it.** One intraday round trip costs 8.2 bps on a ₹1 lakh ticket:
   - STT 2.5 bps on the sell side;
   - brokerage of ₹20 per order;
   - NSE transaction charges, stamp duty and SEBI fees;
   - GST on the brokerage and charges.

   The long-short book pays two round trips a day (16.5 bps). Net of fees only, before any slippage:

   | signal | net |
   |---|---|
   | `hi52` | −8.7%/yr |
   | `lowvol` | +2.4%/yr (t 0.4) |
   | `mom12_1` | −19.9%/yr |

   Break-even slippage is −0.9, 0.2 and −2.0 bps per side, so nothing is left to pay for crossing the spread at the open auction.
3. **Big tickets don't rescue it.** On a ₹1 crore ticket the ₹20 brokerage cap brings fees down to 3.6 bps per round trip. `lowvol`'s 2024–25 gross of 12–16 bps a day would then leave about 1.5–2 bps per side for slippage. Crossing the spread in 150 names a side at the open auction costs more than that.
4. **The edge has decayed.** The gross long-short spread peaked in 2019 at 31–36 bps a day across all three signals. Since 2021, `hi52` has run at 3–11 bps and `mom12_1` at about 0, with `lowvol` the most stable at 5–16. The +3.29% / 21-day intraday leg in the re-test is mostly an average weighted toward 2018–2020.
5. **The open auction is a bad place to buy.** For the average liquid Indian stock, every year from 2017 to 2026, the overnight return is positive (+11 to +38 bps) and the open-to-close return is negative (−12 to −28 bps). The top 100 names show it too: +14.9 overnight, −11.8 intraday. A long-only buy at the open therefore loses about 12–17 bps a day before costs, whatever the signal. The long-short book cancels this common drift, which is why its gross edge is positive.

## What is still useful
- **Execution timing for the swing strategies.** India's daily and weekly picks should not be entered at the open auction. On the drift above, entering at or near the close saves 10–20 bps per entry on average. That matches `signal_research.py`'s next-close entry, and it's worth applying to the mailer's live orders.
- **`lowvol` is the only signal whose intraday edge has held up** (gross 11–16 bps a day in 2022–25). If the costs change, for example an STT cut, a zero-brokerage plan or a maker rebate, it is the one to retest first.

## Caveats
- NSE's official close is the volume-weighted price of the last 30 minutes, not the last trade, so "close" approximates exiting through the 3:20–3:30 square-off window. The bhavcopy open is the pre-open call-auction price. Both are achievable fills, but real slippage around them is untested here. Testing it needs the Groww or IndStocks 1-minute candles (use case 7 in the intraday use-cases note).
- Equal-weight quintiles of about 150 names per side; no capacity or impact model; no price-band check beyond dropping High = Low bars.
- DSR is 0.00 for every variant; this note makes no claim that an edge exists after costs.
