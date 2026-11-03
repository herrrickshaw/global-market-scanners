"""Characterisation of every market's ``fundamental_scan`` on synthetic tickers (no network).

``golden_fundamental.json`` was captured from the pre-split code; results (values and key
order) must not change.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fundamental_fixtures as fx  # noqa: E402

GOLDEN_PATH = Path(__file__).parent / "golden_fundamental.json"
CALLS = {
    "us": ("full_us_market_scan", lambda f: f("AAPL")),
    "india": ("full_indian_market_scan", lambda f: f("TCS", ".NS")),
    "europe": ("full_european_market_scan", lambda f: f("ADS.DE")),
    "korea": ("full_korea_market_scan", lambda f: f("005930", ".KS")),
    "japan": ("full_japan_market_scan", lambda f: f("7203.T")),
}


def run_market(market, monkeypatch=None):
    modname, call = CALLS[market]
    mod = importlib.import_module(modname)
    out = {}
    orig = mod.yf.Ticker
    try:
        for name in fx.variants():
            mod.yf.Ticker = lambda *_a, _n=name, **_k: fx.build(_n)
            out[name] = json.loads(fx.ser(call(mod.fundamental_scan)))
        mod.yf.Ticker = fx.RaisingTicker
        out["ctor_raises"] = json.loads(fx.ser(call(mod.fundamental_scan)))
    finally:
        mod.yf.Ticker = orig
    return out


def run_all():
    return {m: run_market(m) for m in CALLS}


@pytest.mark.parametrize("market", sorted(CALLS))
def test_fundamental_scan_matches_golden(market):
    golden = json.loads(GOLDEN_PATH.read_text())[market]
    got = run_market(market)
    for name in golden:
        assert json.dumps(got[name]) == json.dumps(golden[name]), f"{market}/{name}"
