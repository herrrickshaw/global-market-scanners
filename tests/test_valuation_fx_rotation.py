"""unlisted_valuation.py, liquidity_multiyear.py, fx.py, sector_rotation.py (all inputs faked or tmp)."""
import json
import os
import sqlite3
import sys
import types

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fx  # noqa: E402
import liquidity_multiyear as lm  # noqa: E402
import marketdata  # noqa: E402
import sector_rotation as sr  # noqa: E402
import unlisted_valuation as uv  # noqa: E402
from conftest_helpers import make_ohlcv  # noqa: E402


# ───────────────────────────── unlisted_valuation ─────────────────────────────
def test_implied_value_and_band():
    assert uv.implied_value(100, 12.5) == 1250.0 and uv.implied_value("10", "2") == 20.0
    b = uv.peer_multiple_band([10, 20, 30, 40, None, -5, 0, float("nan"), float("inf")])
    assert b == {"n": 4, "median": 25.0, "q1": 17.5, "q3": 32.5}
    assert uv.peer_multiple_band([]) == {"n": 0, "median": None, "q1": None, "q3": None}
    assert uv.peer_multiple_band([None, -1, 0])["n"] == 0


def test_value_range():
    band = {"n": 4, "median": 20.0, "q1": 10.0, "q3": 30.0}
    assert uv.value_range(5, band) == {"low": 50.0, "mid": 100.0, "high": 150.0, "n_peers": 4}
    empty = {"low": None, "mid": None, "high": None, "n_peers": 0}
    assert uv.value_range(5, {"median": None}) == empty and uv.value_range(5, {}) == empty and uv.value_range(5, None) == empty


@pytest.fixture
def peers_env(monkeypatch, tmp_path):
    comp = pd.DataFrame({"ticker": ["A", "B", "C", "D", "E", "F"], "industry": ["Soft"] * 4 + ["Hard"] * 2,
                         "sector": "S", "extra": 1})
    comp_p = tmp_path / "c.parquet"; comp_p.write_bytes(b"x")
    db = tmp_path / "f.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE fund(ticker TEXT, pe TEXT, pb TEXT, roe TEXT)")
    con.executemany("INSERT INTO fund VALUES (?,?,?,?)", [("A", "10", "1", "5"), ("B", "20", "2", "x"), ("C", "30", "3", "9"),
                                                          ("D", "-4", "4", "1"), ("E", "15", "1.5", "2"), ("ZZ", "99", "9", "9")])
    con.commit(); con.close()
    monkeypatch.setattr(uv, "COMPANIES", str(comp_p)); monkeypatch.setattr(uv, "FUND", str(db))
    monkeypatch.setattr(uv, "UNLISTED", str(tmp_path / "u.parquet"))
    monkeypatch.setattr(uv.pd, "read_parquet", lambda p: comp if p == str(comp_p) else pd.DataFrame(
        {"segment": ["Soft ware", "Soft", None], "company_name": ["U1", "U2", "U3"]}))
    return tmp_path


def test_load_peers_and_industry_band(peers_env):
    p = uv.load_peers()
    assert set(p["ticker"]) == {"A", "B", "C", "D", "E"} and set(p.columns) == {"ticker", "industry", "sector", "pe", "pb", "roe"}
    assert np.isnan(p.set_index("ticker").loc["B", "roe"])
    b = uv.industry_band(p, "Soft", "pe")
    assert b["n"] == 3 and b["median"] == 20.0                               # -4 dropped
    assert uv.industry_band(p, "Soft", "pb")["n"] == 4 and uv.industry_band(p, "Nope")["n"] == 0


def _main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["uv", *argv])
    uv.main()


def test_uv_main_missing_inputs(monkeypatch, tmp_path):
    monkeypatch.setattr(uv, "COMPANIES", str(tmp_path / "x")); monkeypatch.setattr(uv, "FUND", str(tmp_path / "y"))
    with pytest.raises(SystemExit, match="need companies_industry.parquet"):
        _main(monkeypatch)


def test_uv_main_list_and_industry(peers_env, monkeypatch, capsys):
    _main(monkeypatch)                                                       # no industry -> list and return
    out = capsys.readouterr().out
    assert "industries with >=3 listed peers (1)" in out and "Soft" in out and "median P/E=15.0" in out
    _main(monkeypatch, "--industry", "Soft", "--earnings", "100", "--book", "50")
    cap = capsys.readouterr()
    assert "comps for industry: Soft" in cap.out and "P/E band (n=3): q1=15.0 med=20.0 q3=25.0" in cap.out
    assert "implied value @ earnings 100: 1,500 — 2,000 — 2,500" in cap.out and "implied value @ book 50:" in cap.out
    assert "industries with" not in cap.out                                    # --list not requested


def test_uv_main_no_peer_coverage_and_unlisted(peers_env, monkeypatch, capsys):
    _main(monkeypatch, "--industry", "Nothing", "--earnings", "100")
    out = capsys.readouterr().out
    assert "P/E band: no peer coverage" in out and "implied value" not in out
    (peers_env / "u.parquet").write_bytes(b"x")
    _main(monkeypatch, "--industry", "soft", "--list")
    cap = capsys.readouterr()
    assert "unlisted firms tagged to this space (2)" in cap.err and "- U1" in cap.out and "- U2" in cap.out and "U3" not in cap.out
    _main(monkeypatch, "--industry", "nomatch")
    assert "unlisted firms tagged" not in capsys.readouterr().err


# ───────────────────────────── liquidity_multiyear ─────────────────────────────
def _fm_panel(n_dates=8, n=30, slope=0.1, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for d in pd.date_range("2020-01-01", periods=n_dates, freq="QS"):
        ill = rng.uniform(0, 1, n)
        for i in range(n):
            rows.append({"date": d, "ticker": f"T{i}", "illiq": ill[i], "fwd_ret": slope * ill[i] + rng.normal(0, 0.01)})
    return pd.DataFrame(rows)


def test_fama_macbeth():
    r = lm.fama_macbeth_quintiles(_fm_panel())
    assert r["n_dates"] == 8 and len(r["quintile_fwd%"]) == 5 and r["quintile_fwd%"] == sorted(r["quintile_fwd%"])
    assert r["Q5_minus_Q1%"] > 5 and r["t_stat"] > 5 and r["avg_IC"] > 0.9 and r["monotonicity"] == 1.0
    neg = lm.fama_macbeth_quintiles(_fm_panel(slope=-0.1))
    assert neg["Q5_minus_Q1%"] < 0 and neg["t_stat"] < 0 and neg["monotonicity"] == -1.0


def test_fama_macbeth_thresholds():
    assert lm.fama_macbeth_quintiles(_fm_panel(n_dates=4)) == {"n_dates": 4}          # needs >= 5 dates
    assert lm.fama_macbeth_quintiles(_fm_panel(n_dates=5))["n_dates"] == 5
    assert lm.fama_macbeth_quintiles(_fm_panel(n=14)) == {"n_dates": 0}                # < q*3 names per date
    assert lm.fama_macbeth_quintiles(_fm_panel(n=15))["n_dates"] == 8
    tied = _fm_panel(); tied["illiq"] = 1.0
    assert lm.fama_macbeth_quintiles(tied)["n_dates"] == 0                             # all ties -> qcut collapses -> skipped
    nan_p = _fm_panel(); nan_p.loc[nan_p.index[:5], "illiq"] = np.nan
    assert lm.fama_macbeth_quintiles(nan_p)["n_dates"] == 8


def test_fm_constant_spread_gives_nan_t():
    rows = []
    for d in pd.date_range("2020-01-01", periods=6, freq="QS"):
        for i in range(15):
            rows.append({"date": d, "ticker": f"T{i}", "illiq": float(i), "fwd_ret": 0.001 * (i // 3)})
    r = lm.fama_macbeth_quintiles(pd.DataFrame(rows))
    assert np.isnan(r["t_stat"]) and r["Q5_minus_Q1%"] == 0.4                          # identical spread each date -> std 0


def test_universe(monkeypatch):
    idx = pd.date_range("2024-01-01", periods=260, freq="B")
    close = pd.DataFrame({f"S{i}": 10.0 for i in range(10)}, index=idx)
    vol = pd.DataFrame({f"S{i}": 1000.0 * (i + 1) for i in range(10)}, index=idx)
    monkeypatch.setattr(marketdata, "close_volume", lambda m: (close, vol))
    assert lm.universe(2) == ["S6", "S7"] and lm.universe(99) == ["S6", "S7", "S8", "S9"]
    monkeypatch.setattr(marketdata, "close_volume", lambda m: (None, None))
    assert lm.universe(5) == []


def test_build_panel_pit_and_filters():
    df = make_ohlcv(400, seed=1)
    short = make_ohlcv(100)
    bad = df.drop(columns=["Volume"])
    panel = lm.build_panel({"A": df, "SHORT": short, "NONE": None, "NOVOL": bad, "NOCLOSE": df[["Volume"]]}, years=5)
    assert set(panel["ticker"]) == {"A"}                                                # no volume -> NaN illiq -> dropped
    c = df["Close"]
    rows = panel[panel.ticker == "A"]
    assert list(rows["date"]) == [c.index[T] for T in range(252, 400 - 63, 63)]
    first = rows.iloc[0]
    assert first["fwd_ret"] == pytest.approx(c.iloc[252 + 63] / c.iloc[252] - 1)
    assert lm.build_panel({}, 5).empty


def test_build_panel_skips_bad_start_prices():
    df = make_ohlcv(400, seed=2)
    df.iloc[252, df.columns.get_loc("Close")] = np.nan
    panel = lm.build_panel({"A": df}, 5)
    assert df.index[252] not in set(panel["date"])


def test_lm_main(monkeypatch, capsys):
    panel = _fm_panel()
    ac = types.ModuleType("apiclient"); seen = {}
    ac.yf_download = lambda tk, period: (seen.update(tk=tk, period=period), {t: None for t in tk})[1]
    monkeypatch.setitem(sys.modules, "apiclient", ac)
    monkeypatch.setattr(lm, "universe", lambda n: [f"T{i}" for i in range(min(n, 3))])
    monkeypatch.setattr(lm, "build_panel", lambda prices, years, horizon: panel)
    monkeypatch.setattr(sys, "argv", ["lm", "--n", "2", "--years", "7", "--horizon", "21"])
    lm.main()
    out = capsys.readouterr()
    assert seen == {"tk": ["T0", "T1"], "period": "7y"} and "fetching 2 US tickers, 7y daily" in out.err
    assert "MULTI-YEAR (7y), Fama-MacBeth" in out.out and "SIGNIFICANT liquidity premium" in out.out and "Q1=" in out.out and "Q5=" in out.out
    assert "mean 21d fwd return by ILLIQ quintile" in out.out


def test_lm_main_insufficient_and_not_significant(monkeypatch, capsys):
    ac = types.ModuleType("apiclient"); ac.yf_download = lambda tk, period: {}
    monkeypatch.setitem(sys.modules, "apiclient", ac)
    monkeypatch.setattr(lm, "universe", lambda n: [])
    monkeypatch.setattr(lm, "build_panel", lambda prices, years, horizon: _fm_panel(n_dates=3))
    monkeypatch.setattr(sys, "argv", ["lm"])
    lm.main()
    assert "insufficient cross-sections" in capsys.readouterr().out
    monkeypatch.setattr(lm, "build_panel", lambda prices, years, horizon: _fm_panel(slope=-0.1))
    lm.main()
    assert "not significant" in capsys.readouterr().out


# ───────────────────────────── fx ─────────────────────────────
def test_market_currency_and_math():
    assert fx.market_currency("kr") == "KRW" and fx.market_currency("ZZ") == "USD" and fx.market_currency("US") == "USD"
    assert fx.convert_level("100", "0.5") == 50.0
    assert fx.combine_return(0.10, -0.05) == pytest.approx(0.045) and fx.combine_return(0.0, 0.0) == 0.0
    assert fx.combine_return(-1.0, 0.5) == -1.0


def test_normalize_cross_market():
    df = pd.DataFrame({"market": ["KR", "US", "ZZ", "DE"], "cap": [1000.0, 5.0, 7.0, 10.0]})
    out = fx.normalize_cross_market(df, "cap", "market", {"KRW": 0.001, "USD": 1.0, "EUR": 1.1})
    assert list(out["cap_usd"]) == [1.0, 5.0, 7.0, 11.0] and "cap_usd" not in df.columns
    out = fx.normalize_cross_market(df, "cap", "market", {"KRW": 0.001}, base="EUR")
    assert "cap_eur" in out.columns and out["cap_eur"].isna().sum() == 3 and out["cap_eur"].iloc[0] == 1.0


def test_load_rates(monkeypatch, tmp_path):
    monkeypatch.setattr(fx, "CACHE", str(tmp_path / "r.json"))
    assert fx.load_rates() == fx.SNAPSHOT_USD_PER and fx.load_rates() is not fx.SNAPSHOT_USD_PER
    (tmp_path / "r.json").write_text(json.dumps({"base": "USD", "rates": {"EUR": 2.0}}))
    assert fx.load_rates("USD") == {"EUR": 2.0}
    eur = fx.load_rates("EUR")                                                  # cached base differs -> derive from snapshot
    assert eur["EUR"] == 1.0 and eur["USD"] == round(1.0 / 1.08, 6) and eur["JPY"] == round(0.0064 / 1.08, 6)
    (tmp_path / "r.json").write_text("{bad")
    assert fx.load_rates() == fx.SNAPSHOT_USD_PER
    (tmp_path / "r.json").write_text(json.dumps({"base": "USD", "rates": {}}))
    assert fx.load_rates() == fx.SNAPSHOT_USD_PER                               # empty cache ignored
    assert fx.load_rates("XXX") == fx.SNAPSHOT_USD_PER                          # unknown base -> USD snapshot


def test_refresh_rates(monkeypatch, tmp_path):
    monkeypatch.setattr(fx, "CACHE", str(tmp_path / "r.json"))
    seen = {}
    ac = types.ModuleType("apiclient")

    def dl(symbols, period):
        seen.update(symbols=symbols, period=period)
        return {"EURUSD=X": pd.DataFrame({"Close": [1.1, np.nan, 1.2345678]}), "JPYUSD=X": pd.DataFrame({"Close": [np.nan]})}
    ac.yf_download = dl
    monkeypatch.setitem(sys.modules, "apiclient", ac)
    rates = fx.refresh_rates("USD")
    assert rates["USD"] == 1.0 and rates["EUR"] == 1.234568 and rates["JPY"] == fx.SNAPSHOT_USD_PER["JPY"]   # NaN-only -> snapshot
    assert rates["KRW"] == fx.SNAPSHOT_USD_PER["KRW"] and "USDUSD=X" not in seen["symbols"] and seen["period"] == "5d"
    assert json.load(open(tmp_path / "r.json"))["base"] == "USD" and "EURUSD=X" in seen["symbols"]
    eur = fx.refresh_rates("EUR")
    assert "EUR" in eur and eur["EUR"] == 1.0 and "EUREUR=X" not in seen["symbols"]


def test_fx_main(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(fx, "CACHE", str(tmp_path / "r.json"))
    monkeypatch.setattr(sys, "argv", ["fx", "--convert", "1000000", "--from", "kr"])
    fx.main()
    assert "1,000,000.00 KRW  =  730.00 USD" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["fx", "--convert", "5"])                  # no --from -> falls through to the table
    fx.main()
    out = capsys.readouterr().out
    assert "=== USD-per-unit rates (17 currencies) ===" in out and "EUR" in out and "markets: DE,EU,FI" in out
    monkeypatch.setattr(fx, "refresh_rates", lambda base: {"USD": 1.0, "EUR": 2.0})
    monkeypatch.setattr(sys, "argv", ["fx", "--refresh", "--base", "USD"])
    fx.main()
    cap = capsys.readouterr()
    assert "refreshed 2 rates" in cap.err and "2 currencies" in cap.out
    monkeypatch.setattr(sys, "argv", ["fx", "--convert", "1", "--from", "XX"])
    fx.main()
    assert "1.00 USD  =  1.00 USD" in capsys.readouterr().out


# ───────────────────────────── sector_rotation ─────────────────────────────
def test_member_momentum():
    c = pd.Series(np.arange(1.0, 301.0))
    assert sr.member_momentum(c) == pytest.approx(c.iloc[-22] / c.iloc[-252] - 1)         # 12-1 skip
    assert np.isnan(sr.member_momentum(c.iloc[:252])) and not np.isnan(sr.member_momentum(c.iloc[:253]))
    z = c.copy(); z.iloc[-252] = 0.0
    assert np.isnan(sr.member_momentum(z))
    assert sr.member_momentum(c, lookback=10, skip=2) == pytest.approx(c.iloc[-3] / c.iloc[-10] - 1)
    assert sr.member_momentum(pd.concat([c, pd.Series([np.nan])], ignore_index=True)) == sr.member_momentum(c)


def test_rank_groups():
    m = pd.DataFrame({"g": ["A"] * 4 + ["B"] * 3 + ["C"] * 2, "momentum": [0.1, 0.2, -0.1, 0.3, 0.5, 0.6, 0.7, 9.0, 9.0]})
    r = sr.rank_groups(m, "g")
    assert list(r["g"]) == ["B", "A"] and list(r["rank"]) == [1, 2]                      # C (n=2) dropped
    a = r.set_index("g").loc["A"]
    assert a["mom_median%"] == 15.0 and a["mom_mean%"] == 12.5 and a["breadth%"] == 75.0 and a["n"] == 4
    assert len(sr.rank_groups(m, "g", min_members=2)) == 3
    nan = m.copy(); nan.loc[0, "momentum"] = np.nan
    assert sr.rank_groups(nan, "g").set_index("g").loc["A", "n"] == 3
    assert sr.rank_groups(m, "g", min_members=99).empty


@pytest.fixture
def rot(monkeypatch, tmp_path):
    seed = tmp_path / "seed"; seed.mkdir()
    for m in ("US", "JP"):
        (seed / f"cleaned_long_{m}.parquet").write_bytes(b"x")
    (seed / "readme.txt").write_text("x")
    comp_p = tmp_path / "c.parquet"; comp_p.write_bytes(b"x")
    comp = pd.DataFrame({"ticker": ["A.NS", "B", "C", "D", "E", "A"], "country": "x", "sector": "S", "industry": ["Soft"] * 4 + ["Hard", "Dup"],
                         "segment": "g"})
    px = []
    for i, s in enumerate(["A", "B", "C", "D", "E", "NEWIPO"]):
        n = 300 if s != "NEWIPO" else 100
        d = make_ohlcv(n, drift=0.001 * (i + 1), seed=i)
        px.append(pd.DataFrame({"Date": d.index, "Symbol": s, "Close": d["Close"].values}))
    px = pd.concat(px).sample(frac=1.0, random_state=0)
    monkeypatch.setattr(sr, "SEED", str(seed)); monkeypatch.setattr(sr, "COMPANIES", str(comp_p))
    monkeypatch.setattr(sr.pd, "read_parquet", lambda p: comp if p == str(comp_p) else px)
    return comp


def test_build_member_table(rot):
    t = sr.build_member_table("industry", "US")
    assert set(t["ticker"]) == {"A", "B", "C", "D", "E"} and set(t["market"]) == {"US"}               # short NEWIPO skipped
    assert t.set_index("key").loc["B", "industry"] == "Soft" and t.set_index("key").loc["A", "industry"] == "Soft"   # first duplicate key wins
    allm = sr.build_member_table("sector", None)
    assert set(allm["market"]) == {"US", "JP"} and len(allm) == 10
    unk = sr.build_member_table("industry", "US")
    assert (unk["industry"] != "Unknown").all()


def test_build_member_table_unknown_and_missing(rot, monkeypatch, tmp_path):
    comp = rot.iloc[:0]
    monkeypatch.setattr(sr.pd, "read_parquet", lambda p: comp if "c.parquet" in str(p) else pd.DataFrame(
        {"Date": pd.date_range("2020-01-01", periods=300), "Symbol": "ZZZ", "Close": np.linspace(1, 2, 300)}))
    assert sr.build_member_table("industry", "US")["industry"].iloc[0] == "Unknown"
    assert sr.build_member_table("industry", "XX").empty                                              # no parquet for XX


def test_sr_main(rot, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["sr", "--by", "industry", "--market", "US", "--min-members", "1", "--top", "1"])
    sr.main()
    cap = capsys.readouterr()
    assert "SECTOR ROTATION by industry — US (2 groups)" in cap.err and "Leaders (top group's names):" in cap.err
    rows = [l for l in cap.out.splitlines() if l.strip().startswith("1 ")]
    assert len(rows) == 1
    monkeypatch.setattr(sys, "argv", ["sr"])
    sr.main()
    assert "all markets" in capsys.readouterr().err


def test_sr_main_errors(rot, monkeypatch, tmp_path):
    monkeypatch.setattr(sr, "COMPANIES", str(tmp_path / "none.parquet"))
    monkeypatch.setattr(sys, "argv", ["sr"])
    with pytest.raises(SystemExit, match="build_industry_parquet.py first"):
        sr.main()
    p = tmp_path / "c2.parquet"; p.write_bytes(b"x")
    monkeypatch.setattr(sr, "COMPANIES", str(p))
    monkeypatch.setattr(sr, "build_member_table", lambda by, m: pd.DataFrame())
    with pytest.raises(SystemExit, match="no member momentum computed"):
        sr.main()
