"""Characterisation of ``full_us_market_scan.main`` with every network/IO edge faked."""
from __future__ import annotations

import io
import json
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _panels import _walk  # noqa: E402

import full_us_market_scan as mod  # noqa: E402

GOLDEN = Path(__file__).parent / "golden_us_main.json"


def _ohlc():
    out = {}
    for i, sym in enumerate(["AAA", "BBB", "CCC", "DDD", "PENNY", "NAN"]):
        df = _walk(10 + i, 260, drift=0.001 * (i - 2))
        if sym == "PENNY":
            df = df * 0.001
        if sym == "NAN":
            df["Close"] = float("nan")
        out[sym] = df
    return out


def _fund(sym):
    base = {"name": sym + " Inc", "sector": "Tech", "error": "", "f_score": 8, "f_strong": True,
            "qualifies_cc": True, "cc_score": "6/6", "qualifies_mf": sym != "BBB",
            "qualifies_bc": sym == "AAA", "Revenue_CAGR_%": 12.5, "ROE_avg_%": 18.0,
            "ROIC_%": 30.0, "Earnings_Yield_%": 16.0, "Sales_Growth_YoY_%": 20.0,
            "Profit_Growth_YoY_%": 30.0, "Net_Profit_$M": 5.0}
    if sym == "CCC":
        base.update(f_strong=False, qualifies_cc=False, qualifies_mf=False)
    if sym == "DDD":
        raise RuntimeError("fund boom")
    return base


class _Engine:
    def predict_batch(self, data, max_workers=1):
        syms = sorted(data)
        return pd.DataFrame({"symbol": syms, "direction": ["BULLISH", "BEARISH", "NEUTRAL"] * 2,
                             "predicted_ret%": range(len(syms)), "confidence": [0.5] * len(syms),
                             "train_rmse": [0.1] * len(syms)})


def run(monkeypatch, scans=True, ml=False, nasdaq_only=False, top=0):
    saved = {}
    monkeypatch.setattr(mod, "_NSE_OK", False)
    monkeypatch.setattr(mod, "_CACHE_OK", False)
    monkeypatch.setattr(mod, "_ML_OK", ml)
    monkeypatch.setattr(mod, "_ML_ENGINE", _Engine())
    monkeypatch.setattr(mod, "_load_symbol_cache", lambda: (["AAA", "BBB", "AAA"], ["CCC", "DDD"]))
    monkeypatch.setattr(mod, "bulk_download_ohlc", lambda syms, period="1y": _ohlc())
    monkeypatch.setattr(mod, "fundamental_scan", _fund)

    def fake_save(*args, **kw):
        saved["args"] = json.loads(json.dumps([list(a) if not isinstance(a, dict) else a
                                               for a in args], default=str))
        saved["kw"] = json.loads(json.dumps(kw, default=str))
        return Path("out.xlsx")
    monkeypatch.setattr(mod, "save_excel", fake_save)
    buf = io.StringIO()
    with redirect_stdout(buf):
        ret = mod.main(nasdaq_only=nasdaq_only, top=top, run_scans=scans, workers=1)
    text = re.sub(r"\d{2} \w{3} \d{4}\s+\d{2}:\d{2}:\d{2}", "<TS>", buf.getvalue())
    return {"stdout": text, "ret": json.loads(json.dumps(ret, default=str)), "saved": saved}


def collect(monkeypatch):
    return {"scans": run(monkeypatch), "ml": run(monkeypatch, ml=True),
            "noscans": run(monkeypatch, scans=False), "top": run(monkeypatch, top=2, nasdaq_only=True)}


def test_main_matches_golden(monkeypatch):
    got = collect(monkeypatch)
    golden = json.loads(GOLDEN.read_text())
    for k in golden:
        assert json.dumps(got[k], sort_keys=False) == json.dumps(golden[k], sort_keys=False), k
