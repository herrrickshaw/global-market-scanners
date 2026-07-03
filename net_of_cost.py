#!/usr/bin/env python3
"""
net_of_cost.py  —  Remediation L8: report spreads NET of trading cost, plus a
break-even cost for every signal.

The paper reports gross (pre-cost) top-minus-bottom spreads. This module turns a
gross spread into a net spread under an explicit, liquidity-aware cost model, and
computes the *break-even cost* — the round-trip cost that would exactly erase the
spread. A signal "survives costs" iff its break-even cost exceeds the realistic
round-trip cost of its universe.

Cost model (per round trip = one buy + one sell), all in fraction-of-notional:

    round_trip = 2 * ( commission + half_spread + impact )

  - commission   : broker + statutory, per side (fraction).
  - half_spread  : half the quoted bid-ask spread, paid per side (fraction).
  - impact       : price impact of the trade. We tie this to the SAME Amihud
                   (2002) illiquidity used elsewhere in the paper:
                       ILLIQ = mean(|return| / dollar_volume)
                   so ILLIQ * (dollars traded) is a direct estimate of the
                   fractional price move your order causes. impact = ILLIQ * trade_value.

A strategy pays `turnover` round trips per rebalance, so:

    net_spread = gross_spread - turnover * round_trip
    break_even_cost (round-trip) = gross_spread / turnover      # cost that zeroes net

All functions are pure. CLI prints a worked example and a per-market table.

Refs: Amihud (2002); Amihud & Mendelson (1986). Not investment advice.
"""
from __future__ import annotations

# Realistic retail round-trip cost per market, as a FRACTION of notional
# (matches apply_costs.py's percentages / 100). Order-of-magnitude, delivery equity.
ROUND_TRIP = {
    "US": 0.0010, "IN": 0.0030, "India": 0.0030, "JP": 0.0020, "Japan": 0.0020,
    "KR": 0.0025, "Korea": 0.0025, "EU": 0.0040, "Europe": 0.0040, "UK": 0.0025,
    "BR": 0.0035, "CA": 0.0015, "DE": 0.0030, "SG": 0.0020, "ZA": 0.0030,
}
DEFAULT_ROUND_TRIP = 0.0030


def amihud_impact(amihud_illiq: float, trade_value: float) -> float:
    """Fractional price impact of a trade, from the Amihud measure.

    ILLIQ has units of |return| per unit dollar-volume, so ILLIQ * dollars ≈ the
    fractional price move the order causes. Clipped to [0, 0.5] for sanity.
    """
    if amihud_illiq <= 0 or trade_value <= 0:
        return 0.0
    return max(0.0, min(0.5, amihud_illiq * trade_value))


def round_trip_cost(commission: float = 0.0005, half_spread: float = 0.0005,
                    amihud_illiq: float = 0.0, trade_value: float = 0.0) -> float:
    """Round-trip cost as a fraction of notional (one buy + one sell)."""
    impact = amihud_impact(amihud_illiq, trade_value)
    per_side = max(0.0, commission) + max(0.0, half_spread) + impact
    return 2.0 * per_side


def net_spread(gross_spread: float, round_trip: float, turnover: float = 1.0) -> float:
    """Net top-minus-bottom spread after paying `turnover` round trips."""
    return gross_spread - max(0.0, turnover) * max(0.0, round_trip)


def break_even_cost(gross_spread: float, turnover: float = 1.0) -> float:
    """Round-trip cost (fraction) that exactly zeroes the net spread.

    A signal survives costs iff break_even_cost > realistic round_trip.
    """
    if turnover <= 0:
        return float("inf")
    return gross_spread / turnover


def survives_costs(gross_spread: float, market: str = "US", turnover: float = 1.0,
                   round_trip: float | None = None) -> bool:
    """True iff the signal's break-even cost exceeds the market's realistic cost."""
    rt = ROUND_TRIP.get(market, DEFAULT_ROUND_TRIP) if round_trip is None else round_trip
    return break_even_cost(gross_spread, turnover) > rt


def net_report(gross_spread: float, market: str = "US", turnover: float = 1.0,
               round_trip: float | None = None) -> dict:
    """Full gross/net/break-even record for one signal in one market."""
    rt = ROUND_TRIP.get(market, DEFAULT_ROUND_TRIP) if round_trip is None else round_trip
    be = break_even_cost(gross_spread, turnover)
    return {
        "market": market,
        "gross_spread": round(gross_spread, 5),
        "round_trip_cost": round(rt, 5),
        "turnover": turnover,
        "net_spread": round(net_spread(gross_spread, rt, turnover), 5),
        "break_even_cost": round(be, 5) if be != float("inf") else be,
        "survives": break_even_cost(gross_spread, turnover) > rt,
    }


def main():
    # Worked example: the paper's headline accumulation and liquidity spreads.
    print("net_of_cost.py — remediation L8 (net-of-cost + break-even)\n")
    examples = [
        ("accumulation 6m (quarterly turnover)", 0.108, "US", 0.5),
        ("liquidity Q5-Q1 per quarter",          0.0424, "US", 1.0),
        ("liquidity Q5-Q1 (illiquid, BR)",       0.0424, "BR", 1.0),
        ("marginal signal (small edge)",         0.004,  "US", 1.0),
    ]
    hdr = f"{'signal':40s} {'gross':>7s} {'cost':>7s} {'net':>8s} {'break-even':>11s}  survives"
    print(hdr); print("-" * len(hdr))
    for name, g, mkt, to in examples:
        r = net_report(g, mkt, to)
        print(f"{name:40s} {r['gross_spread']*100:6.2f}% {r['round_trip_cost']*100:6.2f}% "
              f"{r['net_spread']*100:7.2f}% {r['break_even_cost']*100:10.2f}%  "
              f"{'YES' if r['survives'] else 'no'}")
    print("\nA signal survives iff its break-even cost exceeds the market round-trip cost.")


if __name__ == "__main__":
    main()
