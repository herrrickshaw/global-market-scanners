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


def event_market(n=320, k=6, events=(120, 200), jump=0.10, seed=0, start="2023-01-02"):
    """Wide close/volume frames where each stock jumps on `events` days with a volume spike."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="B")
    close, vol = {}, {}
    for j in range(k):
        r = rng.normal(0.0003, 0.004, n)
        v = 1e6 * (1 + 0.05 * rng.standard_normal(n))
        for e in events:
            r[e] = jump * (1 if (j + e) % 2 == 0 else -1)
            v[e] = 8e6
        close[f"S{j}"] = 50 * np.cumprod(1 + r)
        vol[f"S{j}"] = v
    return pd.DataFrame(close, index=idx), pd.DataFrame(vol, index=idx)
