# Global DVM Composite — Trendlyne GGG classification worldwide

`dvm_composite.py` fuses the two halves into one ranking: **Momentum** (technical,
local OHLC) + **Durability** and **Valuation** (fundamental, `fundamentals_cache.db`)
→ the Trendlyne D/V/M composite classification, for every stock with both, across
all 19 markets. Self-contained, no network.

- **Durability (0-100)** — ROE, D/E, revenue growth, operating margin, earnings growth
- **Valuation (0-100)** — 60% earnings-yield rank + 40% inverse-P/B rank (cheap = high)
- **Momentum (0-100)** — RSI, MACD, DMA-stack, 52w-high proximity
- **Code** — D/V/M each Good(≥50)/Bad(<50) → GGG Strong Performer, GGB Value Under
  Radar, BBG Momentum Trap, etc. Ranked by (D+V+M)/3.

## Result — 726 stocks, 19 markets

Classification distribution:
`GGG 155 · GBG 146 · GGB 136 · GBB 128 · BGB 66 · BGG 39 · BBG 36 · BBB 20`

**Top GGG "Strong Performers" (global):**

| Mkt | Ticker | D | V | M | composite |
|---|---|---|---|---|---|
| ZA | MTM.JO (Momentum Metropolitan) | 73 | 85 | 88 | **81.8** |
| ZA | RDF.JO (Redefine) | 65 | 95 | 83 | 80.8 |
| UK | NWG.L (NatWest) | 69 | 86 | 86 | 80.1 |
| SA | 1050.SR (Banque Saudi Fransi) | 64 | 88 | 84 | 78.7 |
| UK | STAN.L (Standard Chartered) | 69 | 78 | 88 | 78.4 |
| CA | CM.TO (CIBC) | 80 | 64 | 90 | 78.0 |
| US | BAC / JPM / BRK-B | … | … | … | 75–77 |
| BR | BBSE3 / CPLE3 / MULT3 | … | … | … | 75 |

**Insight:** the global GGG set is dominated by **financials** (banks/insurers across
ZA, UK, SA, CA, US, BR, AU) — high ROE + reasonable valuation + strong momentum in the
2024–25 rate environment. The composite surfaces the same theme independently in every
market.

```bash
python dvm_composite.py                # global GGG ranking
python dvm_composite.py --code GGB     # Value-Under-Radar only
```

## Caveats
- Only covers the ~730 liquid names that have fundamentals (fundamentals are a current
  yfinance snapshot, not point-in-time) — extend coverage via `fundamentals_global.py --top N`.
- Valuation rank is cross-sectional over this set; momentum is a current snapshot.
