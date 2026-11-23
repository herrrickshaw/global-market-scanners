"""dvm_composite.py + esg_screen.py (SQLite/parquet/yfinance faked)."""
import os
import sqlite3
import sys
import types

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dvm_composite as dc  # noqa: E402
import esg_screen as esg  # noqa: E402
from conftest_helpers import make_ohlcv  # noqa: E402


def test_momentum():
    assert np.isnan(dc.momentum(pd.Series(np.arange(199.0) + 1)))
    up = dc.momentum(make_ohlcv(300, drift=0.002, seed=1)["Close"])
    dn = dc.momentum(make_ohlcv(300, drift=-0.002, seed=1)["Close"])
    assert 0 <= dn < up <= 100


def _r(**kw):
    base = dict(roe=np.nan, de=np.nan, rev_growth=np.nan, op_margin=np.nan, earn_growth=np.nan)
    base.update(kw)
    return pd.Series(base)


@pytest.mark.parametrize("roe,exp", [(25, 90), (20, 75), (17, 75), (15, 55), (12, 55), (10, 35), (5, 35), (0, 15), (-5, 15)])
def test_durability_roe_bands(roe, exp):
    assert dc.durability(_r(roe=roe)) == exp


@pytest.mark.parametrize("de,exp", [(0.1, 90), (0.5, 70), (0.99, 70), (1.0, 45), (1.99, 45), (2.0, 20)])
def test_durability_de_bands(de, exp):
    assert dc.durability(_r(de=de)) == exp


def test_durability_growth_margin_earn_and_mean():
    assert dc.durability(_r(rev_growth=16)) == 85 and dc.durability(_r(rev_growth=15)) == 60
    assert dc.durability(_r(rev_growth=5)) == 40 and dc.durability(_r(rev_growth=0)) == 20
    assert dc.durability(_r(op_margin=21)) == 85 and dc.durability(_r(op_margin=20)) == 60
    assert dc.durability(_r(op_margin=10)) == 40 and dc.durability(_r(op_margin=0)) == 20
    assert dc.durability(_r(earn_growth=0.1)) == 75 and dc.durability(_r(earn_growth=0)) == 35
    assert dc.durability(_r(roe=25, de=0.1, rev_growth=16, op_margin=21, earn_growth=1)) == pytest.approx((90 + 90 + 85 + 85 + 75) / 5)
    assert np.isnan(dc.durability(_r()))


def _fund_db(path, rows):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE fund(ticker TEXT, market TEXT, pe TEXT, pb TEXT, roe TEXT, roa TEXT, de TEXT, rev_growth TEXT,"
                " earn_growth TEXT, op_margin TEXT, div_yield TEXT, mktcap TEXT, sector TEXT)")
    con.executemany("INSERT INTO fund VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit(); con.close()


def _row(t, m, pe, pb, roe, de="0.3", rg="20", om="25", eg="5"):
    return (t, m, pe, pb, roe, "5", de, rg, eg, om, "1", "1e9", "Tech")


@pytest.fixture
def setup(monkeypatch, tmp_path):
    db = str(tmp_path / "fund.db")
    rows = [_row(f"A{i}", "US", str(10 + i), str(1 + i * 0.5), str(25 - i)) for i in range(6)]
    rows += [_row("NOPX", "US", "10", "1", "20"), _row("Z0", "ZZ", "10", "1", "20"),
             _row("NEG", "US", "-5", "2", "20"), _row("BAD", "US", "n/a", "x", "x", de="x", rg="x", om="x", eg="x")]
    _fund_db(db, rows)
    seed = tmp_path / "seed"; seed.mkdir()
    (seed / "cleaned_long_US.parquet").write_bytes(b"x")
    monkeypatch.setattr(dc, "CACHE", db)
    monkeypatch.setattr(dc, "SEED", str(seed))
    px = []
    for i, t in enumerate(["A0", "A1", "A2", "A3", "A4", "A5", "NEG", "BAD", "OTHER"]):
        d = make_ohlcv(300, drift=0.001 if i % 2 == 0 else -0.001, seed=i)
        px.append(pd.DataFrame({"Date": d.index, "Symbol": t, "Close": d["Close"].values}))
    monkeypatch.setattr(dc.pd, "read_parquet", lambda p: pd.concat(px).sample(frac=1.0, random_state=0))
    return tmp_path


def test_main_builds_composite(monkeypatch, setup, capsys):
    out = str(setup / "out.db")
    monkeypatch.setattr(sys, "argv", ["dc", "--db", out])
    dc.main()
    con = sqlite3.connect(out)
    df = pd.read_sql("SELECT * FROM dvm_composite", con); con.close()
    assert set(df["ticker"]) <= {"A0", "A1", "A2", "A3", "A4", "A5", "NEG"} and "NOPX" not in set(df["ticker"]) and "Z0" not in set(df["ticker"])
    assert "_ey" not in df.columns and df["composite"].is_monotonic_decreasing
    assert df["code"].map(lambda c: len(c) == 3 and set(c) <= set("GB")).all()
    for _, r in df.iterrows():
        assert r["code"] == ("G" if r["D"] >= 50 else "B") + ("G" if r["V"] >= 50 else "B") + ("G" if r["M"] >= 50 else "B")
        assert r["label"] == dc.LABELS[r["code"]] and r["composite"] == round((r["D"] + r["V"] + r["M"]) / 3, 1)
    cap = capsys.readouterr()
    assert "stocks with fundamentals across" in cap.err and "GLOBAL DVM COMPOSITE" in cap.out and "classification:" in cap.out
    assert "top GGG Strong Performers" in cap.out and "saved to" in cap.out


def test_main_code_filter_and_missing_cache(monkeypatch, setup, capsys, tmp_path):
    out = str(setup / "out.db")
    monkeypatch.setattr(sys, "argv", ["dc", "--db", out, "--code", "BBB"])
    dc.main()
    assert "code=BBB" in capsys.readouterr().out
    monkeypatch.setattr(dc, "CACHE", str(tmp_path / "nope.db"))
    monkeypatch.setattr(sys, "argv", ["dc"])
    dc.main()
    assert "no fundamentals_cache.db" in capsys.readouterr().err


def test_main_no_parquet_or_no_rows(monkeypatch, setup, capsys):
    monkeypatch.setattr(dc, "SEED", str(setup / "empty"))
    monkeypatch.setattr(sys, "argv", ["dc", "--db", str(setup / "o.db")])
    dc.main()
    assert "no stocks with both momentum + durability" in capsys.readouterr().err


# ───────────────────────────── esg_screen ─────────────────────────────
@pytest.mark.parametrize("risk,grade", [(0, "negligible"), (9.99, "negligible"), (10, "low"), (19.9, "low"), (20, "medium"),
                                        (29.9, "medium"), (30, "high"), (39.9, "high"), (40, "severe"), (80, "severe"),
                                        (None, "n/a"), (float("nan"), "n/a"), (float("inf"), "n/a")])
def test_esg_grade(risk, grade):
    assert esg.esg_grade(risk) == grade


def test_esg_score():
    assert esg.esg_score_0_100(0) == 100.0 and esg.esg_score_0_100(25) == 50.0 and esg.esg_score_0_100(50) == 0.0
    assert esg.esg_score_0_100(80) == 0.0 and esg.esg_score_0_100(12.34) == 75.3
    assert np.isnan(esg.esg_score_0_100(None)) and np.isnan(esg.esg_score_0_100(float("nan")))


def test_rank_esg():
    df = pd.DataFrame({"ticker": ["B", "A", "C", "D"], "total_esg": [30.0, 5.0, np.nan, 45.0]})
    r = esg.rank_esg(df)
    assert list(r["ticker"]) == ["A", "B", "D"] and list(r["grade"]) == ["negligible", "high", "severe"]
    assert list(r["esg_score"]) == [90.0, 40.0, 10.0]
    assert len(df) == 4 and "grade" not in df.columns


def _install_yf(monkeypatch, sust_by_ticker, robust_fail=False):
    ac = types.ModuleType("apiclient")
    ac.robust = (lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rate"))) if robust_fail else (lambda src, fn, retries=5: fn())
    monkeypatch.setitem(sys.modules, "apiclient", ac)
    yf = types.ModuleType("yfinance")
    yf.Ticker = lambda t: types.SimpleNamespace(sustainability=sust_by_ticker.get(t))
    monkeypatch.setitem(sys.modules, "yfinance", yf)


def _sust(**kw):
    vals = {"totalEsg": 22.5, "environmentScore": 5.0, "socialScore": 8.0, "governanceScore": 9.5, "highestControversy": 3.0}
    vals.update(kw)
    return pd.DataFrame({"Value": pd.Series(vals)})


def test_fetch_esg_yf(monkeypatch):
    _install_yf(monkeypatch, {"AAPL": _sust(), "SPARSE": pd.DataFrame({"Value": pd.Series({"totalEsg": 10.0, "socialScore": np.nan})}),
                              "EMPTY": pd.DataFrame()})
    r = esg.fetch_esg_yf("AAPL")
    assert r == {"ticker": "AAPL", "total_esg": 22.5, "env": 5.0, "social": 8.0, "gov": 9.5, "controversy": 3.0}
    s = esg.fetch_esg_yf("SPARSE")
    assert s["total_esg"] == 10.0 and np.isnan(s["social"]) and np.isnan(s["env"])
    assert esg.fetch_esg_yf("EMPTY") == {} and esg.fetch_esg_yf("NONE") == {}
    _install_yf(monkeypatch, {}, robust_fail=True)
    assert esg.fetch_esg_yf("AAPL") == {}


def test_esg_main(monkeypatch, capsys):
    _install_yf(monkeypatch, {"AAPL": _sust(totalEsg=15.0), "XOM": _sust(totalEsg=41.0)})
    monkeypatch.setattr(sys, "argv", ["esg", "--tickers", "aapl, xom ,,zzz"])
    esg.main()
    out = capsys.readouterr().out
    assert "ESG RISK SCREENER (yfinance / Sustainalytics) — 2 names" in out
    rows = [l.split() for l in out.splitlines() if l.strip().startswith(("AAPL", "XOM"))]
    assert [r[0] for r in rows] == ["AAPL", "XOM"] and rows[0][3] == "low" and rows[1][3] == "severe"
    monkeypatch.setattr(sys, "argv", ["esg", "--tickers", "zzz"])
    esg.main()
    assert "no ESG data fetched" in capsys.readouterr().out
