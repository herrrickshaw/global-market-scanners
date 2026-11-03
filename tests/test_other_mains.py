"""Characterisation of the Korea / Japan / Europe ``main()`` pipelines (CLI args, stdout,
Excel workbook contents) with every network edge faked and output redirected to a temp dir."""
from __future__ import annotations

import io
import json
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _panels import _walk, build_panels  # noqa: E402

import full_european_market_scan as eu  # noqa: E402
import full_japan_market_scan as jp  # noqa: E402
import full_korea_market_scan as kr  # noqa: E402

GOLDEN = Path(__file__).parent / "golden_other_mains.json"


def _frames(n_names):
    out = {}
    for i, name in enumerate(n_names):
        df = _walk(20 + i, 90, drift=0.002 * (i - 3))
        if i % 3 != 2:   # classic box + final-bar breakout, scaled per name
            df = build_panels()["breakout"] * (1 + 0.1 * i)
        if name.startswith("SHORT"):
            df = df.iloc[:10]
        out[name] = df
    return out


def _fund_res(key, strong=True):
    return {"f_score": 8 if strong else 3, "f_strong": strong, "cc_qualifies": "PASS" if strong else "FAIL",
            "cc_score": "6/6", "cc_rev_cagr": 12.0, "cc_roce_avg": 20.0, "cc_debt_equity": 0.3,
            "market_cap_b_krw": 5.0, "market_cap_b_jpy": 7.0, "market_cap_b": 9.0, "pe_ratio": 11.0,
            "pb_ratio": 1.2, "dividend_yield": 2.0, "pegy_ratio": 1.1, "error": ""}


def _run(mod, argv, tmp_path, monkeypatch, patches):
    monkeypatch.setattr(mod, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(mod, "MAX_WORKERS", 1)   # deterministic completion order
    for name, val in patches.items():
        monkeypatch.setattr(mod, name, val)
    monkeypatch.setattr(sys, "argv", ["scan"] + argv)
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            mod.main()
        exit_msg = None
    except SystemExit as e:
        exit_msg = str(e)
    text = buf.getvalue().replace(str(tmp_path), "<DIR>")
    text = re.sub(r"\d{2} \w{3} \d{4}\s+\d{2}:\d{2}:\d{2}", "<TS>", text)
    text = re.sub(r"\d{8}_\d{4}", "<DATE>", text)
    sheets = {}
    for f in sorted(tmp_path.glob("*.xlsx")):
        book = pd.read_excel(f, sheet_name=None)
        sheets = {k: json.loads(v.to_json(orient="split")) for k, v in book.items()}
        f.unlink()
    return {"stdout": text, "exit": exit_msg, "sheets": sheets}


def _kr(argv, tmp_path, monkeypatch, cap=200):
    uni = [{"code": f"00{i}", "name": f"K{i}", "market": "KOSPI" if i % 2 else "KOSDAQ",
            "yf_suffix": ".KS" if i % 2 else ".KQ"} for i in range(6)]
    data = _frames([u["code"] for u in uni])
    data.pop("005", None)
    def fund(code, sfx):
        if code == "003":
            raise RuntimeError("boom")
        return _fund_res(code, strong=code != "001")
    return _run(kr, argv, tmp_path, monkeypatch, {
        "build_krx_universe": lambda kospi_only=False: [u for u in uni if not kospi_only or u["market"] == "KOSPI"],
        "fetch_krx_ohlc": lambda universe, months=3: data,
        "fundamental_scan": fund, "MAX_FUND_CANDIDATES": cap})


def _jp(argv, tmp_path, monkeypatch, cap=200):
    uni = [{"yf_ticker": f"{7000+i}.T", "code": str(7000 + i), "name": f"J{i}", "sector": "S"}
           for i in range(6)]
    data = _frames([u["yf_ticker"] for u in uni])
    data["9999.T"] = data["7005.T"].copy()   # not in meta
    def fund(tkr):
        if tkr == "7003.T":
            raise RuntimeError("boom")
        return _fund_res(tkr, strong=tkr != "7001.T")
    return _run(jp, argv, tmp_path, monkeypatch, {
        "build_ticker_universe": lambda: uni, "bulk_download_ohlc": lambda t, period="3mo": data,
        "fundamental_scan": fund, "MAX_FUND_CANDIDATES": cap})


def _eu(argv, tmp_path, monkeypatch, fail=False):
    syms = list(eu.EURO_STOXX_50_META)[:6]
    frames = _frames(syms + ["SHORT"])
    raw = pd.concat({s: frames[s] for s in syms[:5]}, axis=1)
    def fund(sym):
        if sym == syms[3]:
            raise RuntimeError("boom")
        return _fund_res(sym, strong=sym != syms[1])
    def dl(*a, **k):
        if fail:
            raise RuntimeError("offline")
        return raw
    monkeypatch.setattr(eu.yf, "download", dl)
    return _run(eu, argv, tmp_path, monkeypatch, {"fundamental_scan": fund})


def collect(monkeypatch, tmp_path):
    out = {}
    for name, fn, argv, kw in [
        ("kr_all", _kr, [], {}), ("kr_kospi_top", _kr, ["--kospi-only", "--top", "4"], {}),
        ("kr_noscans", _kr, ["--no-scans"], {}), ("kr_cap", _kr, ["--workers", "1"], {"cap": 1}),
        ("jp_all", _jp, [], {}), ("jp_top", _jp, ["--top", "4", "--workers", "1"], {}),
        ("jp_noscans", _jp, ["--no-scans"], {}), ("jp_cap", _jp, [], {"cap": 1}),
        ("eu_all", _eu, [], {}), ("eu_top", _eu, ["--top", "3"], {}),
        ("eu_noscans", _eu, ["--no-scans"], {}), ("eu_fail", _eu, [], {"fail": True}),
    ]:
        d = tmp_path / name
        d.mkdir()
        out[name] = fn(argv, d, monkeypatch, **kw)
    return out


def test_mains_match_golden(monkeypatch, tmp_path):
    got = collect(monkeypatch, tmp_path)
    golden = json.loads(GOLDEN.read_text())
    for k in golden:
        assert json.dumps(got[k]) == json.dumps(golden[k]), k
