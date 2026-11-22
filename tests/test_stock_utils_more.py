"""stock_utils.py beyond the pure numeric helpers: OHLCV hygiene, ticker extraction, bulk download, thread map."""
import os
import sys
import types

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stock_utils as su  # noqa: E402


def _ohlcv(n=10):
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    c = np.linspace(10, 20, n)
    return pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": 1000.0}, index=idx)


def test_first_df_picks_first_non_empty_dataframe():
    good = pd.DataFrame({"a": [1]})
    t = types.SimpleNamespace(empty=pd.DataFrame(), notdf="x", good=good, none=None)
    assert su.first_df(t, "missing", "empty", "notdf", "none", "good") is good
    assert su.first_df(t, "empty", "notdf") is None and su.first_df(t) is None


def test_row_edge_cases():
    df = pd.DataFrame({"2024": [1.5], "2023": [np.nan]}, index=["x"])
    assert su.row(df, "x") == 1.5 and su.row(df, "x", col=1) is None
    assert su.row(df, "x", col=5) is None                     # out-of-range column swallowed
    assert su.row(df, "nope", "x") == 1.5 and su.row(pd.DataFrame(), "x") is None
    assert su.fin_metric(df, ("nope",)) is None and su.fin_metric(df, ("x",), default=9) == 1.5
    assert su.series(df, "nope", "x") == [1.5] and su.series(df, "nope") == []


def test_clean_ohlcv_noop_on_clean_and_idempotent():
    df = _ohlcv()
    out = su.clean_ohlcv(df)
    assert out.equals(df) and su.clean_ohlcv(out).equals(out)
    assert su.clean_ohlcv(None) is None and su.clean_ohlcv(pd.DataFrame()) is None
    assert su.clean_ohlcv(pd.DataFrame({"Volume": [1, 2]})) is None            # no Close column


def test_clean_ohlcv_repairs():
    df = _ohlcv(6)
    df.iloc[1, df.columns.get_loc("Open")] = np.nan                           # filled from Close
    df.iloc[2, df.columns.get_loc("Volume")] = np.nan                         # -> 0
    df.iloc[3, df.columns.get_loc("Volume")] = -5.0                           # clipped to 0
    df.iloc[4, df.columns.get_loc("Close")] = np.nan                          # row dropped
    df.iloc[5, df.columns.get_loc("High")] = df.iloc[5]["Close"] - 3          # integrity: High >= Close
    df["Extra"] = "junk"
    out = su.clean_ohlcv(df)
    assert list(out.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert len(out) == 5 and out.iloc[1]["Open"] == out.iloc[1]["Close"]
    assert out.iloc[2]["Volume"] == 0 and out.iloc[3]["Volume"] == 0
    assert (out["High"] >= out[["Open", "Close", "Low"]].max(axis=1)).all()
    assert (out["Low"] <= out[["Open", "Close", "High"]].min(axis=1)).all()


def test_clean_ohlcv_duplicates_nonnumeric_and_nonpositive():
    df = _ohlcv(5)
    dup = df.iloc[[2]].copy(); dup["Close"] = 99.0
    df = pd.concat([df, dup]).sort_index(kind="stable")
    df.iloc[0, df.columns.get_loc("Close")] = 0.0                              # non-positive close dropped
    out = su.clean_ohlcv(df.astype({"Close": object}).replace({99.0: "99.0"}))
    assert out.loc[df.index[2], "Close"] in (99.0,) or len(out) == 4
    assert (out["Close"] > 0).all() and out.index.is_unique
    bad = _ohlcv(3); bad["Close"] = "x"
    assert su.clean_ohlcv(bad) is None


def test_clean_ohlcv_bad_prints_and_min_bars_and_verbose(capsys):
    df = _ohlcv(8)
    df["Close"] = 10.0; df["Open"] = 10.0; df["High"] = 10.0; df["Low"] = 10.0
    df.iloc[4, df.columns.get_loc("Close")] = 100.0                            # +900% on zero volume
    df.iloc[4, df.columns.get_loc("Volume")] = 0.0
    df.iloc[5, df.columns.get_loc("Close")] = 10.0
    out = su.clean_ohlcv(df, ticker="XYZ", verbose=True)
    assert (out["Close"] == 10.0).all()                                        # bad print neutralised (ffilled)
    assert "clean_ohlcv[XYZ]" in capsys.readouterr().out
    df2 = df.copy(); df2.iloc[4, df2.columns.get_loc("Volume")] = 5000.0       # real volume -> kept
    assert su.clean_ohlcv(df2)["Close"].iloc[4] == 100.0
    assert su.clean_ohlcv(_ohlcv(5), min_bars=6) is None and su.clean_ohlcv(_ohlcv(5), min_bars=5) is not None
    df3 = df.copy()
    assert su.clean_ohlcv(df3, max_daily_move=100.0)["Close"].iloc[4] == 100.0  # threshold respected


def test_clean_financials():
    cols = pd.to_datetime(["2022-12-31", "2024-12-31", "2023-12-31"])
    df = pd.DataFrame([[1, 2, 3], [None, None, None]], columns=cols, index=["a", "b"]).astype(object)
    df.iloc[0, 0] = "1"
    out = su.clean_financials(df)
    assert list(out.columns) == sorted(cols, reverse=True) and list(out.loc["a"]) == [2, 3, 1]
    allnan = pd.DataFrame({"x": [np.nan]})
    assert su.clean_financials(allnan) is None and su.clean_financials(None) is None and su.clean_financials(pd.DataFrame()) is None
    mixed = pd.DataFrame([[1, 2]], columns=["b", 1])                           # unorderable labels -> left as is
    assert list(su.clean_financials(mixed).columns) == ["b", 1]


def test_extract_ticker_df():
    a, b = _ohlcv(6), _ohlcv(6) * 2
    multi = pd.concat({"Open": pd.concat({"A": a["Open"], "B": b["Open"]}, axis=1),
                       "High": pd.concat({"A": a["High"], "B": b["High"]}, axis=1),
                       "Low": pd.concat({"A": a["Low"], "B": b["Low"]}, axis=1),
                       "Close": pd.concat({"A": a["Close"], "B": b["Close"]}, axis=1),
                       "Volume": pd.concat({"A": a["Volume"], "B": b["Volume"]}, axis=1)}, axis=1)
    out = su.extract_ticker_df(multi, "B")
    assert out["Close"].iloc[0] == b["Close"].iloc[0] and list(out.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert su.extract_ticker_df(multi, "ZZZ") is None                          # KeyError swallowed
    assert su.extract_ticker_df(a, "A").equals(a)                              # flat layout
    assert su.extract_ticker_df(a.iloc[:0], "A") is None
    assert su.extract_ticker_df(a, "A", min_bars=7) is None
    raw = su.extract_ticker_df(a.assign(Junk=1), "A", clean=False)
    assert list(raw.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert su.extract_ticker_df(a, "A", clean=False, min_bars=7) is None
    all_nan = a.copy(); all_nan[:] = np.nan
    assert su.extract_ticker_df(all_nan, "A") is None


def test_is_rate_limited():
    for msg in ("YFRateLimitError", "HTTP 429", "Too Many Requests", "Invalid Crumb"):
        assert su.is_rate_limited(Exception(msg))
    assert not su.is_rate_limited(Exception("boom")) and not su.is_rate_limited(Exception("rate"))   # case-sensitive


def test_strip_suffix_and_misc():
    assert su._strip_suffix("RELIANCE.NS") == "RELIANCE" and su._strip_suffix("X.BO") == "X" and su._strip_suffix("AAPL") == "AAPL"
    assert su.market_cap_crores(1e9) == 100.0 and su.market_cap_crores(None) == 0.0
    assert su.pct_change(None, 1) is None and su.cagr(None, 1, 1) is None and su.cagr(5, 1, 0) is None
    assert su.cagr(0, 1, 1) is None                                           # latest of 0 treated as falsy
    assert su.normalise_debt_to_equity(10) == 10 and su.normalise_debt_to_equity(10.01) == pytest.approx(0.1001)


def _install_yf(monkeypatch, download):
    yf = types.SimpleNamespace(download=download)
    monkeypatch.setattr(su, "yf", yf, raising=False)
    monkeypatch.setattr(su, "_YF_OK", True)
    monkeypatch.setattr(su.time, "sleep", lambda s: sleeps.append(s))


sleeps = []


def _bulk(tickers, rows=40):
    d = _ohlcv(rows)
    return pd.concat({f: pd.concat({t: d[f] for t in tickers}, axis=1) for f in ["Open", "High", "Low", "Close", "Volume"]}, axis=1)


def test_bulk_download_batches_strips_suffix_and_sleeps(monkeypatch, capsys):
    sleeps.clear()
    calls = []

    def dl(batch, **kw):
        calls.append((list(batch), kw))
        return _bulk(batch)
    _install_yf(monkeypatch, dl)
    out = su.bulk_download(["A.NS", "B.BO", "C", "D"], period="6mo", batch_size=3, sleep_between=0.7)
    assert set(out) == {"A", "B", "C", "D"} and [len(c[0]) for c in calls] == [3, 1]
    assert calls[0][1] == {"auto_adjust": True, "threads": True, "progress": False, "period": "6mo"}
    assert sleeps == [0.7]                                                     # only between batches
    assert "bulk_download complete: 4/4 loaded" in capsys.readouterr().out
    su.bulk_download(["A"], start="2024-01-01")
    assert calls[-1][1]["start"] == "2024-01-01" and "period" not in calls[-1][1]


def test_bulk_download_min_bars_empty_and_quiet(monkeypatch, capsys):
    sleeps.clear()
    _install_yf(monkeypatch, lambda b, **k: _bulk(b, rows=10))
    assert su.bulk_download(["A"], min_bars=30, verbose=False) == {} and capsys.readouterr().out == ""
    _install_yf(monkeypatch, lambda b, **k: pd.DataFrame())
    assert su.bulk_download(["A", "B"], batch_size=1) == {}
    assert "empty" in capsys.readouterr().out and sleeps == [1.5]


def test_bulk_download_retries_rate_limit_then_gives_up(monkeypatch, capsys):
    sleeps.clear()
    n = []

    def dl(b, **k):
        n.append(1)
        if len(n) < 3:
            raise RuntimeError("HTTP 429")
        return _bulk(b)
    _install_yf(monkeypatch, dl)
    assert set(su.bulk_download(["A"])) == {"A"} and sleeps == [20, 40]
    sleeps.clear(); n.clear()
    _install_yf(monkeypatch, lambda b, **k: (_ for _ in ()).throw(RuntimeError("HTTP 429")))
    assert su.bulk_download(["A"], max_retries=2) == {} and sleeps == [20]
    _install_yf(monkeypatch, lambda b, **k: (_ for _ in ()).throw(ValueError("x" * 100)))
    assert su.bulk_download(["A"]) == {} and "ERROR: " + "x" * 50 in capsys.readouterr().out


def test_bulk_download_without_yfinance(monkeypatch):
    monkeypatch.setattr(su, "_YF_OK", False)
    assert su.bulk_download(["A"]) == {}


def test_parallel_map(capsys):
    out = su.parallel_map(lambda x: None if x == 2 else (_ for _ in ()).throw(ValueError("x")) if x == 3 else x * 10,
                          [1, 2, 3, 4], workers=2, progress_every=2, label="things")
    assert sorted(out) == [10, 40]
    text = capsys.readouterr().out
    assert "2/4 things processed" in text and "4/4 things processed, 2 results" in text
    assert su.parallel_map(lambda x: x, [1, 2, 3], verbose=False) is not None
    assert capsys.readouterr().out == "" and sorted(su.parallel_map(lambda x: x, [], verbose=False)) == []
