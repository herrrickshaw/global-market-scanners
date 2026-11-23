"""full_us_market_scan.py: symbol discovery (NCDS/FTP/SEC faked), caches, bulk download, Excel writer, main()."""
import json
import os
import sys
import time
import types

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import full_us_market_scan as us  # noqa: E402
from conftest_helpers import make_ohlcv  # noqa: E402

NASDAQ_TXT = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot|ETF|NextShares
AAPL|Apple|Q|N|N|100|N|N
TEST|Test issue|Q|Y|N|100|N|N
QQQ|Etf|G|N|N|100|Y|N
TOOLONG|x|Q|N|N|100|N|N
BRK.B|x|Q|N|N|100|N|N
MSFT|Microsoft|Q|N|N|100|N|N
File Creation Time: 1234|||||||
"""
OTHER_TXT = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot|Test Issue|NASDAQ Symbol
IBM|IBM|N|IBM|N|100|N|IBM
SPY|ETF|P|SPY|Y|100|N|SPY
XYZ|x|A|XYZ|N|100|N|XYZ
"""


def test_parse_nasdaq_file_filters():
    assert us._parse_nasdaq_file(NASDAQ_TXT) == ["AAPL", "MSFT"]                 # test issue, ETF, >5 chars, specials, footer dropped
    assert us._parse_nasdaq_file(OTHER_TXT) == ["IBM", "XYZ"]
    assert us._parse_nasdaq_file(OTHER_TXT, exchange_filter="n") == ["IBM"]
    assert us._parse_nasdaq_file("") == [] and us._parse_nasdaq_file("Symbol|Name\n") == []
    assert us._parse_nasdaq_file("Symbol|Name\n\n|x\nA\n") == ["A"]                # blank rows skipped, short rows tolerated


def test_symbol_cache_roundtrip_and_ttl(monkeypatch, tmp_path):
    monkeypatch.setattr(us, "_SYMBOL_CACHE", tmp_path / "c.json")
    assert us._load_symbol_cache() == (None, None)
    us._save_symbol_cache(["A"], ["B"])
    assert us._load_symbol_cache() == (["A"], ["B"])
    data = json.loads((tmp_path / "c.json").read_text())
    data["ts"] = time.time() - us.SYMBOL_CACHE_TTL - 5
    (tmp_path / "c.json").write_text(json.dumps(data))
    assert us._load_symbol_cache() == (None, None)                                # expired
    (tmp_path / "c.json").write_text("{bad")
    assert us._load_symbol_cache() == (None, None)
    monkeypatch.setattr(us, "_SYMBOL_CACHE", tmp_path / "nodir" / "c.json")
    us._save_symbol_cache(["A"], [])                                              # unwritable path swallowed


def test_ncds_creds(monkeypatch, tmp_path):
    p = tmp_path / "auth.json"
    monkeypatch.setattr(us, "_NCDS_CREDS_PATH", p)
    monkeypatch.setattr(us, "_NCDS_AVAILABLE", False)
    p.write_text('{"a": 1}')
    assert us._load_ncds_creds() is None
    monkeypatch.setattr(us, "_NCDS_AVAILABLE", True)
    assert us._load_ncds_creds() == {"a": 1}
    p.write_text("nope")
    assert us._load_ncds_creds() is None
    p.unlink()
    assert us._load_ncds_creds() is None


class Msg:
    def __init__(self, v, call=False):
        self._v, self._call = v, call

    def value(self):
        return self._v


def test_fetch_symbols_from_ncds(monkeypatch):
    assert us.fetch_symbols_from_ncds.__name__
    monkeypatch.setattr(us, "_load_ncds_creds", lambda: None)
    assert us.fetch_symbols_from_ncds() == ([], [])
    creds = {"oauth": {"endpoint": "e", "client_id": "i", "client_secret": "s"}, "kafka": {"bootstrap_servers": "b"}, "topic": "T"}
    monkeypatch.setattr(us, "_load_ncds_creds", lambda: creds)
    msgs = [[Msg({"schema_name": "SeqDirectoryMessage", "symbol": "aapl", "marketClass": "Q"}),
             Msg({"schema_name": "SeqDirectoryMessage", "symbol": "IBM", "marketClass": "N"}),
             Msg({"schema_name": "SeqDirectoryMessage", "symbol": "BRK.B", "marketClass": "N"}),
             Msg({"schema_name": "SeqDirectoryMessage", "symbol": "", "marketClass": "Q"}),
             Msg({"schema_name": "Other", "symbol": "ZZ", "marketClass": "Q"}),
             Msg("not a dict"),
             Msg({"schema_name": "SeqDirectoryMessage", "symbol": "AAPL", "marketClass": "G"}),
             Msg({"schema_name": "SeqDirectoryMessage", "symbol": "NOMKT", "marketClass": ""})]]
    seen = {}

    class Consumer:
        closed = False

        def consume(self, num_messages, timeout):
            return msgs.pop(0) if msgs else []

        def close(self):
            Consumer.closed = True

    class Client:
        def __init__(self, sec, kafka):
            seen.update(sec=sec, kafka=kafka)

        def ncds_kafka_consumer(self, topic):
            seen["topic"] = topic
            return Consumer()
    monkeypatch.setattr(us, "_NCDSClient", Client, raising=False)
    nasdaq, nyse = us.fetch_symbols_from_ncds()
    assert nasdaq == ["AAPL", "NOMKT"] and nyse == ["IBM"] and Consumer.closed and seen["topic"] == "T"
    assert seen["sec"]["oauth.client.id"] == "i" and seen["kafka"]["bootstrap.servers"] == "b"
    monkeypatch.setattr(us, "_NCDSClient", lambda *a: (_ for _ in ()).throw(RuntimeError("auth")), raising=False)
    assert us.fetch_symbols_from_ncds() == ([], [])


def _resp(text="", status=200, js=None):
    def raise_for_status():
        if status >= 400:
            raise RuntimeError(f"HTTP {status}")
    return types.SimpleNamespace(text=text, raise_for_status=raise_for_status, json=lambda: js)


def test_fetch_nasdaq_file_https_then_http(monkeypatch):
    urls = []

    def get(url, timeout, headers):
        urls.append(url)
        return _resp("", 500) if url.startswith("https") else _resp("DATA")
    monkeypatch.setattr(us.requests, "get", get)
    assert us._fetch_nasdaq_file("x.txt") == "DATA" and urls[0].startswith("https://") and urls[1].startswith("http://")
    monkeypatch.setattr(us.requests, "get", lambda *a, **k: _resp(""))
    assert us._fetch_nasdaq_file("x.txt") == ""                                    # empty body on both -> ""


def test_fetch_all_us_symbols_from_sec(monkeypatch, capsys):
    js = {"data": [[1, "n", "aapl", "Nasdaq"], [2, "n", "IBM", "NYSE"], [3, "n", "SPY", "Arca"], [4, "n", "BRK.B", "NYSE"],
                   [5, "n", "A B", "Nasdaq"], [6, "n", "", "Nasdaq"], [7, "n", "OTC", "OTC"], [8, "n"]]}
    monkeypatch.setattr(us.requests, "get", lambda *a, **k: _resp(js=js))
    assert us.fetch_all_us_symbols_from_sec() == (["AAPL"], ["IBM", "SPY"])
    monkeypatch.setattr(us.requests, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert us.fetch_all_us_symbols_from_sec() == ([], []) and "SEC EDGAR fetch failed" in capsys.readouterr().out


def test_fetch_nasdaq_and_nyse_symbol_chains(monkeypatch, capsys):
    monkeypatch.setattr(us, "_NCDS_AVAILABLE", False)
    monkeypatch.setattr(us, "_ncds_symbol_cache", {})
    texts = {"nasdaqlisted.txt": NASDAQ_TXT, "otherlisted.txt": OTHER_TXT}
    monkeypatch.setattr(us, "_fetch_nasdaq_file", lambda fn: texts.get(fn, ""))
    assert us.fetch_nasdaq_symbols() == ["AAPL", "MSFT"] and us.fetch_nyse_symbols() == ["IBM", "XYZ"]
    texts.clear()
    monkeypatch.setattr(us, "fetch_all_us_symbols_from_sec", lambda: (["SECN"], ["SECY"]))
    assert us.fetch_nasdaq_symbols() == ["SECN"] and us.fetch_nyse_symbols() == ["SECY"]
    assert "using SEC EDGAR fallback" in capsys.readouterr().out
    us._ncds_symbol_cache["nyse"] = ["CACHED"]
    assert us.fetch_nyse_symbols() == ["CACHED"]


def test_fetch_nasdaq_symbols_prefers_ncds(monkeypatch):
    monkeypatch.setattr(us, "_NCDS_AVAILABLE", True)
    monkeypatch.setattr(us, "_ncds_symbol_cache", {})
    monkeypatch.setattr(us, "_load_ncds_creds", lambda: {"x": 1})
    monkeypatch.setattr(us, "fetch_symbols_from_ncds", lambda: (["N1"], ["Y1"]))
    assert us.fetch_nasdaq_symbols() == ["N1"] and us._ncds_symbol_cache == {"nasdaq": ["N1"], "nyse": ["Y1"]}
    assert us.fetch_nyse_symbols() == ["Y1"]
    monkeypatch.setattr(us, "_ncds_symbol_cache", {})
    monkeypatch.setattr(us, "fetch_symbols_from_ncds", lambda: ([], []))
    monkeypatch.setattr(us, "_fetch_nasdaq_file", lambda fn: NASDAQ_TXT)
    assert us.fetch_nasdaq_symbols() == ["AAPL", "MSFT"]                           # NCDS empty -> FTP


def _multi(frames):
    return pd.concat({f: pd.concat({t: d[f] for t, d in frames.items()}, axis=1)
                      for f in ["Open", "High", "Low", "Close", "Volume"]}, axis=1)


def test_bulk_download_ohlc_cache_and_network(monkeypatch, capsys):
    class Cache:
        def get_ohlc_bulk(self, tickers, force):
            return {"C": 1}
    monkeypatch.setattr(us, "_CACHE_OK", True); monkeypatch.setattr(us, "_US_CACHE", Cache())
    assert us.bulk_download_ohlc(["A"]) == {"C": 1}
    monkeypatch.setattr(us, "_CACHE_OK", False)
    monkeypatch.setattr(us, "BATCH_SIZE", 2)
    monkeypatch.setattr(us.time, "sleep", lambda s: None)
    d = make_ohlcv(30)
    calls = []

    def dl(batch, **kw):
        calls.append(list(batch))
        if len(calls) == 3:
            return pd.DataFrame()
        return _multi({t: d for t in batch if t != "GONE"})
    monkeypatch.setattr(us.yf, "download", dl)
    out = us.bulk_download_ohlc(["A", "GONE", "B", "C", "E"])
    assert set(out) == {"A", "B", "C"} and [len(c) for c in calls] == [2, 2, 1]
    assert "empty" in capsys.readouterr().out
    short = make_ohlcv(7)
    monkeypatch.setattr(us.yf, "download", lambda b, **k: _multi({t: short for t in b}))
    assert us.bulk_download_ohlc(["A"]) == {}                                      # fewer than DARVAS_CONFIRM+5 bars -> unusable
    flat = make_ohlcv(40)
    monkeypatch.setattr(us.yf, "download", lambda b, **k: flat)
    assert list(us.bulk_download_ohlc(["Q"])) == ["Q"]
    sleeps = []
    monkeypatch.setattr(us.time, "sleep", lambda s: sleeps.append(s))
    n = []

    def dl2(b, **k):
        n.append(1)
        if len(n) == 1:
            raise RuntimeError("429 Rate")
        if len(n) == 2:
            raise ValueError("boom")
        return flat
    monkeypatch.setattr(us.yf, "download", dl2)
    assert us.bulk_download_ohlc(["Q"]) == {} and sleeps == [30] and "ERROR — boom" in capsys.readouterr().out


def test_save_excel_sheets(monkeypatch, tmp_path):
    monkeypatch.setattr(us, "DOWNLOAD_DIR", tmp_path)
    written = {}

    class W:
        def __init__(self, path, engine):
            written["path"], written["engine"] = path, engine

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(us.pd, "ExcelWriter", W)
    monkeypatch.setattr(pd.DataFrame, "to_excel", lambda self, w, sheet_name, index: written.setdefault("sheets", {}).__setitem__(sheet_name, self.copy()))
    all_rows = [{"Symbol": "A", "Change%": 1.0, "GC_Signal": "GOLDEN_CROSS", "DMA_Gap%": 2.0}, {"Symbol": "B", "Change%": 5.0, "GC_Signal": "BELOW_200DMA", "DMA_Gap%": -1}]
    fund = [{"Symbol": "A", "Piotroski_Score": 8, "Piotroski_Strong": "YES", "CoffeeCan": "PASS", "MagicFormula": "FAIL", "BullCartel": "PASS",
             "Revenue_CAGR_%": 12, "ROIC_%": 1, "Profit_Growth_YoY_%": 30},
            {"Symbol": "B", "Piotroski_Score": 3, "Piotroski_Strong": "NO", "CoffeeCan": "FAIL", "MagicFormula": "PASS", "BullCartel": "FAIL",
             "Revenue_CAGR_%": 1, "ROIC_%": 40, "Profit_Growth_YoY_%": 0}]
    ml = {"A": {"ML_Direction": "BULLISH", "ML_Pred_Ret%": 2.0}, "B": {"ML_Direction": "BEARISH", "ML_Pred_Ret%": -2.0}, "C": {"ML_Direction": "NEUTRAL", "ML_Pred_Ret%": 0}}
    path = us.save_excel(all_rows, [], fund, [], None, tag="us", ml_signal_map=ml)
    sh = written["sheets"]
    assert written["engine"] == "openpyxl" and path.parent == tmp_path and path.name.startswith("us_full_scan_")
    assert list(sh["All_Stocks"]["Symbol"]) == ["B", "A"]                          # sorted by Change% desc
    assert sh["Darvas_Signals"].empty and sh["Triple_Hits"].empty and sh["Multi_Screen_Hits"].empty
    assert list(sh["Piotroski_Strong"]["Symbol"]) == ["A"] and list(sh["Coffee_Can"]["Symbol"]) == ["A"]
    assert list(sh["Magic_Formula"]["Symbol"]) == ["B"] and list(sh["Bull_Cartel"]["Symbol"]) == ["A"]
    assert list(sh["Golden_Crossover"]["Symbol"]) == ["A"] and list(sh["ML_Bullish"]["Symbol"]) == ["A"] and list(sh["ML_Bearish"]["Symbol"]) == ["B"]
    assert list(sh["All_Fundamentals"]["Symbol"]) == ["A", "B"]                    # sorted by Piotroski desc
    written.clear()
    us.save_excel([], [], [], [])
    assert "ML_Bullish" not in written["sheets"] and written["sheets"]["All_Stocks"].empty


def _setup_main(monkeypatch, tmp_path, symbols, ohlc, fund_fn, ml=None):
    monkeypatch.setattr(us, "_SYMBOL_CACHE", tmp_path / "s.json")
    monkeypatch.setattr(us, "_NSE_OK", False)
    monkeypatch.setattr(us, "fetch_nasdaq_symbols", lambda: list(symbols))
    monkeypatch.setattr(us, "fetch_nyse_symbols", lambda: ["NYSE1"])
    monkeypatch.setattr(us, "bulk_download_ohlc", lambda syms, period: {s: ohlc[s] for s in syms if s in ohlc})
    monkeypatch.setattr(us, "fundamental_scan", fund_fn)
    saved = {}
    monkeypatch.setattr(us, "save_excel", lambda *a, **k: (saved.update(args=a, kw=k), tmp_path / "out.xlsx")[1])
    monkeypatch.setattr(us, "_ML_OK", ml is not None)
    if ml is not None:
        monkeypatch.setattr(us, "_ML_ENGINE", ml)
    return saved


def _uptrend(n=260, seed=1):
    return make_ohlcv(n, drift=0.003, vol=0.004, seed=seed, base=20.0)


def test_main_end_to_end(monkeypatch, tmp_path, capsys):
    ohlc = {"AAA": _uptrend(), "BBB": _uptrend(seed=2), "PENNY": make_ohlcv(260, base=0.5, vol=0.0, drift=0.0)}
    good = {"name": "Good Co", "sector": "Tech", "f_score": 8, "f_strong": True, "qualifies_cc": True, "cc_score": "6/6", "qualifies_mf": True,
            "qualifies_bc": True, "Revenue_CAGR_%": 12.0}
    bad = {"name": "Bad Co", "sector": "X", "f_score": 2, "f_strong": False, "qualifies_cc": False, "cc_score": "1/6", "qualifies_mf": False,
           "qualifies_bc": False, "error": ""}

    def fund(sym):
        if sym == "BBB":
            raise RuntimeError("boom")
        return good if sym == "AAA" else bad

    class ML:
        def predict_batch(self, data, max_workers):
            return pd.DataFrame({"symbol": list(data), "direction": ["BULLISH"] + ["NEUTRAL"] * (len(data) - 1),
                                 "predicted_ret%": 1.0, "confidence": 0.5, "train_rmse": 0.1})
    saved = _setup_main(monkeypatch, tmp_path, ["AAA", "BBB", "PENNY"], ohlc, fund, ml=ML())
    res = us.main(top=0, workers=2, min_price=1.0)
    out = capsys.readouterr().out
    all_rows, darvas, fund_rows, triple, six = saved["args"][:5]
    assert [r["Symbol"] for r in all_rows] == ["AAA", "BBB"]                       # PENNY filtered by min_price
    assert saved["kw"]["tag"] == "us" and set(saved["kw"]["ml_signal_map"]) == {"AAA", "BBB", "PENNY"}      # ML runs on the full OHLC set
    assert res["total_scanned"] == 2 and res["excel_path"].endswith("out.xlsx") and res["gc_count"] >= 0
    assert [r["Symbol"] for r in fund_rows] == ["AAA"] and fund_rows[0]["CoffeeCan"] == "PASS" and fund_rows[0]["Piotroski_Strong"] == "YES"
    assert "BBB: error — boom" in out and "Stage 4b" in out and "SCAN COMPLETE" in out and "Symbol cache" not in out
    assert all_rows[0]["ML_Direction"] == "BULLISH"
    assert res["breakout_count"] == sum(1 for r in all_rows if r["Darvas_Signal"] == "BREAKOUT_BUY")
    # symbol cache was written; second run hits it, honours --top / nasdaq_only
    capsys.readouterr()
    _setup_main(monkeypatch, tmp_path, ["NEVER"], ohlc, fund)
    us.main(nasdaq_only=True, top=1, run_scans=False)
    out = capsys.readouterr().out
    assert "Symbol cache hit: 3 NASDAQ + 1 NYSE/other" in out and "limited to first 1 symbols" in out and "Stage 4 — Skipped" in out


def test_main_multi_screen_and_triple(monkeypatch, tmp_path, capsys):
    ohlc = {"AAA": _uptrend()}
    res_fn = lambda sym: {"name": "G", "sector": "T", "f_score": 9, "f_strong": True, "qualifies_cc": True, "cc_score": "6/6",
                          "qualifies_mf": True, "qualifies_bc": True, "error": ""}
    saved = _setup_main(monkeypatch, tmp_path, ["AAA"], ohlc, res_fn)
    monkeypatch.setattr(us, "compute_darvas_box", lambda df: {"signal": "BREAKOUT_BUY", "box_top": 10, "box_bottom": 5, "upside_to_top_pct": 1,
                                                              "position_in_box_pct": 2, "data_points": 260})
    r = us.main(run_scans=True, workers=1)
    out = capsys.readouterr().out
    assert r["triple_hits"] and r["six_screen_hits"][0]["Screens_Passed"] >= 5 and "Top Multi-Screen Hits" in out
    assert r["six_screen_hits"][0].get("ML_Confirmed") is None                        # ML disabled


def test_parse_symbol_length_boundary():
    text = "Symbol|Name\nABCDE|x\nABCDEF|x\n"
    assert us._parse_nasdaq_file(text) == ["ABCDE"]                                 # 5 chars allowed, 6 dropped


def test_symbol_cache_exact_ttl_boundary(monkeypatch, tmp_path):
    monkeypatch.setattr(us, "_SYMBOL_CACHE", tmp_path / "c.json")
    (tmp_path / "c.json").write_text(json.dumps({"ts": 1000.0, "nasdaq": ["A"], "nyse": []}))
    monkeypatch.setattr(us.time, "time", lambda: 1000.0 + us.SYMBOL_CACHE_TTL)
    assert us._load_symbol_cache() == (None, None)                                  # age == TTL is already expired
    monkeypatch.setattr(us.time, "time", lambda: 1000.0 + us.SYMBOL_CACHE_TTL - 1)
    assert us._load_symbol_cache() == (["A"], [])


def test_main_price_floor_is_inclusive_and_three_screens_is_a_hit(monkeypatch, tmp_path, capsys):
    idx = pd.bdate_range(end="2026-11-20", periods=260)
    flat = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 1e6}, index=idx)
    up = _uptrend()
    fund = lambda sym: {"name": "G", "sector": "T", "f_score": 8, "f_strong": True, "qualifies_cc": True, "cc_score": "6/6",
                        "qualifies_mf": False, "qualifies_bc": False, "error": ""}
    saved = _setup_main(monkeypatch, tmp_path, ["FLAT", "UP"], {"FLAT": flat, "UP": up}, fund)
    monkeypatch.setattr(us, "compute_darvas_box", lambda df: {"signal": "IN_BOX", "box_top": 5, "box_bottom": 1})
    r = us.main(min_price=1.0, workers=1)
    assert r["total_scanned"] == 2                                                   # LTP == min_price is kept
    six = saved["args"][4]
    flat_hit = [x for x in six if x["Symbol"] == "FLAT"]
    # FLAT: darvas not a breakout, GC state BELOW/ABOVE...: exactly Piotroski + CoffeeCan (+ maybe above-200DMA)
    assert all(x["Screens_Passed"] >= 3 for x in six) and {x["Symbol"] for x in six} == {"UP"} | ({"FLAT"} if flat_hit else set())
    up_hit = next(x for x in six if x["Symbol"] == "UP")
    assert up_hit["Screens_Passed"] == 3                                             # above-200DMA + Piotroski + CC: threshold is >= 3
