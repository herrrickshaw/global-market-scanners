"""corporate_actions.py: split / rights detection, EDGAR + yfinance sources, CLI (offline)."""
import os
import sys
import types

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import corporate_actions as ca  # noqa: E402
import marketdata  # noqa: E402


def test_nearest_split_ratio():
    assert ca.nearest_split_ratio(0.5) == ("2:1", 0.5)
    assert ca.nearest_split_ratio(0.5 * 1.029) == ("2:1", 0.5)            # inside the 3% tolerance
    assert ca.nearest_split_ratio(0.5 * 1.031) is None
    assert ca.nearest_split_ratio(10.0) == ("1:10", 10.0)
    assert ca.nearest_split_ratio(0.667)[0] == "3:2"
    for bad in (float("nan"), float("inf"), 0.0, -1.0):
        assert ca.nearest_split_ratio(bad) is None
    assert ca.nearest_split_ratio(0.83) is None
    assert ca.nearest_split_ratio(0.5, tol=0.5) is not None and ca.nearest_split_ratio(0.45, tol=0.2) == ("2:1", 0.5)


def _series(n=40, split_at=30, factor=0.5, vol_mult=3.0, base=100.0, bounce=False):
    c = np.full(n, base)
    c[split_at:] = base * factor
    if bounce:
        c[split_at + 1] = base                                              # immediately reverses
    v = np.full(n, 1e6)
    v[split_at] = 1e6 * vol_mult
    return c, v


def test_detect_splits_forward():
    c, v = _series()
    dates = [f"d{i}" for i in range(len(c))]
    ev = ca.detect_splits(c, v, dates)
    assert ev == [{"i": 30, "date": "d30", "ratio": "2:1", "gap": 0.5, "vol_x": 3.0, "type": "forward"}]
    assert ca.detect_splits(c, v)[0]["date"] == 30                          # no dates -> index


def test_detect_splits_requires_volume_and_persistence():
    c, v = _series(vol_mult=1.29)
    assert ca.detect_splits(c, v) == []                                     # < 1.3x average volume
    c, v = _series(vol_mult=1.31)
    assert len(ca.detect_splits(c, v)) == 1
    c, v = _series(bounce=True)
    assert ca.detect_splits(c, v) == []                                     # reverses next day -> not a split


def test_detect_splits_reverse_needs_low_volume():
    c, v = _series(factor=2.0, vol_mult=0.5)
    ev = ca.detect_splits(c, v)
    assert len(ev) == 1 and ev[0]["type"] == "reverse" and ev[0]["ratio"] == "1:2"
    c, v = _series(factor=2.0, vol_mult=0.8)                                # 1/1.3 = 0.769 boundary: 0.8 too high
    assert ca.detect_splits(c, v) == []


def test_detect_splits_skips_glitches_and_nans():
    c, v = _series(factor=0.05)
    assert ca.detect_splits(c, v) == []                                     # < MIN_GAP deep drop ignored
    c, v = _series()
    c[29] = np.nan
    assert ca.detect_splits(c, v) == []                                     # previous close NaN
    c, v = _series(split_at=20)
    assert ca.detect_splits(c, v) == []                                     # loop starts at i=21


def test_detect_splits_last_bar_no_next_close():
    c, v = _series(n=31, split_at=30)
    assert len(ca.detect_splits(c, v)) == 1                                 # persists defaults to True at the end


def _rights_series(n=40, at=30, drop=-0.2, vol_mult=2.0, stable=True):
    c = np.full(n, 100.0)
    c[at:] = 100.0 * (1 + drop)
    if not stable:
        c[at + 3:] = c[at] * 0.7
    v = np.full(n, 1e6)
    v[at] = 1e6 * vol_mult
    return c, v


def test_detect_rights_basic_and_filters():
    c, v = _rights_series()
    m = np.zeros(40)
    ev = ca.detect_rights(c, v, m, [f"d{i}" for i in range(40)])
    assert ev == [{"i": 30, "date": "d30", "drop%": -20.0, "vs_mkt%": -20.0, "vol_x": 2.0}]
    assert ca.detect_rights(c, v, m)[0]["date"] == 30
    # market fell too (idiosyncratic margin: mkt - ret must be >= 0.08)
    m2 = np.zeros(40); m2[30] = -0.13
    assert ca.detect_rights(c, v, m2) == []
    m3 = np.zeros(40); m3[30] = -0.10
    assert len(ca.detect_rights(c, v, m3)) == 1
    assert len(ca.detect_rights(c, v, np.zeros(10))) == 1                    # short market array -> treated as 0
    assert len(ca.detect_rights(c, v, np.full(40, np.nan))) == 1


def test_detect_rights_band_volume_and_stabilisation():
    m = np.zeros(40)
    for drop, ok in [(-0.36, False), (-0.30, True), (-0.081, True), (-0.07, False)]:
        c, v = _rights_series(drop=drop)
        assert bool(ca.detect_rights(c, v, m)) is ok, drop
    c, v = _rights_series(vol_mult=1.29)
    assert ca.detect_rights(c, v, m) == []
    c, v = _rights_series(stable=False)
    assert ca.detect_rights(c, v, m) == []                                   # keeps falling after day 3
    c, v = _rights_series(drop=-0.3333)
    assert ca.detect_rights(c, v, m) == []                                   # looks like a 3:2 split, not rights


def test_label_ratio():
    assert ca.label_ratio(10.0) == "10:1" and ca.label_ratio(2.0) == "2:1" and ca.label_ratio(1.5) == "1.50:1"
    assert ca.label_ratio(0.5) == "1:2" and ca.label_ratio(0.1) == "1:10" and ca.label_ratio(0.4) == "1:2.50"
    assert ca.label_ratio(1.0) == "1:1"


def _install_pf(monkeypatch, symbols=None):
    pf = types.ModuleType("pead_factor")
    pf._liquid_symbols = lambda c, v: list(symbols if symbols is not None else c.columns)
    monkeypatch.setitem(sys.modules, "pead_factor", pf)
    import pead_factor
    return pf


def _wide(split=True):
    n = 60
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    c, v = _series(n=n, split_at=40)
    close = pd.DataFrame({"AAA": c, "BBB": np.full(n, 50.0) if split else c}, index=idx)
    vol = pd.DataFrame({"AAA": v, "BBB": np.full(n, 1e6)}, index=idx)
    return close, vol


def test_scan_splits_and_rights(monkeypatch):
    _install_pf(monkeypatch)
    close, vol = _wide()
    monkeypatch.setattr(ca, "_wide", lambda m: None if m == "NONE" else (close, vol))
    sp = ca.scan_splits("US")
    assert len(sp) == 1 and sp.iloc[0]["ticker"] == "AAA" and sp.iloc[0]["ratio"] == "2:1"
    assert sp.iloc[0]["date"] == str(close.index[40].date()) and ca.scan_splits("NONE").empty
    # rights: a -20% idiosyncratic drop with 2x volume that stabilises
    c2, v2 = _rights_series(n=60, at=40)
    close2 = pd.DataFrame({"AAA": c2, "BBB": np.full(60, 100.0)}, index=close.index)
    vol2 = pd.DataFrame({"AAA": v2, "BBB": np.full(60, 1e6)}, index=close.index)
    monkeypatch.setattr(ca, "_wide", lambda m: (close2, vol2))
    ri = ca.scan_rights("US")
    assert list(ri["ticker"]) == ["AAA"] and ri.iloc[0]["drop%"] == -20.0
    monkeypatch.setattr(ca, "_wide", lambda m: None)
    assert ca.scan_rights("US").empty


def test_confirm_split_yf(monkeypatch):
    idx = pd.to_datetime(["2020-01-01", "2021-02-03", "2022-03-04", "2023-04-05", "2024-05-06", "2025-06-07"])
    s = pd.Series([2.0, 3.0, 0.5, 4.0, 10.0, 5.0], index=idx)
    ac = types.ModuleType("apiclient")
    ac.robust = lambda src, fn, retries=5: fn()
    monkeypatch.setitem(sys.modules, "apiclient", ac)
    yf = types.ModuleType("yfinance")
    yf.Ticker = lambda t: types.SimpleNamespace(splits=s)
    monkeypatch.setitem(sys.modules, "yfinance", yf)
    out = ca.confirm_split_yf("AAPL")
    assert out == [("2021-02-03", 3.0), ("2022-03-04", 0.5), ("2023-04-05", 4.0), ("2024-05-06", 10.0), ("2025-06-07", 5.0)]
    ac.robust = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
    assert ca.confirm_split_yf("AAPL") == []


def test_parse_edgar_hits():
    payload = {"hits": {"hits": [
        {"_source": {"display_names": ["ACME CORP  (ACME, ACMEW)  (CIK 0000123)"], "file_date": "2025-01-02",
                     "root_forms": ["8-K"]}},
        {"_source": {"display_names": ["NO TICKER INC  (CIK 0000999)"], "file_date": "2025-01-03", "form_type": "10-K"}},
        {"_source": {"display_names": [], "file_date": None}},
        {}]}}
    rows = ca.parse_edgar_hits(payload, "kw")
    assert rows[0] == {"company": "ACME CORP", "ticker": "ACME", "date": "2025-01-02", "form": "8-K", "event": "kw"}
    assert rows[1]["ticker"] == "" and rows[1]["form"] == "10-K" and rows[1]["company"] == "NO TICKER INC"
    assert rows[2]["company"] == "" and rows[2]["form"] == ""
    assert len(rows) == 4 and ca.parse_edgar_hits({}) == []


def _install_http(monkeypatch, fn):
    ac = types.ModuleType("apiclient")
    ac.http_get = fn
    ac.robust = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "apiclient", ac)


def test_announcements_edgar(monkeypatch, capsys):
    seen = []
    payload = {"hits": {"hits": [{"_source": {"display_names": [f"C{i}  (T{i})  (CIK 1)"], "file_date": f"2025-01-0{i+1}",
                                              "root_forms": ["8-K"]}} for i in range(5)]}}
    _install_http(monkeypatch, lambda src, url, headers=None, retries=None: (
        seen.append((src, url, headers, retries)) or types.SimpleNamespace(status_code=200, json=lambda: payload)))
    monkeypatch.setenv("SEC_UA", "tester ua")
    df = ca.announcements_edgar("forward stock split", forms="8-K", since="2025-02-01", limit=3)
    assert len(df) == 3
    src, url, hdr, retries = seen[0]
    assert src == "edgar" and retries == 2 and hdr == {"User-Agent": "tester ua"}
    assert "q=%22forward+stock+split%22" in url and "forms=8-K" in url and "startdt=2025-02-01" in url
    _install_http(monkeypatch, lambda *a, **k: types.SimpleNamespace(status_code=500))
    assert ca.announcements_edgar("x").empty
    _install_http(monkeypatch, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    assert ca.announcements_edgar("rights offering").empty
    assert "'rights offering' unavailable: offline" in capsys.readouterr().err


def test_announcements_union_and_dedupe(monkeypatch):
    calls = []

    def fake(k, since="x", limit=1):
        calls.append((k, since, limit))
        if k == "stock split":
            return pd.DataFrame()
        return pd.DataFrame({"company": ["A", "B"], "date": ["2025-01-01", "2025-02-01"], "ticker": ["A", "B"],
                             "form": "8-K", "event": k})
    monkeypatch.setattr(ca, "announcements_edgar", fake)
    df = ca.announcements("split", since="2025-01-01", limit=7)
    assert [c[0] for c in calls] == ca.SPLIT_KEYWORDS and all(c[2] == 7 for c in calls)
    assert len(df) == 2 and list(df["date"]) == ["2025-02-01", "2025-01-01"]       # deduped + newest first
    calls.clear()
    ca.announcements("rights")
    assert [c[0] for c in calls] == ca.RIGHTS_KEYWORDS
    monkeypatch.setattr(ca, "announcements_edgar", lambda *a, **k: pd.DataFrame())
    assert ca.announcements("split").empty


def test_screen_splits_yf(monkeypatch):
    _install_pf(monkeypatch)
    n = 300
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    close = pd.DataFrame({"BIG": 100.0, "SMALL": 10.0, "TINY": 1.0}, index=idx)
    vol = pd.DataFrame({"BIG": 1e6, "SMALL": 1e5, "TINY": 1e3}, index=idx)
    monkeypatch.setattr(ca, "_wide", lambda m: (close, vol))
    asked = []
    start = str(idx.min().date())

    def confirm(t):
        asked.append(t)
        return {"BIG": [("2023-01-01", 2.0), (start, 4.0)], "SMALL": [("2024-06-01", 0.5)]}.get(t, [])
    monkeypatch.setattr(ca, "confirm_split_yf", confirm)
    df = ca.screen_splits_yf("US", limit=2)
    assert asked == ["BIG", "SMALL"]                                          # most liquid first, limited
    assert sorted(df["ticker"]) == ["BIG", "SMALL"] and "2023-01-01" not in set(df["date"])    # pre-window dropped
    r = df.set_index("ticker")
    assert r.loc["BIG", "ratio"] == "4:1" and r.loc["BIG", "type"] == "forward" and r.loc["SMALL", "type"] == "reverse"
    assert len(ca.screen_splits_yf("US", limit=2, min_date="2024-07-01")) == 0
    monkeypatch.setattr(ca, "_wide", lambda m: None)
    assert ca.screen_splits_yf("US").empty


def _main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["ca", *argv])
    ca.main()


def test_main_news_mode(monkeypatch, capsys):
    df = pd.DataFrame({"company": ["ACME"], "ticker": ["A"], "date": ["2025-01-02"], "form": ["8-K"], "event": ["stock split"]})
    got = []
    monkeypatch.setattr(ca, "announcements", lambda kind, since, limit: (got.append((kind, since, limit)), df if kind == "split" else pd.DataFrame())[1])
    _main(monkeypatch, "--news", "--action", "both", "--since", "2024-12-01", "--top", "5")
    out = capsys.readouterr().out
    assert got == [("split", "2024-12-01", 5), ("rights", "2024-12-01", 5)]
    assert "STOCK SPLIT — SEC EDGAR public announcements since 2024-12-01 (1 filings)" in out
    assert "ACME" in out and "RIGHTS ISSUE" in out and "none found" in out
    got.clear()
    _main(monkeypatch, "--news", "--action", "rights")
    assert [g[0] for g in got] == ["rights"]


def test_main_heuristic_and_yf_and_rights(monkeypatch, capsys):
    monkeypatch.setattr(marketdata, "market_list", lambda: ["US"])
    sp = pd.DataFrame({"market": ["US"], "ticker": ["AAA"], "date": ["2024-01-01"], "ratio": ["2:1"], "gap": [0.5],
                       "vol_x": [3.0], "type": ["forward"]})
    ri = pd.DataFrame({"market": ["US", "US"], "ticker": ["AAA", "BBB"], "date": ["d1", "d2"], "drop%": [-10.0, -20.0],
                       "vs_mkt%": [-10.0, -20.0], "vol_x": [2.0, 3.0]})
    monkeypatch.setattr(ca, "scan_splits", lambda m: sp)
    monkeypatch.setattr(ca, "scan_rights", lambda m: ri)
    monkeypatch.setattr(ca, "screen_splits_yf", lambda m, limit: sp.assign(ratio="1:10", type="reverse"))
    _main(monkeypatch, "--heuristic", "--action", "both")
    out = capsys.readouterr().out
    assert "offline heuristic) — 1 candidate events" in out and "0.500" in out and "forward" in out
    assert "RIGHTS-ISSUE / DILUTION-EVENT SCREENER — 2 candidate ex-dates" in out
    lines = [l for l in out.splitlines() if l.startswith("  US")]
    assert lines[-2].split()[1] == "BBB"                                      # sorted by drop% ascending (deepest first)
    _main(monkeypatch, "--market", "US", "--limit", "9")
    out = capsys.readouterr().out
    assert "yfinance, authoritative) — 1 splits in window" in out and "1:10" in out and "reverse" in out
    monkeypatch.setattr(ca, "screen_splits_yf", lambda m, limit: pd.DataFrame())
    _main(monkeypatch)
    assert "no splits recorded" in capsys.readouterr().out
    monkeypatch.setattr(marketdata, "market_list", lambda: [])
    _main(monkeypatch, "--heuristic", "--action", "both")
    out = capsys.readouterr().out
    assert "0 candidate events" in out and "0 candidate ex-dates" in out


def test_detect_splits_volume_exactly_at_multiplier():
    c, v = _series(vol_mult=1.3)
    assert len(ca.detect_splits(c, v)) == 1



def test_detect_rights_market_margin_exactly_at_threshold():
    c, v = _rights_series(drop=-0.125)           # exact binary fractions: (-0.045) - (-0.125) == 0.08 exactly
    m = np.zeros(40); m[30] = -0.045
    assert (m[30] - (c[30] / c[29] - 1)) == ca.RIGHTS_MKT_MARGIN
    assert len(ca.detect_rights(c, v, m)) == 1   # margin must be strictly LESS than the threshold to be rejected
