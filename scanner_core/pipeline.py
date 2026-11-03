"""Pure helpers shared by the full-universe scanners' ``main()`` pipelines."""
from __future__ import annotations

from typing import Dict, Iterable, List

MULTI_SCREEN_MIN = 3  # a row is a multi-screen hit when it passes at least this many of 6 screens


def ohlc_counts(all_rows: Iterable[dict]) -> Dict[str, int]:
    """Counts of Darvas and Golden-Crossover outcomes over the OHLC summary rows."""
    rows = list(all_rows)
    return {
        "breakout":  sum(1 for r in rows if r["Darvas_Signal"] == "BREAKOUT_BUY"),
        "breakdown": sum(1 for r in rows if r["Darvas_Signal"] == "BREAKDOWN_SELL"),
        "gc":        sum(1 for r in rows if r["GC_Signal"] == "GOLDEN_CROSS"),
        "above_200": sum(1 for r in rows if r["GC_Signal"] in ("GOLDEN_CROSS", "ABOVE_200DMA")),
    }


def screens_passed(ohlc: dict, res: dict) -> int:
    """How many of the six screens (Darvas, Golden Crossover, 4 fundamentals) a symbol passes."""
    return sum([
        ohlc.get("Darvas_Signal") == "BREAKOUT_BUY",
        ohlc.get("GC_Signal") in ("GOLDEN_CROSS", "ABOVE_200DMA"),
        bool(res.get("f_strong")),
        bool(res.get("qualifies_cc")),
        bool(res.get("qualifies_mf")),
        bool(res.get("qualifies_bc")),
    ])


def triple_rows(fund_rows: Iterable[dict]) -> List[dict]:
    """Backward-compat 'triple hits': Darvas breakout + strong Piotroski + Coffee Can PASS."""
    return [r for r in fund_rows
            if r.get("Darvas_Signal") == "BREAKOUT_BUY"
            and r.get("Piotroski_Strong") == "YES"
            and r.get("CoffeeCan") == "PASS"]


def new_screener_counts() -> Dict[str, int]:
    """Fresh accumulator for per-screener pass counts."""
    return {"piotroski": 0, "cc": 0, "mf": 0, "bc": 0}


def count_screeners(sc_counts: Dict[str, int], res: dict) -> None:
    """Increment the per-screener counters for one fundamental result."""
    if res.get("f_strong"):     sc_counts["piotroski"] += 1
    if res.get("qualifies_cc"): sc_counts["cc"]        += 1
    if res.get("qualifies_mf"): sc_counts["mf"]        += 1
    if res.get("qualifies_bc"): sc_counts["bc"]        += 1
