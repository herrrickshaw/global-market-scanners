"""Shared synthetic-data helpers for the offline tests (imported, not collected)."""
import numpy as np
import pandas as pd


def make_ohlcv(n=700, drift=0.0005, vol=0.012, seed=0, base=100.0, volume=1e6, start="2020-01-01"):
    rng = np.random.default_rng(seed)
    c = base * np.cumprod(1 + drift + vol * rng.standard_normal(n))
    idx = pd.date_range(start, periods=n, freq="B")
    spread = 0.01 * c
    v = volume * (1 + 0.2 * rng.standard_normal(n)).clip(0.2)
    return pd.DataFrame({"Open": c, "High": c + spread, "Low": c - spread, "Close": c, "Volume": v}, index=idx)
