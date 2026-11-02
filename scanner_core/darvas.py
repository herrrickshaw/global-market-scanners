"""Darvas-box engine shared by every market scanner.

``locate_box`` is the single pure implementation of the algorithm; the per-market
differences (column lookup, rounding, result keys) live in a :class:`DarvasProfile`
selected from ``PROFILES`` by market name (Strategy + registry), so a new market is
added by registering a profile instead of copying the algorithm.

Hard rule: the current (last) bar is excluded from box formation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

DARVAS_CONFIRM = 3  # bars that must stay inside a swing high/low to confirm a box edge
MIN_EXTRA_BARS = 5  # history needed beyond ``confirm``

LONG_KEYS = ("signal", "box_top", "box_bottom", "current_price", "box_range",
             "upside_to_top_pct", "position_in_box_pct", "data_points")
LONG_KEYS_POS_FIRST = ("signal", "box_top", "box_bottom", "current_price", "box_range",
                       "position_in_box_pct", "upside_to_top_pct", "data_points")
SHORT_KEYS = ("signal", "box_top", "box_bottom", "current_price", "upside_pct",
              "pos_in_box", "data_points")
SHORT_KEYS_NO_COUNT = ("signal", "box_top", "box_bottom", "current_price",
                       "upside_pct", "pos_in_box")


def locate_box(highs: Sequence[float], lows: Sequence[float],
               confirm: int) -> Tuple[Optional[float], Optional[float]]:
    """Return ``(box_top, box_bottom)`` from history that already excludes the current bar.

    ``highs``/``lows`` are plain floats (0 means missing). Top = latest swing high whose
    next ``confirm`` highs are all lower; bottom = first swing low after the top whose next
    ``confirm`` lows are all higher, else the lowest positive low in that segment.
    """
    n = len(highs)
    box_top_idx: Optional[int] = None
    box_top: Optional[float] = None
    for i in range(n - confirm - 1, -1, -1):
        candidate = highs[i]
        if candidate == 0:
            continue
        window = highs[i + 1: i + 1 + confirm]
        if len(window) == confirm and all(h < candidate for h in window):
            box_top_idx, box_top = i, candidate
            break
    if box_top is None:
        return None, None

    segment = list(lows[box_top_idx:])
    box_bottom: Optional[float] = None
    for i in range(len(segment) - confirm):
        candidate = segment[i]
        if candidate == 0:
            continue
        window = segment[i + 1: i + 1 + confirm]
        if len(window) == confirm and all(low > candidate for low in window):
            box_bottom = candidate
            break
    if box_bottom is None:
        valid = [low for low in segment if low > 0]
        box_bottom = min(valid) if valid else None
    return box_top, box_bottom


def classify(current: float, box_top: float, box_bottom: float) -> str:
    """Signal for the current close relative to the box."""
    if current > box_top:
        return "BREAKOUT_BUY"
    if current < box_bottom:
        return "BREAKDOWN_SELL"
    return "IN_BOX"


@dataclass(frozen=True)
class DarvasProfile:
    """Per-market presentation of a Darvas result (algorithm is shared)."""
    market: str
    fuzzy_columns: Optional[Mapping[str, Sequence[str]]]  # None -> exact High/Low/Close
    guard_empty: bool           # treat None / empty frames as INSUFFICIENT_DATA
    decimals: int               # rounding for prices
    current_on_no_top: str      # "raw" | "rounded" | "omit"
    current_on_no_bottom: str   # "rounded" | "omit"
    keys: Tuple[str, ...]       # output keys, in order

    def extract(self, df: pd.DataFrame, confirm: int):
        """Return ``(highs, lows, closes)`` lists, or ``None`` if data is insufficient."""
        if self.fuzzy_columns is None:
            if df is None or df.empty or len(df) < confirm + MIN_EXTRA_BARS:
                return None
            cols = ("High", "Low", "Close")
        else:
            cols = tuple(_find_col(df, self.fuzzy_columns[k]) for k in ("High", "Low", "Close"))
            if not all(cols) or len(df) < confirm + MIN_EXTRA_BARS:
                return None
        return tuple(pd.to_numeric(df[c], errors="coerce").fillna(0).tolist() for c in cols)

    def compute(self, df: pd.DataFrame, confirm: int = DARVAS_CONFIRM) -> dict:
        """Darvas box for ``df`` (columns High/Low/Close, oldest row first)."""
        data = self.extract(df, confirm)
        if data is None:
            return {"signal": "INSUFFICIENT_DATA", "box_top": None, "box_bottom": None}
        all_highs, all_lows, all_closes = data
        current = all_closes[-1]
        box_top, box_bottom = locate_box(all_highs[:-1], all_lows[:-1], confirm)  # current bar excluded
        d = self.decimals
        if box_top is None:
            out = {"signal": "NO_BOX", "box_top": None, "box_bottom": None}
            if self.current_on_no_top == "raw":
                out["current_price"] = current
            elif self.current_on_no_top == "rounded":
                out["current_price"] = round(current, d)
            return out
        if box_bottom is None:
            out = {"signal": "NO_BOX", "box_top": round(box_top, d), "box_bottom": None}
            if self.current_on_no_bottom == "rounded":
                out["current_price"] = round(current, d)
            return out
        box_range = box_top - box_bottom
        upside = ((box_top - current) / current * 100) if current else 0
        pos = ((current - box_bottom) / box_range * 100) if box_range else 0
        values = {
            "signal": classify(current, box_top, box_bottom),
            "box_top": round(box_top, d),
            "box_bottom": round(box_bottom, d),
            "current_price": round(current, d),
            "box_range": round(box_range, d),
            "upside_to_top_pct": round(upside, 2), "upside_pct": round(upside, 2),
            "position_in_box_pct": round(pos, 1), "pos_in_box": round(pos, 1),
            "data_points": len(all_closes),
        }
        return {k: values[k] for k in self.keys}


def _find_col(df: pd.DataFrame, candidates: Sequence[str]) -> Optional[str]:
    for cand in candidates:
        match = next((col for col in df.columns if cand.upper() in col.upper()), None)
        if match:
            return match
    return None


PROFILES: Dict[str, DarvasProfile] = {
    "us": DarvasProfile("us", {"High": ["High"], "Low": ["Low"], "Close": ["Close"]},
                        False, 2, "rounded", "rounded", LONG_KEYS),
    "india": DarvasProfile(
        "india",
        {"High": ["High", "CH_TRADE_HIGH_PRICE"], "Low": ["Low", "CH_TRADE_LOW_PRICE"],
         "Close": ["Close", "CH_CLOSING_PRICE"]},
        False, 2, "raw", "rounded", LONG_KEYS_POS_FIRST),
    "japan": DarvasProfile("japan", None, True, 0, "raw", "rounded", SHORT_KEYS),
    "korea": DarvasProfile("korea", None, True, 0, "raw", "rounded", SHORT_KEYS),
    "europe": DarvasProfile("europe", None, True, 2, "omit", "omit", SHORT_KEYS_NO_COUNT),
}


def get_profile(market: str) -> DarvasProfile:
    """Look up the registered profile for ``market`` (KeyError lists the known ones)."""
    try:
        return PROFILES[market]
    except KeyError:
        raise KeyError(f"unknown market {market!r}; registered: {sorted(PROFILES)}") from None


def available_markets() -> List[str]:
    """Registered market names."""
    return sorted(PROFILES)
