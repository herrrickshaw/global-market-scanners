"""Characterisation of ``full_indian_market_scan.main`` with every network/IO edge faked."""
from __future__ import annotations

import io
import json
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_us_main import _ohlc  # noqa: E402

import full_indian_market_scan as mod  # noqa: E402

GOLDEN = Path(__file__).parent / "golden_india_main.json"


def _fund(sym, suffix):
    if sym == "DDD":
        raise RuntimeError("fund boom")
    return {"error": "", "f_score": 8, "f_strong": sym != "CCC", "qualifies_cc": sym != "CCC",
            "cc_score": "5/5", "qualifies_mf": sym == "AAA", "qualifies_bc": sym != "BBB",
            "Revenue_CAGR_%": 12.5, "ROCE_avg_%": 18.0, "ROIC_%": 30.0, "Earnings_Yield_%": 16.0,
            "Sales_Growth_YoY_%": 20.0, "Profit_Growth_YoY_%": 30.0, "Net_Profit_Cr": 5.0}


def run(monkeypatch, scans=True, nse_only=False, top=0):
    saved = {}
    monkeypatch.setattr(mod, "_USE_NSE_FETCHER", False)
    monkeypatch.setattr(mod, "_load_symbol_cache", lambda: (["AAA", "BBB", "CCC"], ["DDD", "PENNY"]))
    data = {"AAA.NS": "AAA", "BBB.NS": "BBB", "CCC.NS": "CCC", "DDD.BO": "DDD", "NAN.NS": "NAN"}
    src = _ohlc()
    monkeypatch.setattr(mod, "bulk_download_ohlc",
                        lambda tk, period="1y": {k: src[v] for k, v in data.items() if k in tk or True})
    monkeypatch.setattr(mod, "fundamental_scan", _fund)

    def fake_save(*args, **kw):
        saved["args"] = json.loads(json.dumps([list(a) for a in args], default=str))
        saved["kw"] = json.loads(json.dumps(kw, default=str))
        return Path("out.xlsx")
    monkeypatch.setattr(mod, "save_excel", fake_save)
    buf = io.StringIO()
    with redirect_stdout(buf):
        ret = mod.main(nse_only=nse_only, top=top, run_scans=scans, workers=1)
    text = re.sub(r"\d{2} \w{3} \d{4}\s+\d{2}:\d{2}:\d{2}", "<TS>", buf.getvalue())
    return {"stdout": text, "ret": json.loads(json.dumps(ret, default=str)), "saved": saved}


def collect(monkeypatch):
    return {"scans": run(monkeypatch), "noscans": run(monkeypatch, scans=False),
            "nse_only": run(monkeypatch, nse_only=True, top=3)}


def test_main_matches_golden(monkeypatch):
    got = collect(monkeypatch)
    golden = json.loads(GOLDEN.read_text())
    for k in golden:
        assert json.dumps(got[k]) == json.dumps(golden[k]), k
