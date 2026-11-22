"""fundamentals_global.py: yfinance fundamentals normalisation, screens, cache + main() (offline)."""
import os
import sqlite3
import sys
import types

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fundamentals_global as fg  # noqa: E402


def test_fin_filters_non_finite():
    assert fg._fin(None) is None
    assert fg._fin(float("inf")) is None and fg._fin(float("-inf")) is None
    assert fg._fin(float("nan")) is None
    assert fg._fin("abc") is None            # TypeError path
    assert fg._fin(0) == 0 and fg._fin(-3.5) == -3.5


def test_norm_de_percent_boundary():
    assert fg._norm_de(None) is None
    assert fg._norm_de(float("inf")) is None
    assert fg._norm_de(10) == 10          # not > 10 -> unchanged
    assert fg._norm_de(10.5) == pytest.approx(0.105)
    assert fg._norm_de(150) == 1.5
    assert fg._norm_de(0.4) == 0.4


def _install_info(monkeypatch, fn):
    mod = types.ModuleType("apiclient")
    mod.yf_info = fn
    monkeypatch.setitem(sys.modules, "apiclient", mod)


def test_fetch_one_maps_fields(monkeypatch):
    info = {"marketCap": 1e9, "trailingPE": 12.0, "priceToBook": 2.0, "returnOnEquity": 0.2,
            "returnOnAssets": 0.05, "debtToEquity": 50, "revenueGrowth": 0.1, "earningsGrowth": -0.2,
            "operatingMargins": 0.3, "dividendYield": 4.1, "sector": "Tech"}
    _install_info(monkeypatch, lambda t: info)
    t, d = fg.fetch_one("AAA")
    assert t == "AAA"
    assert d["roe"] == pytest.approx(20.0) and d["roa"] == pytest.approx(5.0)
    assert d["de"] == 0.5          # 50 > 10 -> treated as percent
    assert d["rev_growth"] == pytest.approx(10.0) and d["earn_growth"] == pytest.approx(-20.0)
    assert d["op_margin"] == pytest.approx(30.0) and d["div_yield"] == 4.1
    assert d["mktcap"] == 1e9 and d["sector"] == "Tech" and d["pe"] == 12.0 and d["pb"] == 2.0


def test_fetch_one_de_percent_normalised_and_missing_fields_none(monkeypatch):
    _install_info(monkeypatch, lambda t: {"marketCap": 5, "debtToEquity": 150})
    _, d = fg.fetch_one("A")
    assert d["de"] == 1.5
    for k in ("pe", "pb", "roe", "roa", "rev_growth", "earn_growth", "op_margin", "div_yield", "sector"):
        assert d[k] is None


def test_fetch_one_inf_pe_is_none(monkeypatch):
    _install_info(monkeypatch, lambda t: {"marketCap": 5, "trailingPE": float("inf")})
    assert fg.fetch_one("A")[1]["pe"] is None


@pytest.mark.parametrize("info", [None, {}, {"pe": 1}])
def test_fetch_one_no_marketcap_returns_none(monkeypatch, info):
    _install_info(monkeypatch, lambda t: info)
    assert fg.fetch_one("A") == ("A", None)


def test_fetch_one_swallows_exceptions(monkeypatch):
    def boom(t):
        raise RuntimeError("rate limited")
    _install_info(monkeypatch, boom)
    assert fg.fetch_one("A") == ("A", None)


def test_liquid_subset_orders_by_median_dollar_volume(monkeypatch, tmp_path):
    df = pd.DataFrame({"Symbol": ["A"] * 3 + ["B"] * 3 + ["C"] * 3,
                       "Close": [1, 1, 1, 10, 10, 10, 5, 5, 5],
                       "Volume": [100, 100, 100, 100, 100, 1000, 10, 10, 10]})
    monkeypatch.setattr(fg, "SEED", str(tmp_path))
    seen = {}
    monkeypatch.setattr(fg.pd, "read_parquet", lambda p: seen.setdefault("p", p) and df)
    assert fg.liquid_subset("US", 2) == ["B", "A"]
    assert seen["p"].endswith("cleaned_long_US.parquet")
    assert fg.liquid_subset("US", 1) == ["B"]


def _r(**kw):
    r = {"roe": 20, "de": 0.5, "rev_growth": 12, "pe": 10, "pb": 1.5, "div_yield": 4}
    r.update(kw)
    return r


def test_screen_high_roe_low_de():
    f = fg.SCREENS["high_roe_low_de"]
    assert f(_r()) and not f(_r(roe=15)) and f(_r(roe=15.01))
    assert not f(_r(roe=None)) and not f(_r(de=None)) and not f(_r(de=1)) and f(_r(de=0.99))
    assert f(_r(de=0))


def test_screen_growth_roe_lowpe():
    f = fg.SCREENS["growth_roe_lowpe"]
    assert f(_r())
    assert not f(_r(rev_growth=10)) and not f(_r(roe=15)) and not f(_r(pe=None))
    assert not f(_r(pe=0)) and f(_r(pe=0.1)) and not f(_r(pe=30)) and f(_r(pe=29.9))
    assert not f(_r(rev_growth=None))


def test_screen_value():
    f = fg.SCREENS["value"]
    assert f(_r(pb=2.9, pe=14.9)) and not f(_r(pb=3)) and not f(_r(pe=15)) and not f(_r(pe=0))
    assert not f(_r(pb=None)) and not f(_r(pe=None))
    assert f(_r(de=None)) and not f(_r(de=1)) and f(_r(de=0.99))


def test_screen_dividend():
    f = fg.SCREENS["dividend"]
    assert f(_r(div_yield=3.1)) and not f(_r(div_yield=3)) and not f(_r(div_yield=None))
    assert f(_r(div_yield=4, de=1.49)) and not f(_r(div_yield=4, de=1.5)) and f(_r(div_yield=4, de=None))


def test_cache_creates_table_and_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr(fg, "CACHE", str(tmp_path / "c.db"))
    c = fg._cache()
    c.execute("INSERT INTO fund(ticker, market) VALUES ('A','US')")
    c.commit(); c.close()
    c2 = fg._cache()
    assert c2.execute("SELECT COUNT(*) FROM fund").fetchone()[0] == 1
    c2.close()


def _setup_main(monkeypatch, tmp_path, info_by_ticker, universe):
    monkeypatch.setattr(fg, "SEED", str(tmp_path))
    for m in universe:
        (tmp_path / f"cleaned_long_{m}.parquet").write_bytes(b"x")
    monkeypatch.setattr(fg, "CACHE", str(tmp_path / "cache.db"))
    monkeypatch.setattr(fg, "liquid_subset", lambda m, top: universe[m][:top])
    calls = []

    def info(t):
        calls.append(t)
        return info_by_ticker.get(t)

    _install_info(monkeypatch, info)
    return calls


def _good(roe=0.2, de=50):
    return {"marketCap": 1e9, "returnOnEquity": roe, "debtToEquity": de, "trailingPE": 10, "sector": "S"}


def test_main_fetches_screens_and_caches(monkeypatch, tmp_path, capsys):
    uni = {"US": ["A", "B", "C"], "JP": ["D"]}
    info = {"A": _good(), "B": _good(roe=0.05), "D": _good()}      # C has no info -> dropped
    calls = _setup_main(monkeypatch, tmp_path, info, uni)
    out_db = str(tmp_path / "out.db")
    monkeypatch.setattr(sys, "argv", ["fundamentals_global.py", "--db", out_db, "--top", "3",
                                      "--screen", "high_roe_low_de", "--workers", "1"])
    fg.main()
    assert sorted(calls) == ["A", "B", "C", "D"]
    con = sqlite3.connect(out_db)
    hits = sorted(con.execute("SELECT market, ticker FROM fund_hits").fetchall())
    con.close()
    assert hits == [("JP", "D"), ("US", "A")]
    cache = sqlite3.connect(str(tmp_path / "cache.db"))
    assert {r[0] for r in cache.execute("SELECT ticker FROM fund")} == {"A", "B", "D"}
    cache.close()
    cap = capsys.readouterr()
    assert "4 liquid tickers across 2 markets; cached=0 to_fetch=4" in cap.err
    lines = cap.out.splitlines()
    assert any(l.split()[:3] == ["US", "2", "1"] for l in lines)
    assert any(l.split()[:3] == ["JP", "1", "1"] for l in lines)
    assert any(l.split()[:3] == ["TOTAL", "3", "2"] for l in lines)
    assert "ROE=20.0" in cap.out


def test_main_second_run_uses_cache_and_filters_to_targets(monkeypatch, tmp_path, capsys):
    uni = {"US": ["A", "B"]}
    calls = _setup_main(monkeypatch, tmp_path, {"A": _good(), "B": _good()}, uni)
    argv = ["fundamentals_global.py", "--db", str(tmp_path / "o.db"), "--markets", "US", "--workers", "1"]
    monkeypatch.setattr(sys, "argv", argv)
    fg.main()
    assert sorted(calls) == ["A", "B"]
    capsys.readouterr()
    # second run only asks for A; cache holds B too but it must not be screened/counted
    uni["US"] = ["A"]
    calls.clear()
    fg.main()
    assert calls == []
    cap = capsys.readouterr()
    assert "cached=1 to_fetch=0" in cap.err
    assert any(l.split()[:3] == ["TOTAL", "1", "1"] for l in cap.out.splitlines())


def test_main_progress_every_100(monkeypatch, tmp_path, capsys):
    tick = [f"T{i:03d}" for i in range(100)]
    _setup_main(monkeypatch, tmp_path, {t: _good() for t in tick}, {"US": tick})
    monkeypatch.setattr(sys, "argv", ["fundamentals_global.py", "--db", str(tmp_path / "o.db"),
                                      "--markets", "US", "--top", "100", "--workers", "2"])
    fg.main()
    assert "fetched 100/100" in capsys.readouterr().err
