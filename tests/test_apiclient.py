"""apiclient.py: governed fetch helpers — retry/backoff, yfinance chunking, http 429 (all faked)."""
import os
import sys
import types

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apiclient as ac  # noqa: E402


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    sleeps = []
    monkeypatch.setattr(ac.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(ac.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(ac, "_sources", {})
    monkeypatch.setitem(ac.LIMITS, "x", (0.0, 3))        # no throttle waits: sleeps list then holds backoff only
    monkeypatch.setitem(ac.LIMITS, "edgar", (0.0, 5))
    ac._test_sleeps = sleeps
    return sleeps


def test_limits_and_source_registry():
    assert ac.LIMITS["yfinance"] == (0.40, 3) and ac.LIMITS["wikidata"] == (60.0, 1)
    s = ac._src("yfinance")
    assert s.interval == 0.40 and ac._src("yfinance") is s
    d = ac._src("unknown-source")
    assert d.interval == 0.50 and d.penalty == 1.0
    assert ac.LIMITS["gleif"] == (0.25, 4)


def test_rate_error_hints():
    for msg in ("rate limited", "HTTP 429", "Too Many Requests", "Invalid Crumb", "401 Unauthorized",
                "throttled", "Service Unavailable", "timed out", "Connection reset by peer"):
        assert ac._is_rate_error(Exception(msg)), msg
    assert not ac._is_rate_error(Exception("KeyError: 'x'"))


def test_acquire_sleeps_when_next_slot_in_future(monkeypatch):
    s = ac._Source(1.0, 2)
    monkeypatch.setattr(ac.time, "monotonic", lambda: 100.0)
    s.acquire()                     # first: no wait
    assert ac._test_sleeps == []
    s.acquire()                     # second at the same instant: must wait one full interval
    assert ac._test_sleeps == [1.0]
    s.penalty = 4.0
    s.sem.release(); s.sem.release()
    s.acquire()                     # next_ok advanced by interval*penalty? (set before penalty) -> waits 2.0 + ...
    assert ac._test_sleeps[-1] == pytest.approx(2.0)


def test_penalty_caps_at_32_and_relax_floor():
    s = ac._Source(0.1, 1)
    for _ in range(10):
        s.penalize()
    assert s.penalty == 32.0
    for _ in range(200):
        s.relax()
    assert s.penalty == 1.0


def test_throttle_context_acquires_and_releases():
    with ac.throttle("yfinance") as t:
        assert t.s.sem._value == 2
    assert t.s.sem._value == 3


def test_robust_success_relaxes_penalty():
    s = ac._src("x")
    s.penalty = 8.0
    assert ac.robust("x", lambda a, b=0: a + b, 1, b=2) == 3
    assert s.penalty == pytest.approx(8.0 * 0.92)


def test_robust_retries_rate_errors_with_backoff_and_penalty():
    calls = []

    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("HTTP 429")
        return "ok"
    assert ac.robust("x", fn) == "ok"
    assert len(calls) == 3
    assert ac._test_sleeps == [1.5, 3.0]                       # (2**attempt)*1.5, jitter patched to 0
    assert ac._src("x").penalty == pytest.approx(4.0 * 0.92)   # 2 penalties then one relax


def test_robust_non_rate_error_gives_up_after_three_attempts():
    calls = []

    def fn():
        calls.append(1)
        raise ValueError("bad")
    with pytest.raises(ValueError):
        ac.robust("x", fn)
    assert len(calls) == 3 and ac._test_sleeps == [1.5, 3.0]


def test_robust_exhausts_retries_and_raises_last():
    def fn():
        raise RuntimeError("rate limit hit")
    with pytest.raises(RuntimeError, match="rate limit"):
        ac.robust("x", fn, retries=3)
    assert len(ac._test_sleeps) == 3


def test_robust_zero_retries():
    with pytest.raises(RuntimeError, match="x: retries exhausted"):
        ac.robust("x", lambda: 1, retries=0)


def test_backoff_sleep_is_capped_at_90():
    n = []

    def fn():
        n.append(1)
        raise RuntimeError("429")
    with pytest.raises(RuntimeError):
        ac.robust("x", fn, retries=9)
    assert max(ac._test_sleeps) == 90.0


def _fake_yf(monkeypatch, download):
    yf = types.ModuleType("yfinance")
    yf.download = download
    monkeypatch.setitem(sys.modules, "yfinance", yf)
    return yf


def _multi(tickers, rows=3):
    idx = pd.date_range("2024-01-01", periods=rows)
    cols = pd.MultiIndex.from_product([tickers, ["Open", "Close"]])
    return pd.DataFrame(1.0, index=idx, columns=cols)


def test_yf_download_dedupes_chunks_and_collects(monkeypatch):
    calls = []

    def dl(batch, **kw):
        calls.append((list(batch), kw))
        return _multi(batch)
    _fake_yf(monkeypatch, dl)
    tickers = [f"T{i}" for i in range(120)] + ["T0", None, ""]
    out = ac.yf_download(tickers, period="5y")
    assert len(out) == 120 and [len(c[0]) for c in calls] == [50, 50, 20]
    kw = calls[0][1]
    assert kw["period"] == "5y" and kw["auto_adjust"] is True and kw["group_by"] == "ticker" and kw["threads"] is True


def test_yf_download_single_string_and_start_kw(monkeypatch):
    seen = {}

    def dl(batch, **kw):
        seen.update(kw, batch=list(batch))
        return _multi(batch)
    _fake_yf(monkeypatch, dl)
    out = ac.yf_download("AAPL", start="2024-01-01", interval="1d")
    assert list(out) == ["AAPL"] and seen["start"] == "2024-01-01" and seen["interval"] == "1d"


def test_yf_download_flat_columns_and_empty_and_missing(monkeypatch):
    idx = pd.date_range("2024-01-01", periods=2)
    flat = pd.DataFrame({"Close": [1.0, 2.0]}, index=idx)
    _fake_yf(monkeypatch, lambda b, **k: flat)
    assert list(ac.yf_download(["A"])) == ["A"]
    empty = pd.DataFrame({"Close": [float("nan")] * 2}, index=idx)
    _fake_yf(monkeypatch, lambda b, **k: empty)
    assert ac.yf_download(["A"]) == {}                               # all-NaN rows dropped -> empty -> skipped
    _fake_yf(monkeypatch, lambda b, **k: _multi(["OTHER"]))
    assert ac.yf_download(["A"]) == {}                               # ticker missing from frame -> KeyError swallowed


def test_yf_download_failed_chunk_skipped_but_others_kept(monkeypatch):
    n = []

    def dl(batch, **kw):
        n.append(1)
        if len(n) <= 3:                       # first chunk: fails (non-rate error: 3 tries) then raises
            raise ValueError("boom")
        return _multi(batch)
    _fake_yf(monkeypatch, dl)
    out = ac.yf_download([f"T{i}" for i in range(60)])
    assert set(out) == {f"T{i}" for i in range(50, 60)}


def test_yf_info(monkeypatch):
    yf = types.ModuleType("yfinance")
    yf.Ticker = lambda t: types.SimpleNamespace(get_info=lambda: {"sym": t})
    monkeypatch.setitem(sys.modules, "yfinance", yf)
    assert ac.yf_info("AAPL") == {"sym": "AAPL"}


def test_http_get_ok_and_429_retry(monkeypatch):
    rq = types.ModuleType("requests")
    seq = [types.SimpleNamespace(status_code=429), types.SimpleNamespace(status_code=200, text="ok")]
    seen = []
    rq.get = lambda url, headers=None, timeout=None: (seen.append((url, headers, timeout)) or seq.pop(0))
    monkeypatch.setitem(sys.modules, "requests", rq)
    r = ac.http_get("edgar", "http://x", headers={"UA": "t"}, timeout=7)
    assert r.text == "ok" and len(seen) == 2 and seen[0] == ("http://x", {"UA": "t"}, 7)
    assert ac._test_sleeps == [1.5]


def test_http_get_retries_param_fails_fast(monkeypatch):
    rq = types.ModuleType("requests")
    rq.get = lambda *a, **k: types.SimpleNamespace(status_code=429)
    monkeypatch.setitem(sys.modules, "requests", rq)
    with pytest.raises(RuntimeError, match="429"):
        ac.http_get("edgar", "http://x", retries=2)
    assert len(ac._test_sleeps) == 2
