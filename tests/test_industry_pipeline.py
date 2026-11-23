"""build_industry_parquet.py + enrich_industries.py: company/industry dataset build (all I/O faked)."""
import json
import os
import sys
import types

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import build_industry_parquet as bp  # noqa: E402
import enrich_industries as ei  # noqa: E402


# ───────────────────────────── build_industry_parquet ─────────────────────────────
def test_latest_returns_last_sorted_or_none(monkeypatch):
    monkeypatch.setattr(bp.glob, "glob", lambda p, recursive=False: ["/b", "/a", "/c"])
    assert bp.latest("x") == "/c"
    monkeypatch.setattr(bp.glob, "glob", lambda p, recursive=False: [])
    assert bp.latest("x") is None


def _scan_frames():
    return {
        "USA": pd.DataFrame({"Symbol": ["AAPL", "ZZZ", " "]}),
        "India": pd.DataFrame({"Symbol": ["TCS", "RELIANCE", "FOO"], "Suffix": [".NS", ".BO", ".XX"]}),
        "Japan": pd.DataFrame({"Code": ["7203"], "YF_Ticker": ["7203.T"], "Name": ["Toyota"], "Sector": ["Autos"]}),
        "South Korea": pd.DataFrame({"Code": ["005930", "000660"], "YF_Ticker": ["005930.KS", "000660.KS"],
                                     "Name": ["Samsung", None], "Market": ["KOSPI", ""]}),
        "Europe": pd.DataFrame({"Symbol": ["ADS.DE"], "Name": [" Adidas "], "Sector": [None]}),
    }


@pytest.fixture
def scans(monkeypatch):
    frames = _scan_frames()
    paths = {pat: f"/dl/{c}.xlsx" for c, pat in bp.SCANS.items()}
    monkeypatch.setattr(bp, "latest", lambda pat: None if pat == bp.SCANS["Europe"] and False else
                        (paths.get(pat) if pat != bp.SCAN_SUMMARY else "/dl/summary.xlsx"))

    class XL:
        def __init__(self, path):
            self.path = path

        def parse(self, sheet):
            assert sheet == "All_Stocks"
            country = os.path.basename(self.path).split(".")[0]
            return frames[country]

    monkeypatch.setattr(bp.pd, "ExcelFile", XL)
    summary = pd.DataFrame({"symbol": ["AAPL", "", "OTHER"], "name": ["Apple Inc", "x", "o"],
                            "exchange": ["NASDAQ", None, None], "sector": ["Tech", None, None],
                            "industry": ["Consumer Electronics", None, None]})
    monkeypatch.setattr(bp.pd, "read_excel", lambda f, sheet_name=0: summary)
    return frames


def test_load_us_industry_map(scans):
    m = bp.load_us_industry_map()
    assert m["AAPL"] == ("Apple Inc", "NASDAQ", "Tech", "Consumer Electronics")
    assert "" not in m and "OTHER" in m


def test_load_us_industry_map_missing(monkeypatch):
    monkeypatch.setattr(bp, "latest", lambda p: None)
    assert bp.load_us_industry_map() == {}


def test_collect_all_countries(scans):
    df = bp.collect().set_index(["country", "ticker"])
    a = df.loc[("USA", "AAPL")]
    assert (a.company_name, a.exchange, a.sector, a.industry, a.code) == ("Apple Inc", "NASDAQ", "Tech",
                                                                         "Consumer Electronics", "AAPL")
    z = df.loc[("USA", "ZZZ")]                                   # not in summary -> symbol as name, "US" exchange
    assert z.company_name == "ZZZ" and z.exchange == "US" and pd.isna(z.industry)
    assert df.loc[("India", "TCS.NS")].exchange == "NSE" and df.loc[("India", "RELIANCE.BO")].exchange == "BSE"
    assert df.loc[("India", "FOO.XX")].exchange == "IN"
    assert df.loc[("India", "TCS.NS")].company_name == "TCS"
    t = df.loc[("Japan", "7203.T")]
    assert (t.company_name, t.exchange, t.sector, t.code) == ("Toyota", "TSE", "Autos", "7203")
    assert df.loc[("South Korea", "005930.KS")].exchange == "KOSPI"
    assert df.loc[("South Korea", "000660.KS")].exchange == "KRX"          # blank market -> KRX
    assert df.loc[("South Korea", "000660.KS")].company_name == "000660"   # missing name -> code
    e = df.loc[("Europe", "ADS.DE")]
    assert e.company_name == "Adidas" and e.exchange == "EU" and pd.isna(e.sector)


def test_collect_skips_missing_scan(monkeypatch, capsys):
    monkeypatch.setattr(bp, "latest", lambda p: None)
    assert bp.collect().empty
    err = capsys.readouterr().err
    assert "[skip] USA: no scan found" in err and "[skip] Europe: no scan found" in err


def _base_df():
    return pd.DataFrame({"company_name": ["A", "B"], "ticker": ["A.NS", "B.KS"], "code": ["A", "B"],
                         "country": ["India", "South Korea"], "exchange": ["NSE", "KRX"],
                         "sector": [None, "Existing"], "industry": [None, None]})


def test_merge_cache_absent(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(bp, "HERE", str(tmp_path))
    df = _base_df()
    assert bp.merge_cache(df) is df
    assert "no industry_cache.json" in capsys.readouterr().err


@pytest.mark.xfail(int(pd.__version__.split(".")[0]) >= 3, strict=True, raises=AssertionError,
                   reason="build_industry_parquet.py merge_cache: `not r['sector']` is False for NaN, and pandas>=3 stores "
                          "missing strings as NaN (not None), so blank sector/industry cells are never filled from the cache")
def test_merge_cache_fills_only_blanks(monkeypatch, tmp_path):
    monkeypatch.setattr(bp, "HERE", str(tmp_path))
    (tmp_path / "industry_cache.json").write_text(json.dumps({
        "A.NS": {"sector": "Tech", "industry": "Software"},
        "B.KS": {"sector": "Overwrite?", "industry": "Chips"},
        "C.XX": {"sector": "n/a", "industry": "n/a"}}))
    out = bp.merge_cache(_base_df())
    a, b = out.iloc[0], out.iloc[1]
    assert (a.sector, a.industry) == ("Tech", "Software")
    assert (b.sector, b.industry) == ("Existing", "Chips")          # existing sector preserved


def test_merge_cache_ignores_empty_enrichment(monkeypatch, tmp_path):
    monkeypatch.setattr(bp, "HERE", str(tmp_path))
    (tmp_path / "industry_cache.json").write_text(json.dumps({"A.NS": {"sector": None, "industry": None}}))
    out = bp.merge_cache(_base_df())
    assert pd.isna(out.iloc[0].sector) and pd.isna(out.iloc[0].industry)


def test_main_builds_outputs(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(bp, "HERE", str(tmp_path))
    rows = [("USA", "A", "A", "Tech", "Software"), ("India", "A.NS", "A.NS", None, "Software"),
            ("Japan", "B", "B", "Auto", None), ("Japan", "C", "C", None, None),
            ("Japan", "B", "B", "Auto", None)]               # duplicate (ticker, country) dropped
    df = pd.DataFrame([{"company_name": n, "ticker": t, "code": t, "country": c, "exchange": "X",
                        "sector": s, "industry": i} for c, t, n, s, i in rows])
    monkeypatch.setattr(bp, "collect", lambda: df)
    monkeypatch.setattr(bp, "merge_cache", lambda d: d)
    saved = {}
    monkeypatch.setattr(pd.DataFrame, "to_parquet", lambda self, p, **kw: saved.__setitem__(os.path.basename(p), self.copy()))
    monkeypatch.setattr(bp.os.path, "getsize", lambda p: 4096)
    bp.main()
    co = saved["companies_industry.parquet"].set_index("company_name")
    assert len(co) == 4
    assert co.loc["C", "segment"] == "Unclassified" and co.loc["B", "segment"] == "Auto"
    sw = saved["companies_industry.parquet"]
    sw = sw[sw.segment == "Software"]
    assert list(sw["peer_count"]) == [1, 1]
    assert sorted(sum(sw["global_peers"].tolist(), [])) == ["A", "A.NS"]      # each lists the other
    seg = saved["industry_segments.parquet"].set_index("segment")
    assert seg.loc["Software", "n_companies"] == 2 and seg.loc["Software", "countries"] == ["India", "USA"]
    assert list(saved["industry_segments.parquet"]["n_companies"]) == sorted(
        saved["industry_segments.parquet"]["n_companies"], reverse=True)
    err = capsys.readouterr().err
    assert "companies: 4  classified: 3  unclassified: 1" in err and "segments : 3" in err


def test_main_peer_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(bp, "HERE", str(tmp_path))
    n = bp.PEER_CAP + 10
    df = pd.DataFrame({"company_name": [f"C{i:03d}" for i in range(n)], "ticker": [f"T{i}" for i in range(n)],
                       "code": "c", "country": "USA", "exchange": "X", "sector": "S", "industry": "I"})
    monkeypatch.setattr(bp, "collect", lambda: df)
    monkeypatch.setattr(bp, "merge_cache", lambda d: d)
    saved = {}
    monkeypatch.setattr(pd.DataFrame, "to_parquet", lambda self, p, **kw: saved.__setitem__(os.path.basename(p), self.copy()))
    monkeypatch.setattr(bp.os.path, "getsize", lambda p: 1)
    bp.main()
    row = saved["companies_industry.parquet"].iloc[0]
    assert len(row["global_peers"]) == bp.PEER_CAP and row["peer_count"] == n - 1
    assert row["company_name"] not in row["global_peers"]


# ───────────────────────────── enrich_industries ─────────────────────────────
def _install_info(monkeypatch, fn):
    m = types.ModuleType("apiclient")
    m.yf_info = fn
    monkeypatch.setitem(sys.modules, "apiclient", m)


def test_cache_roundtrip_and_corruption(monkeypatch, tmp_path):
    monkeypatch.setattr(ei, "CACHE", str(tmp_path / "c.json"))
    assert ei.load_cache() == {}
    ei.save_cache({"A": {"sector": "S"}})
    assert ei.load_cache() == {"A": {"sector": "S"}}
    assert not os.path.exists(str(tmp_path / "c.json") + ".tmp")           # atomic replace
    (tmp_path / "c.json").write_text("{not json")
    assert ei.load_cache() == {}


def test_fetch_one_variants(monkeypatch):
    _install_info(monkeypatch, lambda t: {"sector": "Tech", "industry": "Software"})
    assert ei.fetch_one("A") == ("A", {"sector": "Tech", "industry": "Software"})
    _install_info(monkeypatch, lambda t: {"sector": "Tech"})
    assert ei.fetch_one("A")[1] == {"sector": "Tech", "industry": None}
    _install_info(monkeypatch, lambda t: {})
    assert ei.fetch_one("A")[1] == {"sector": None, "industry": None, "_err": "empty"}

    def boom(t):
        raise RuntimeError("x" * 200)
    _install_info(monkeypatch, boom)
    err = ei.fetch_one("A")[1]["_err"]
    assert err == "x" * 80


def _setup_main(monkeypatch, tmp_path, df, info):
    monkeypatch.setattr(ei, "CACHE", str(tmp_path / "c.json"))
    monkeypatch.setattr(ei.pd, "read_parquet", lambda p: df)
    monkeypatch.setattr(ei.time, "sleep", lambda s: None)
    calls = []
    _install_info(monkeypatch, lambda t: calls.append(t) or info.get(t, {}))
    return calls


def _universe_df():
    return pd.DataFrame({"ticker": ["A.NS", "B.KS", "C.T", None, "D.NS"],
                         "country": ["India", "South Korea", "Japan", "India", "India"],
                         "industry": [None, None, None, None, "Known"]})


def test_main_default_targets_india_korea_and_resumes(monkeypatch, tmp_path, capsys):
    calls = _setup_main(monkeypatch, tmp_path, _universe_df(),
                        {"A.NS": {"sector": "S", "industry": "I"}, "B.KS": {}, "D.NS": {"industry": "K"}})
    ei.CACHE = str(tmp_path / "c.json")
    (tmp_path / "c.json").write_text(json.dumps({"D.NS": {"sector": None, "industry": "K"}}))
    monkeypatch.setattr(sys, "argv", ["enrich_industries.py", "--workers", "1"])
    ei.main()
    assert sorted(calls) == ["A.NS", "B.KS"]                      # Japan excluded, cached D.NS skipped, None dropped
    cache = json.load(open(ei.CACHE))
    assert cache["A.NS"]["industry"] == "I" and cache["B.KS"]["_err"] == "empty"
    err = capsys.readouterr().err
    assert "targets=3  cached=1  to_fetch=2" in err
    assert "DONE enrich: fetched=2  industry_hits=2/3" in err


def test_main_all_targets_unclassified(monkeypatch, tmp_path, capsys):
    calls = _setup_main(monkeypatch, tmp_path, _universe_df(), {})
    monkeypatch.setattr(sys, "argv", ["enrich_industries.py", "--all"])
    ei.main()
    assert sorted(calls) == ["A.NS", "B.KS", "C.T"]


def test_main_progress_checkpoint_every_100(monkeypatch, tmp_path, capsys):
    n = 100
    df = pd.DataFrame({"ticker": [f"T{i:03d}.NS" for i in range(n)], "country": "India", "industry": None})
    saves = []
    calls = _setup_main(monkeypatch, tmp_path, df, {f"T{i:03d}.NS": {"sector": "S", "industry": "I"} for i in range(n)})
    real = ei.save_cache
    monkeypatch.setattr(ei, "save_cache", lambda c: (saves.append(len(c)), real(c)))
    monkeypatch.setattr(sys, "argv", ["enrich_industries.py", "--workers", "2"])
    ei.main()
    assert saves == [100, 100]                                    # checkpoint at 100 + final save
    assert "100/100  hits=100" in capsys.readouterr().err


def test_main_rebuild_invokes_build(monkeypatch, tmp_path):
    _setup_main(monkeypatch, tmp_path, _universe_df().iloc[:0], {})
    cmds = []
    monkeypatch.setattr(ei.os, "system", lambda c: cmds.append(c) or 0)
    monkeypatch.setattr(sys, "argv", ["enrich_industries.py", "--rebuild"])
    ei.main()
    assert len(cmds) == 1 and cmds[0].endswith("python3 build_industry_parquet.py")
