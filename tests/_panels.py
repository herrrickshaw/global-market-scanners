"""Deterministic synthetic OHLC panels used to pin Darvas-box behaviour (no network)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _walk(seed: int, n: int, drift: float = 0.0, vol: float = 0.02) -> pd.DataFrame:
    rs = np.random.RandomState(seed)
    close = 100 * np.exp(np.cumsum(rs.normal(drift, vol, n)))
    high = close * (1 + np.abs(rs.normal(0, 0.01, n)))
    low = close * (1 - np.abs(rs.normal(0, 0.01, n)))
    return pd.DataFrame({"High": high, "Low": low, "Close": close})


def build_panels() -> dict:
    """Name -> DataFrame covering random walks, trends, flats, gaps, zeros and short series."""
    panels = {}
    for seed in range(40):
        panels[f"walk_{seed}"] = _walk(seed, 60 + seed * 3, drift=0.002 * ((seed % 5) - 2))
    up = pd.DataFrame({"High": np.arange(1, 41) + 0.5, "Low": np.arange(1, 41) - 0.5,
                       "Close": np.arange(1, 41, dtype=float)})
    panels["monotone_up"] = up
    panels["monotone_down"] = up.iloc[::-1].reset_index(drop=True)
    panels["flat"] = pd.DataFrame({"High": [10.0] * 30, "Low": [10.0] * 30, "Close": [10.0] * 30})
    panels["short"] = _walk(1, 6)
    panels["empty"] = pd.DataFrame({"High": [], "Low": [], "Close": []})
    z = _walk(7, 50)
    z.loc[10:14, ["High", "Low"]] = 0.0
    z.loc[20, "Close"] = np.nan
    panels["zeros_nan"] = z
    # classic box then breakout on the final bar
    hi = [10, 11, 12, 13, 12, 11.5, 11.8, 12.2, 11.9, 12.1, 12.0, 11.7, 14.0]
    lo = [9, 10, 11, 12, 11, 10.5, 10.8, 11.2, 10.9, 11.1, 11.0, 10.7, 13.0]
    cl = [9.5, 10.5, 11.5, 12.5, 11.5, 11.0, 11.4, 11.7, 11.5, 11.6, 11.5, 11.2, 13.8]
    panels["breakout"] = pd.DataFrame({"High": hi * 2, "Low": lo * 2, "Close": cl * 2})
    nse = _walk(3, 70).rename(columns={"High": "CH_TRADE_HIGH_PRICE", "Low": "CH_TRADE_LOW_PRICE",
                                       "Close": "CH_CLOSING_PRICE"})
    panels["nse_columns"] = nse
    adj = _walk(4, 70).rename(columns={"Close": "Adj Close"})
    panels["adj_close_only"] = adj
    return panels
