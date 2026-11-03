"""Characterisation of ``NSEDataFetcher.get_regime`` with all data sources faked."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

import nse_data_fetcher as mod

GOLDEN = Path(__file__).parent / "golden_regime.json"


def _idx(n=260, slope=0.1, start=100.0):
    close = [start + slope * i for i in range(n)]
    return pd.DataFrame({"Close": close})


def _fii(net=5e7):
    return pd.DataFrame({"FII Net": [net] * 6, "DII Net": [1.0] * 6})


def _run(monkeypatch, *, ok=True, quote=None, hist=None, vix=15.0, fii=None, boom=()):
    monkeypatch.setattr(mod, "_NSEPY_OK", ok)
    def q(_name):
        if "quote" in boom:
            raise RuntimeError("x")
        return quote
    monkeypatch.setattr(mod, "nse_get_index_quote", q, raising=False)
    def dl(*a, **k):
        if "dl" in boom:
            raise RuntimeError("x")
        return hist
    monkeypatch.setattr(mod.yf, "download", dl)
    def iv():
        if "vix" in boom:
            raise RuntimeError("x")
        return vix
    monkeypatch.setattr(mod, "indiavix", iv, raising=False)
    def fd():
        if "fii" in boom:
            raise RuntimeError("x")
        return fii
    monkeypatch.setattr(mod, "nse_fiidii", fd, raising=False)
    res = mod.NSEDataFetcher().get_regime()
    res["timestamp"] = "<TS>"
    return json.loads(json.dumps(res, default=str))


def collect(monkeypatch):
    quote = {"last": 22000, "yearHigh": 23000, "yearLow": 18000}
    up, down = _idx(), _idx(slope=-0.1, start=200.0)
    flat = _idx(slope=0.0)
    return {
        "bull": _run(monkeypatch, quote=quote, hist=up, fii=_fii()),
        "bull_vol": _run(monkeypatch, quote=quote, hist=up, vix=20.0, fii=_fii(-5e7)),
        "bear": _run(monkeypatch, quote=quote, hist=down, fii=_fii(-5.0)),
        "bear_extreme": _run(monkeypatch, quote=quote, hist=down, vix=30.0, fii=_fii()),
        "sideways": _run(monkeypatch, quote=None, hist=flat, vix=None, fii=pd.DataFrame()),
        "no_nsepy": _run(monkeypatch, ok=False, hist=up),
        "empty_hist": _run(monkeypatch, quote=quote, hist=pd.DataFrame(), fii=None),
        "multiindex": _run(monkeypatch, quote=quote,
                           hist=pd.concat({"Close": up["Close"]}, axis=1).T.T.set_axis(
                               pd.MultiIndex.from_tuples([("Close", "^NSEI")]), axis=1), fii=_fii()),
        "all_boom": _run(monkeypatch, quote=quote, hist=up, fii=_fii(), boom=("quote", "dl", "vix", "fii")),
    }


def test_get_regime_matches_golden(monkeypatch):
    assert json.dumps(collect(monkeypatch)) == json.dumps(json.loads(GOLDEN.read_text()))
