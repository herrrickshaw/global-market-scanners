"""dashboard.py + data_quality.py (parquet/SQLite/duckdb faked)."""
import os
import sqlite3
import sys
import types

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard as db  # noqa: E402
import data_quality as dq  # noqa: E402


# ───────────────────────────── dashboard ─────────────────────────────
def test_render_html_structure():
    html = db.render_html({"A": pd.DataFrame({"x": [1, 2]}), "B": pd.DataFrame(), "C": None}, generated="2026-01-01 10:00")
    assert html.startswith("<!doctype html>") and html.endswith("</body></html>")
    assert "generated 2026-01-01 10:00" in html and "<h2>A</h2>" in html and "<th>x</th>" in html
    assert html.count("no data") == 2                                        # empty df and None
    assert "<h2>B</h2><p class='meta'>no data</p>" in html


def test_render_html_default_timestamp():
    html = db.render_html({})
    import re
    assert re.search(r"generated \d{4}-\d{2}-\d{2} \d{2}:\d{2}", html)


def _install_acc(monkeypatch, tmp_path, screens):
    acc = types.ModuleType("accumulation_screener")
    acc.SEED = str(tmp_path)
    acc.current_screen = lambda m: screens.get(m, pd.DataFrame())
    monkeypatch.setitem(sys.modules, "accumulation_screener", acc)


def test_accumulation_section(monkeypatch, tmp_path):
    for m in ("US", "JP", "ZZ"):
        (tmp_path / f"cleaned_long_{m}.parquet").write_bytes(b"x")
    (tmp_path / "other.txt").write_bytes(b"x")
    s_us = pd.DataFrame({"market": "US", "ticker": ["A", "B"], "close": [1.0, 2.0], "cmf": [0.123, 0.5], "accum": [1.234, np.nan]})
    s_jp = pd.DataFrame({"market": "JP", "ticker": ["C"], "close": [3.0], "cmf": [0.2], "accum": [5.678]})
    _install_acc(monkeypatch, tmp_path, {"US": s_us, "JP": s_jp})
    df = db._accumulation_section(top=5)
    assert list(df["ticker"]) == ["C", "A"]                                  # NaN accum dropped, sorted desc
    assert list(df.columns) == ["market", "ticker", "close", "cmf", "accum"]
    assert df["accum"].tolist() == [5.68, 1.23] and df["cmf"].tolist() == [0.2, 0.12]
    assert len(db._accumulation_section(top=1)) == 1
    assert list(db._accumulation_section(markets=["US"], top=5)["ticker"]) == ["A"]
    assert db._accumulation_section(markets=["ZZ"]).empty


class FakeDF:
    def __init__(self, name):
        self.name = name


class FakeCon:
    def __init__(self):
        self.sql, self.closed = [], False

    def execute(self, q):
        self.sql.append(q)
        return types.SimpleNamespace(df=lambda: pd.DataFrame({"q": [q[:20]]}))

    def close(self):
        self.closed = True


def _install_duck(monkeypatch):
    con = FakeCon()
    duck = types.ModuleType("duckdb")
    duck.connect = lambda p: con
    wh = types.ModuleType("warehouse")
    wh.build = lambda c: con.sql.append("BUILD")
    wh.SHOWS = {"markets": "m", "dvm_dist": "d", "ggg_global": "g", "high_roe_low_de": "h"}
    monkeypatch.setitem(sys.modules, "duckdb", duck)
    monkeypatch.setitem(sys.modules, "warehouse", wh)
    return con


def test_query_all_sections(monkeypatch, tmp_path):
    con = _install_duck(monkeypatch)
    monkeypatch.setattr(db, "_accumulation_section", lambda top: pd.DataFrame({"t": [top]}))
    sec = db._query_all(accum_top=7)
    assert list(sec) == ["Market coverage", "DVM classification", "Top GGG Strong Performers", "High ROE / low D-E",
                         "Accumulation / CMF screen (top by accumulation, validated 6m signal)"]
    assert con.closed and con.sql[0] == "BUILD" and "SET max_expression_depth=10000" in con.sql
    assert sec[list(sec)[-1]]["t"].iloc[0] == 7
    assert "Accumulation / CMF screen (top by accumulation, validated 6m signal)" not in db._query_all(include_accum=False)


def test_query_all_accum_failure_is_isolated(monkeypatch, capsys):
    _install_duck(monkeypatch)
    monkeypatch.setattr(db, "_accumulation_section", lambda top: (_ for _ in ()).throw(RuntimeError("no parquet")))
    sec = db._query_all()
    assert sec["Accumulation / CMF screen (top by accumulation, validated 6m signal)"].empty
    assert "accumulation section skipped: no parquet" in capsys.readouterr().out
    assert len(sec) == 5


def test_dashboard_main(monkeypatch, tmp_path, capsys):
    got = {}
    monkeypatch.setattr(db, "_query_all", lambda accum_top, include_accum: (got.update(a=accum_top, i=include_accum), {"S": pd.DataFrame({"x": [1]})})[1])
    out = str(tmp_path / "d.html")
    monkeypatch.setattr(sys, "argv", ["db", "--out", out, "--accum-top", "3", "--no-accum"])
    db.main()
    assert got == {"a": 3, "i": False} and "<h2>S</h2>" in open(out).read()
    assert f"wrote {out}" in capsys.readouterr().out
    opened = []
    wb = types.ModuleType("webbrowser"); wb.open = lambda u: opened.append(u)
    monkeypatch.setitem(sys.modules, "webbrowser", wb)
    monkeypatch.setattr(sys, "argv", ["db", "--out", out, "--open"])
    db.main()
    assert opened == [f"file://{os.path.abspath(out)}"] and got["i"] is True


# ───────────────────────────── data_quality ─────────────────────────────
def test_staleness_days():
    assert dq.staleness_days(["2024-01-01", "2024-01-10"], asof="2024-01-15") == 5
    assert dq.staleness_days(["2024-01-15"], asof="2024-01-15") == 0
    assert dq.staleness_days(["2024-01-20"], asof="2024-01-15") == -5
    assert dq.staleness_days(["2000-01-01"]) > 9000                              # default asof=today


def test_null_rate():
    assert dq.null_rate([1, None, 3, np.nan]) == 0.5
    assert dq.null_rate([]) == 1.0 and dq.null_rate([1, 2]) == 0.0


def test_outlier_rate():
    base = [100.0 + i * 0.1 for i in range(50)]
    assert dq.outlier_rate(base) == 0.0
    assert dq.outlier_rate(base + [1e6]) == pytest.approx(1 / 51)
    assert dq.outlier_rate([1, 2, 3, 4]) == 0.0                                 # < 5 values
    assert dq.outlier_rate([1, 2, 3, 4, 1e9]) > 0
    assert dq.outlier_rate(["x", None, 1, 2]) == 0.0
    assert dq.outlier_rate([5.0] * 10) == 0.0                                   # MAD = 0 and std = 0 -> falls back to 1.0
    assert dq.outlier_rate(base + [1e6], k=1e9) == 0.0                           # k is the robust-z cutoff


def test_is_monotonic_dates():
    assert dq.is_monotonic_dates(["2024-01-01", "2024-01-02"]) is True
    assert dq.is_monotonic_dates(["2024-01-02", "2024-01-01"]) is False
    assert dq.is_monotonic_dates(["2024-01-01", "2024-01-01"]) is True           # equal allowed
    assert dq.is_monotonic_dates(["2024-01-01"]) is True and dq.is_monotonic_dates([]) is True


def test_evaluate_ops_and_status():
    rows = dq.evaluate("src", {"a": 5, "b": 5, "c": True, "d": 5, "e": 5},
                       {"a": ("<=", 5), "b": ("<=", 4), "c": ("==", True), "d": (">=", 6), "e": (">=", 5)})
    st = {r["rule"]: r["status"] for r in rows}
    assert st == {"a": "PASS", "b": "FAIL", "c": "PASS", "d": "FAIL", "e": "PASS"}
    assert rows[1] == {"source": "src", "rule": "b", "value": 5, "limit": "<=4", "status": "FAIL"}


def _ohlc_df(n=30, stale_end="2024-01-31", glitch=False, unsorted=False, nulls=False):
    idx = pd.date_range(end=stale_end, periods=n, freq="D")
    close = np.linspace(100, 110, n)
    if glitch:
        close[15] = 1e7
    if nulls:
        close[:10] = np.nan
    df = pd.DataFrame({"Date": idx, "Symbol": "A", "Close": close})
    if unsorted:
        df = df.iloc[::-1].reset_index(drop=True)
    return df


def test_scan_parquet(monkeypatch):
    monkeypatch.setattr(dq.pd, "read_parquet", lambda p: _ohlc_df())
    monkeypatch.setattr(dq, "staleness_days", lambda d: 3)
    r = {x["rule"]: x for x in dq.scan_parquet("p", "US", 30, 0.2, 0.01)}
    assert set(r) == {"stale_days", "close_null_rate", "close_outlier_rate", "dates_sorted_within_symbol"}
    assert all(x["status"] == "PASS" for x in r.values()) and r["stale_days"]["source"] == "ohlc:US"
    monkeypatch.setattr(dq, "staleness_days", lambda d: 31)
    assert {x["rule"]: x["status"] for x in dq.scan_parquet("p", "US", 30, 0.2, 0.01)}["stale_days"] == "FAIL"
    monkeypatch.setattr(dq, "staleness_days", lambda d: 30)
    assert {x["rule"]: x["status"] for x in dq.scan_parquet("p", "US", 30, 0.2, 0.01)}["stale_days"] == "PASS"
    monkeypatch.setattr(dq.pd, "read_parquet", lambda p: _ohlc_df(glitch=True, nulls=True))
    st = {x["rule"]: x["status"] for x in dq.scan_parquet("p", "US", 30, 0.2, 0.01)}
    assert st["close_null_rate"] == "FAIL" and st["close_outlier_rate"] == "FAIL"
    monkeypatch.setattr(dq.pd, "read_parquet", lambda p: _ohlc_df(unsorted=True))
    assert {x["rule"]: x["status"] for x in dq.scan_parquet("p", "US", 30, 0.2, 0.01)}["dates_sorted_within_symbol"] == "PASS"   # sorted before check


def test_scan_fundamentals(tmp_path):
    p = str(tmp_path / "f.db")
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE fund(ticker TEXT, roe TEXT, de TEXT, pe TEXT)")
    con.executemany("INSERT INTO fund VALUES (?,?,?,?)", [("A", "10", "1", "15"), ("B", None, "1", "16"),
                                                           ("C", "x", "1", "17"), ("D", "5", "1", "18"), ("E", "7", "1", "19")])
    con.commit(); con.close()
    r = {x["rule"]: x for x in dq.scan_fundamentals(p, 0.4)}
    assert r["roe_null_rate"]["value"] == 0.4 and r["roe_null_rate"]["status"] == "PASS"
    assert dq.scan_fundamentals(p, 0.39)[0]["status"] == "FAIL"
    assert r["pe_outlier_rate"]["status"] == "PASS" and r["roe_null_rate"]["source"] == "fundamentals"


def test_main_report_and_exit_codes(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(dq, "SEED", str(tmp_path / "seed"))
    monkeypatch.setattr(dq, "HERE", str(tmp_path))
    os.makedirs(tmp_path / "seed")
    for m in ("US", "JP"):
        (tmp_path / "seed" / f"cleaned_long_{m}.parquet").write_bytes(b"x")
    calls = []

    def scan(p, m, s, n, o):
        calls.append((m, s, n, o))
        if m == "JP":
            raise ValueError("corrupt")
        return [{"source": "ohlc:US", "rule": "stale_days", "value": 1, "limit": "<=30", "status": "PASS"}]
    monkeypatch.setattr(dq, "scan_parquet", scan)
    monkeypatch.setattr(sys, "argv", ["dq", "--max-stale-days", "9", "--max-null-rate", "0.5", "--max-outlier-rate", "0.02"])
    with pytest.raises(SystemExit) as e:
        dq.main()
    assert e.value.code == 1 and calls[0] == ("JP", 9, 0.5, 0.02)               # sorted: JP first
    out = capsys.readouterr().out
    assert "DATA QUALITY REPORT" in out and "1/2 checks PASS; 1 FAIL" in out and "corrupt" in out


def test_main_all_pass_and_fundamentals_branch(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(dq, "SEED", str(tmp_path / "seed"))
    monkeypatch.setattr(dq, "HERE", str(tmp_path))
    os.makedirs(tmp_path / "seed")
    (tmp_path / "fundamentals_cache.db").write_bytes(b"x")
    ok = [{"source": "s", "rule": "r", "value": 1, "limit": "<=2", "status": "PASS"}]
    monkeypatch.setattr(dq, "scan_fundamentals", lambda db, n: ok)
    monkeypatch.setattr(sys, "argv", ["dq"])
    with pytest.raises(SystemExit) as e:
        dq.main()
    assert e.value.code == 0 and "1/1 checks PASS; 0 FAIL" in capsys.readouterr().out
    monkeypatch.setattr(dq, "scan_fundamentals", lambda db, n: (_ for _ in ()).throw(RuntimeError("locked")))
    with pytest.raises(SystemExit) as e:
        dq.main()
    assert e.value.code == 1 and "locked" in capsys.readouterr().out


def test_main_no_sources(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(dq, "SEED", str(tmp_path / "none"))
    monkeypatch.setattr(dq, "HERE", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["dq"])
    dq.main()
    assert "no data sources found to check" in capsys.readouterr().err
