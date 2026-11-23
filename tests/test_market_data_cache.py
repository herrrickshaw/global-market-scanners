"""market_data_cache.py: parquet-backed OHLC/financials/index cache (HOME redirected, parquet + yfinance faked)."""
import importlib
import os
import pickle
import sys
import types
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _bars(n=30, end=None, base=100.0):
    end = end or pd.Timestamp(date.today())
    idx = pd.bdate_range(end=end, periods=n)
    c = base + np.arange(n, dtype=float)
    return pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": 1000.0}, index=idx)


def _multi(frames):
    return pd.concat({f: pd.concat({t: d[f] for t, d in frames.items()}, axis=1)
                      for f in ["Open", "High", "Low", "Close", "Volume"]}, axis=1)


class FakeYF:
    def __init__(self):
        self.calls, self.fail = [], []
        self.data = {}

    def download(self, tickers, **kw):
        self.calls.append((tickers, kw))
        if self.fail:
            raise self.fail.pop(0)
        names = [tickers] if isinstance(tickers, str) else list(tickers)
        frames = {t: self.data[t] for t in names if t in self.data}
        if not frames:
            return pd.DataFrame()
        return _multi(frames)


@pytest.fixture
def mc(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delitem(sys.modules, "market_data_cache", raising=False)
    mod = importlib.import_module("market_data_cache")
    yf = FakeYF()
    monkeypatch.setattr(mod, "yf", yf, raising=False)
    monkeypatch.setattr(mod, "_YF_OK", True)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)

    def to_parquet(self, path, **kw):
        with open(path, "wb") as f:
            pickle.dump(self, f)

    def read_parquet(path):
        with open(path, "rb") as f:
            return pickle.load(f)
    monkeypatch.setattr(pd.DataFrame, "to_parquet", to_parquet)
    monkeypatch.setattr(mod.pd, "read_parquet", read_parquet)
    mod.yf_fake = yf
    yield mod
    sys.modules.pop("market_data_cache", None)


def test_module_creates_cache_dirs_under_home(mc, tmp_path):
    assert str(mc.CACHE_ROOT) == str(tmp_path / "Downloads" / "market_cache")
    for d in (mc.OHLC_DIR, mc.FUND_DIR, mc.INDEX_DIR, mc.META_DIR):
        assert d.is_dir()


def test_meta_roundtrip_and_corrupt(mc):
    c = mc.MarketCache(verbose=False)
    assert c._meta == {}
    df = _bars(5)
    c._update_meta("ohlc:A", mc.OHLC_DIR / "A.parquet", df)
    m = c._meta["ohlc:A"]
    assert m["rows"] == 5 and m["from"] == str(df.index.min().date()) and m["to"] == str(df.index.max().date())
    assert mc.MarketCache(verbose=False)._meta["ohlc:A"]["rows"] == 5              # persisted
    c._update_meta("ohlc:E", mc.OHLC_DIR / "E.parquet", df.iloc[:0])
    assert c._meta["ohlc:E"]["from"] is None and c._meta["ohlc:E"]["rows"] == 0
    mc.META_FILE.write_text("{not json")
    assert mc.MarketCache(verbose=False)._meta == {}


def test_staleness_and_last_date(mc):
    c = mc.MarketCache(verbose=False)
    assert c._is_stale("nope", 20) is True
    c._meta["k"] = {"updated": (datetime.now() - timedelta(hours=19)).isoformat()}
    assert c._is_stale("k", 20) is False
    c._meta["k"] = {"updated": (datetime.now() - timedelta(hours=21)).isoformat()}
    assert c._is_stale("k", 20) is True
    c._meta["k"] = {"updated": "garbage"}
    assert c._is_stale("k", 20) is True
    c._meta["d"] = {"to": "2024-05-06"}
    assert c._last_cached_date("d") == date(2024, 5, 6)
    c._meta["d"] = {"to": "bad"}
    assert c._last_cached_date("d") is None and c._last_cached_date("zz") is None
    c._meta["d"] = {"to": None}
    assert c._last_cached_date("d") is None


def test_ohlc_path_sanitised(mc):
    c = mc.MarketCache(verbose=False)
    assert c._ohlc_path("A/B:C").name == "A_B_C.parquet" and c._ohlc_path("X.NS").parent == mc.OHLC_DIR


def test_clean_ohlc(mc):
    assert mc.MarketCache._clean_ohlc(pd.DataFrame()).empty
    df = _bars(5)
    df.index = df.index.astype(str)
    df.loc["nonsense"] = df.iloc[0]
    out = mc.MarketCache._clean_ohlc(df)
    assert isinstance(out.index, pd.DatetimeIndex) and out.index.is_monotonic_increasing and len(out) == 5
    bad = _bars(3); bad["Close"] = -1.0
    assert mc.MarketCache._clean_ohlc(bad).empty                                   # nothing usable survives


def test_clean_ohlc_fallback_without_central_gate(mc, monkeypatch):
    monkeypatch.setattr(mc, "_clean_ohlcv_central", None)
    df = _bars(4)
    df.iloc[1, 0] = np.nan
    dup = pd.concat([df, df.iloc[[0]]])
    out = mc.MarketCache._clean_ohlc(dup)
    assert len(out) == 4 and not out.isna().any().any()                            # dedup + ffill/bfill


def test_write_parquet_atomic_and_cleanup(mc, tmp_path, monkeypatch):
    p = tmp_path / "x.parquet"
    mc.MarketCache._write_parquet(p, _bars(3))
    assert p.exists() and not p.with_suffix(".tmp").exists()

    def boom(self, path, **kw):
        open(path, "wb").write(b"partial")
        raise OSError("disk full")
    monkeypatch.setattr(pd.DataFrame, "to_parquet", boom)
    with pytest.raises(OSError):
        mc.MarketCache._write_parquet(tmp_path / "y.parquet", _bars(3))
    assert not (tmp_path / "y.tmp").exists() and not (tmp_path / "y.parquet").exists()


def test_get_ohlc_cold_then_fresh_from_memory(mc, capsys):
    mc.yf_fake.data["AAA"] = _bars(30)
    c = mc.MarketCache(verbose=True)
    df = c.get_ohlc("AAA")
    assert len(df) == 30 and c._ohlc_path("AAA").exists() and "Cache created: AAA 30 rows" in capsys.readouterr().out
    kw = mc.yf_fake.calls[0][1]
    assert kw["start"] == date.today() - timedelta(days=mc.OHLC_YEARS * 365 + 30) and kw["auto_adjust"] is True
    again = c.get_ohlc("AAA")
    assert len(mc.yf_fake.calls) == 1 and len(again) == 30                         # fresh -> disk read, no download
    mem = c.get_ohlc("AAA")
    assert mem is c._mem["AAA"]
    assert c.get_ohlc("EMPTY").empty and not c._ohlc_path("EMPTY").exists()


def test_get_ohlc_incremental_update(mc, capsys):
    old = _bars(20, end=pd.Timestamp(date.today()) - pd.Timedelta(days=10))
    mc.yf_fake.data["AAA"] = old
    c = mc.MarketCache(verbose=True)
    c.get_ohlc("AAA")
    c._meta["ohlc:AAA"]["updated"] = (datetime.now() - timedelta(hours=48)).isoformat()   # force stale
    new = _bars(5, end=pd.Timestamp(date.today()), base=500.0)
    mc.yf_fake.data["AAA"] = new
    out = c.get_ohlc("AAA")
    start = mc.yf_fake.calls[-1][1]["start"]
    assert start == old.index.max().date() + timedelta(days=1)
    assert len(out) > 20 and out.index.max() == new.index.max() and "Cache updated: AAA +" in capsys.readouterr().out
    c._meta["ohlc:AAA"]["updated"] = (datetime.now() - timedelta(hours=48)).isoformat()
    mc.yf_fake.data["AAA"] = pd.DataFrame()
    n = len(mc.yf_fake.calls)
    assert len(c.get_ohlc("AAA")) == len(out) and len(mc.yf_fake.calls) == n     # already current through today -> no download


def test_get_ohlc_stale_but_up_to_date_skips_download(mc):
    mc.yf_fake.data["AAA"] = _bars(10)                                              # ends today
    c = mc.MarketCache(verbose=False)
    c.get_ohlc("AAA")
    c._meta["ohlc:AAA"]["updated"] = (datetime.now() - timedelta(hours=48)).isoformat()
    n = len(mc.yf_fake.calls)
    assert len(c.get_ohlc("AAA")) == 10 and len(mc.yf_fake.calls) == n


def test_get_ohlc_force_redownloads(mc):
    mc.yf_fake.data["AAA"] = _bars(10)
    c = mc.MarketCache(verbose=False)
    c.get_ohlc("AAA")
    c.get_ohlc("AAA", force=True)
    assert len(mc.yf_fake.calls) == 2


def test_get_ohlc_bulk_all_paths(mc, capsys):
    c = mc.MarketCache(verbose=True)
    mc.yf_fake.data.update({"COLD": _bars(20), "FRESH": _bars(20), "STALE": _bars(15, end=pd.Timestamp(date.today()) - pd.Timedelta(days=9)),
                            "BROKEN": _bars(20)})
    c.get_ohlc("FRESH"); c.get_ohlc("STALE"); c.get_ohlc("BROKEN")
    c._meta["ohlc:STALE"]["updated"] = (datetime.now() - timedelta(hours=48)).isoformat()
    c._mem.pop("FRESH", None)
    c._ohlc_path("BROKEN").write_bytes(b"corrupt")                                  # unreadable parquet -> re-downloaded
    mc.yf_fake.data["STALE"] = _bars(4, end=pd.Timestamp(date.today()), base=900.0)
    mc.yf_fake.calls.clear()
    out = c.get_ohlc_bulk(["COLD", "FRESH", "STALE", "BROKEN", "GHOST"])
    assert set(out) == {"COLD", "FRESH", "STALE", "BROKEN"}
    text = capsys.readouterr().out
    assert "Cache: 2 fresh | 1 incremental | 1 cold-start" in text or "fresh" in text
    assert "Cache result: 4/5 tickers loaded" in text and "Bulk download complete" in text
    assert out["STALE"].index.max() == mc.yf_fake.data["STALE"].index.max() and len(out["STALE"]) > 4
    starts = [call[1]["start"] for call in mc.yf_fake.calls]
    assert date.today() - timedelta(days=5) in starts                               # incremental window is 5 days


def test_get_ohlc_bulk_uses_memory_and_force(mc):
    c = mc.MarketCache(verbose=False)
    mc.yf_fake.data["A"] = _bars(10)
    c.get_ohlc_bulk(["A"])
    n = len(mc.yf_fake.calls)
    assert set(c.get_ohlc_bulk(["A"])) == {"A"} and len(mc.yf_fake.calls) == n
    c.get_ohlc_bulk(["A"], force=True)
    assert len(mc.yf_fake.calls) == n + 1


def test_get_ohlc_bulk_incremental_error_falls_back(mc, monkeypatch):
    c = mc.MarketCache(verbose=False)
    mc.yf_fake.data["A"] = _bars(10)
    c.get_ohlc("A")
    c._meta["ohlc:A"]["updated"] = (datetime.now() - timedelta(hours=48)).isoformat()
    monkeypatch.setattr(c, "_clean_ohlc", lambda df: (_ for _ in ()).throw(ValueError("x")))
    out = c.get_ohlc_bulk(["A"])
    assert "A" in out


def test_bulk_yf_download_variants(mc, capsys):
    c = mc.MarketCache(verbose=False)
    yf = mc.yf_fake
    yf.data.update({"A": _bars(5), "B": _bars(5)})
    res = c._bulk_yf_download(["A", "B", "C"], start=date(2024, 1, 1))
    assert set(res) == {"A", "B"} and list(res["A"].columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert "OK (2/3)" in capsys.readouterr().out
    assert c._bulk_yf_download(["Z"], start=date(2024, 1, 1)) == {} and "empty" in capsys.readouterr().out
    mc.BATCH_SIZE = 2
    yf.calls.clear()
    c._bulk_yf_download(["A", "B", "C"], start=date(2024, 1, 1))
    assert [len(x[0]) for x in yf.calls] == [2, 1]
    mc.BATCH_SIZE = 100


def test_bulk_yf_download_flat_columns_rate_limit_and_error(mc, capsys, monkeypatch):
    c = mc.MarketCache(verbose=False)
    flat = _bars(5)
    monkeypatch.setattr(mc.yf_fake, "download", lambda t, **k: flat)
    assert set(c._bulk_yf_download(["Q"], start=date(2024, 1, 1))) == {"Q"}
    sleeps = []
    monkeypatch.setattr(mc.time, "sleep", lambda s: sleeps.append(s))
    n = []

    def dl(t, **k):
        n.append(1)
        if len(n) < 3:
            raise RuntimeError("429 Too Many Requests")
        return flat
    monkeypatch.setattr(mc.yf_fake, "download", dl)
    assert "Q" in c._bulk_yf_download(["Q"], start=date(2024, 1, 1)) and sleeps == [30, 60]
    capsys.readouterr()
    monkeypatch.setattr(mc.yf_fake, "download", lambda t, **k: (_ for _ in ()).throw(ValueError("bad")))
    assert c._bulk_yf_download(["Q"], start=date(2024, 1, 1)) == {} and "ERROR: bad" in capsys.readouterr().out
    monkeypatch.setattr(mc, "_YF_OK", False)
    assert c._bulk_yf_download(["Q"], start=date(2024, 1, 1)) == {}


def test_yf_download_single(mc, monkeypatch):
    c = mc.MarketCache(verbose=False)
    mc.yf_fake.data["A"] = _bars(5)
    out = c._yf_download_single("A", date(2024, 1, 1))
    assert len(out) == 5 and list(out.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert c._yf_download_single("NONE", date(2024, 1, 1)).empty
    sleeps = []
    monkeypatch.setattr(mc.time, "sleep", lambda s: sleeps.append(s))
    mc.yf_fake.fail = [RuntimeError("Rate limited"), RuntimeError("429")]
    assert len(c._yf_download_single("A", date(2024, 1, 1))) == 5 and sleeps == [15, 30]
    mc.yf_fake.fail = [ValueError("other")]
    assert c._yf_download_single("A", date(2024, 1, 1)).empty and len(sleeps) == 2        # non-rate error: give up, no sleep
    monkeypatch.setattr(mc, "_YF_OK", False)
    assert c._yf_download_single("A", date(2024, 1, 1)).empty


def _fake_ticker(monkeypatch, mc, **attrs):
    monkeypatch.setattr(mc.yf_fake, "Ticker", lambda t: types.SimpleNamespace(**attrs), raising=False)


def _fin(rows):
    cols = pd.to_datetime(["2024-12-31", "2023-12-31"])
    return pd.DataFrame(rows, index=cols[:0].tolist() or None, columns=cols) if False else pd.DataFrame(
        {cols[0]: [r[0] for r in rows.values()], cols[1]: [r[1] for r in rows.values()]}, index=list(rows))


def test_first_df(mc):
    good = pd.DataFrame({"a": [1]})
    t = types.SimpleNamespace(e=pd.DataFrame(), x="no", g=good)
    assert mc._first_df(t, "missing", "e", "x", "g") is good and mc._first_df(t, "e") is None


def test_get_financials_annual_cache_and_quarterly(mc, monkeypatch):
    c = mc.MarketCache(verbose=False)
    _fake_ticker(monkeypatch, mc, income_stmt=_fin({"Net Income": (10.0, 8.0)}), balance_sheet=_fin({"Total Assets": (100.0, 90.0)}),
                 cash_flow=None, cashflow=pd.DataFrame(), quarterly_income_stmt=_fin({"Net Income": (3.0, 2.0)}))
    r = c.get_financials("AAA")
    assert set(r) == {"income_stmt", "balance_sheet"} and "fund:AAA:annual" in c._meta
    monkeypatch.setattr(mc.yf_fake, "Ticker", lambda t: (_ for _ in ()).throw(AssertionError("must hit cache")), raising=False)
    cached = c.get_financials("AAA")
    assert set(cached) == {"income_stmt", "balance_sheet", "cash_flow"} or set(cached) == {"income_stmt", "balance_sheet"}
    assert isinstance(cached["income_stmt"].columns, pd.DatetimeIndex) and cached["income_stmt"].loc["Net Income"].iloc[0] == 10.0
    _fake_ticker(monkeypatch, mc, quarterly_income_stmt=_fin({"Net Income": (3.0, 2.0)}))
    q = c.get_financials("AAA", freq="quarterly")
    assert list(q) == ["quarterly_income"]
    assert (mc.FUND_DIR / "AAA_quarterly.parquet").exists()


def test_get_financials_force_errors_and_empty(mc, monkeypatch, capsys):
    c = mc.MarketCache(verbose=True)
    _fake_ticker(monkeypatch, mc, income_stmt=_fin({"Net Income": (1.0, 2.0)}))
    c.get_financials("B")
    _fake_ticker(monkeypatch, mc, income_stmt=_fin({"Net Income": (5.0, 2.0)}))
    assert c.get_financials("B", force=True)["income_stmt"].iloc[0, 0] == 5.0
    monkeypatch.setattr(mc.yf_fake, "Ticker", lambda t: (_ for _ in ()).throw(RuntimeError("boom")), raising=False)
    assert c.get_financials("C") == {} and "Financial fetch failed for C: boom" in capsys.readouterr().out
    _fake_ticker(monkeypatch, mc)
    assert c.get_financials("D") == {} and not (mc.FUND_DIR / "D_annual.parquet").exists()
    (mc.FUND_DIR / "E_annual.parquet").write_bytes(b"corrupt")
    c._meta["fund:E:annual"] = {"updated": datetime.now().isoformat()}
    _fake_ticker(monkeypatch, mc, income_stmt=_fin({"Net Income": (1.0, 2.0)}))
    assert "income_stmt" in c.get_financials("E")                                    # corrupt cache -> refetch
    monkeypatch.setattr(mc, "_YF_OK", False)
    assert c.get_financials("F") == {}


def test_get_index(mc, capsys):
    c = mc.MarketCache(verbose=True)
    n = 260
    idx = pd.bdate_range(end=pd.Timestamp(date.today()), periods=n)
    df = pd.DataFrame({"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": np.linspace(100, 200, n), "Volume": 1.0}, index=idx)
    mc.yf_fake.data["^NSEI"] = df
    out = c.get_index("^NSEI")
    assert {"dma50", "dma200", "dma200_sl", "vol20"} <= set(out.columns) and out["dma200"].iloc[-1] == pytest.approx(df["Close"].iloc[-200:].mean())
    assert (mc.INDEX_DIR / "NSEI.parquet").exists() and c._meta["index:^NSEI"]["rows"] == n and "Index cached: ^NSEI 260 bars" in capsys.readouterr().out
    calls = len(mc.yf_fake.calls)
    assert len(c.get_index("^NSEI")) == n and len(mc.yf_fake.calls) == calls
    c.get_index("^NSEI", force=True)
    assert len(mc.yf_fake.calls) == calls + 1
    assert mc.yf_fake.calls[-1][1]["start"] == date.today() - timedelta(days=mc.INDEX_YEARS * 365 + 30)
    assert c.get_index("^NONE").empty


def test_report_and_purge(mc, capsys):
    c = mc.MarketCache(verbose=False)
    assert c.report().empty and "Cache is empty" in capsys.readouterr().out
    mc.yf_fake.data.update({"AAA": _bars(5), "BBB": _bars(5)})
    c.get_ohlc("AAA"); c.get_ohlc("BBB")
    c._meta["fund:AAA:annual"] = {"updated": (datetime.now() - timedelta(days=3)).isoformat()}
    c._meta["index:^X"] = {"updated": (datetime.now() - timedelta(hours=5)).isoformat()}
    c._meta["odd"] = {"updated": "bad"}
    c._meta["noupd"] = {}
    r = c.report().set_index("Key")
    out = capsys.readouterr().out
    assert "2 OHLC | 1 fundamental | 1 index" in out and r.loc["fund:AAA:annual", "Age"] == "3d" and r.loc["index:^X", "Age"] == "5h"
    assert r.loc["ohlc:AAA", "Age"] == "0m" and r.loc["odd", "Age"] == "—" and r.loc["noupd", "Age"] == "—" and r.loc["ohlc:AAA", "Rows"] == 5
    c.purge("AAA")
    assert not c._ohlc_path("AAA").exists() and c._ohlc_path("BBB").exists() and not any("AAA" in k for k in c._meta)
    c.purge()
    assert not any(mc.OHLC_DIR.glob("*.parquet")) and c._meta == {} and "Cache wiped" in capsys.readouterr().out


def test_get_cache_singleton_and_warm_cache(mc, monkeypatch, capsys):
    monkeypatch.setattr(mc, "_CACHE_INSTANCE", None)
    a = mc.get_cache()
    assert mc.get_cache() is a
    calls = []
    monkeypatch.setattr(a, "get_index", lambda s: calls.append(("idx", s)))
    monkeypatch.setattr(a, "get_ohlc_bulk", lambda syms: calls.append(("bulk", list(syms))))
    monkeypatch.setattr(a, "report", lambda: calls.append(("report",)))
    mc.warm_cache(["A", "B"], period_years=3)
    assert calls == [("idx", "^NSEI"), ("idx", "^GSPC"), ("bulk", ["A", "B"]), ("report",)]
    assert "Warming cache: 2 stocks, 3-year window" in capsys.readouterr().out
    calls.clear()
    mc.warm_cache(["A"], index_tickers=["^X"])
    assert calls[0] == ("idx", "^X") and len(calls) == 3
