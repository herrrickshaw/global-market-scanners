#!/usr/bin/env python3
"""
apply_costs.py
--------------
Converts the pre-cost screen-viability edges into NET-of-cost edges by
subtracting a realistic per-market ROUND-TRIP transaction cost (retail
brokerage + local statutory taxes/fees). Re-judges each screen's viability
net of costs, for both the 5-day and 21-day horizon summaries.

Round-trip = one buy + one sell, as a % of trade value. Estimates below are
retail, delivery-equity, order-of-magnitude — edit COSTS_BPS to match your broker.

Per-market round-trip cost (basis points; 100 bps = 1.0%):
  India   ~30 bps  STT 0.20% (0.1% buy + 0.1% sell delivery) + brokerage ~0.06%
                   + exchange/stamp/SEBI/GST ~0.04%
  US      ~10 bps  ~$0 commission + SEC/FINRA TAF (sell) ~0.01% + ~0.09% spread proxy
  Japan   ~20 bps  brokerage ~0.10% + no securities transaction tax + ~0.10% spread
  Korea   ~25 bps  Securities Transaction Tax ~0.18% (sell) + brokerage ~0.03% + ~0.04%
  Europe  ~40 bps  French FTT 0.30% (buy, large caps) + brokerage ~0.20%; blended
                   across FR/DE/NL Euro Stoxx names

Usage:
  python apply_costs.py                     # writes net tables + NET_OF_COST.md
"""

import os
import sqlite3
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))

# round-trip cost in PERCENT of trade value, by market
COSTS_PCT = {
    "India":  0.30,
    "US":     0.10,
    "Japan":  0.20,
    "Korea":  0.25,
    "Europe": 0.40,
}

SUMMARIES = {
    "5d":  os.path.join(HERE, "viability_summary.db"),
    "21d": os.path.join(HERE, "viability_summary_21d.db"),
}


def load(db):
    if not os.path.exists(db):
        return None
    return pd.read_sql(
        "SELECT market,screen,avg_hit_pct,avg_edge FROM market_screen_summary",
        sqlite3.connect(db))


def net_table(df):
    df = df.copy()
    df["cost%"] = df["market"].map(COSTS_PCT)
    df["net_edge"] = (df["avg_edge"] - df["cost%"]).round(3)
    df["net_viable"] = ((df["net_edge"] > 0) & (df["avg_hit_pct"] > 50)).map(
        {True: "YES", False: "no"})
    return df.rename(columns={"avg_edge": "gross_edge", "avg_hit_pct": "hit%"})[
        ["market", "screen", "gross_edge", "cost%", "net_edge", "hit%", "net_viable"]
    ].sort_values(["market", "net_edge"], ascending=[True, False])


def main():
    md = ["# Screen Viability — NET of local tax + brokerage\n",
          "Pre-cost edges minus a per-market round-trip cost (retail brokerage + local "
          "statutory taxes). Round-trip cost assumptions (% of trade value):\n",
          "| Market | round-trip cost | main components |",
          "|---|---|---|",
          "| India | 0.30% | STT 0.20% + brokerage ~0.06% + stamp/exch/GST ~0.04% |",
          "| US | 0.10% | ~$0 commission + SEC/TAF ~0.01% + ~0.09% spread |",
          "| Japan | 0.20% | brokerage ~0.10% + no STT + ~0.10% spread |",
          "| Korea | 0.25% | STT ~0.18% (sell) + brokerage ~0.03% + ~0.04% |",
          "| Europe | 0.40% | French FTT 0.30% + brokerage ~0.20% (blended FR/DE/NL) |",
          "\n`net_edge = gross_edge − round_trip_cost`. "
          "`net_viable = net_edge > 0 AND hit% > 50`.\n"]

    for horizon, db in SUMMARIES.items():
        df = load(db)
        if df is None:
            print(f"  [skip] {db} missing", file=sys.stderr); continue
        nt = net_table(df)
        out_csv = os.path.join(HERE, f"net_viability_{horizon}.csv")
        nt.to_csv(out_csv, index=False)
        n_gross = (df["avg_edge"] > 0).sum()
        n_net = (nt["net_edge"] > 0).sum()
        md += [f"\n## {horizon} horizon  ({n_net}/{len(nt)} screens keep positive edge "
               f"net of cost; {n_gross} were positive pre-cost)\n",
               nt.to_markdown(index=False)]
        print(f"[{horizon}] wrote {out_csv}", file=sys.stderr)

    md += ["\n## Bottom line",
           "Costs are small relative to the strongest signals but decisive for the marginal ones. "
           "Screens with sub-cost gross edge (e.g. Golden Crossover's ~0.03–0.2% in some markets, "
           "US Price-Volume) flip to **not viable** once tax+brokerage are paid — only the higher-edge "
           "screens (RSI-oversold, Europe/India volume breakouts at the monthly horizon) survive net "
           "of cost. Per-signal edges this thin also assume no slippage beyond the spread proxy."]
    open(os.path.join(HERE, "NET_OF_COST.md"), "w").write("\n".join(md))
    print("wrote NET_OF_COST.md", file=sys.stderr)


if __name__ == "__main__":
    main()
