"""stream_pipeline.py: Kafka work-queue scan signals (broker, Cassandra and Excel all faked)."""
import json
import os
import sys
import types

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stream_pipeline as sp  # noqa: E402
from conftest_helpers import make_ohlcv  # noqa: E402


def _install_store(monkeypatch, get, cached=None):
    m = types.ModuleType("market_store")
    m.get_ohlc = get
    m.cached_download = cached or (lambda t: {})
    monkeypatch.setitem(sys.modules, "market_store", m)


def _px(vals):
    idx = pd.date_range("2024-01-01", periods=len(vals), freq="B")
    return pd.DataFrame({"Close": vals}, index=idx)


def test_signal_no_data_paths(monkeypatch):
    _install_store(monkeypatch, lambda t: pd.DataFrame())
    assert sp._signal("X") == {"ticker": "X", "signal": "NO_DATA"}
    _install_store(monkeypatch, lambda t: _px(list(range(1, 60))))               # 59 bars < 60
    assert sp._signal("X")["signal"] == "NO_DATA"
    _install_store(monkeypatch, lambda t: pd.DataFrame(), cached=lambda t: {t[0]: None})
    assert sp._signal("X")["signal"] == "NO_DATA"


def test_signal_falls_back_to_cached_download(monkeypatch):
    seen = []
    _install_store(monkeypatch, lambda t: pd.DataFrame(),
                   cached=lambda t: seen.append(t) or {t[0]: _px(list(np.linspace(1, 100, 80)))})
    r = sp._signal("Z")
    assert seen == [["Z"]] and r["signal"] == "BREAKOUT"


def test_signal_breakout_oversold_neutral(monkeypatch):
    up = list(np.linspace(10, 100, 70))                          # last == 60d high
    up[65] = up[64] - 0.5                                        # one dip so RSI is defined (<100)
    _install_store(monkeypatch, lambda t: _px(up))
    r = sp._signal("U")
    assert r["signal"] == "BREAKOUT" and r["close"] == 100.0 and 70 < r["rsi"] < 100
    down = list(np.linspace(100, 10, 70))
    _install_store(monkeypatch, lambda t: _px(down))
    r = sp._signal("D")
    assert r["signal"] == "OVERSOLD" and r["rsi"] == 0.0
    mid = [100.0] * 30 + [90.0, 110.0] * 20                       # last (110) below nothing; make last < hi
    mid[-1] = 95.0
    _install_store(monkeypatch, lambda t: _px(mid))
    r = sp._signal("M")
    assert r["signal"] == "NEUTRAL" and 30 <= r["rsi"] <= 100


def test_signal_exactly_60_bars_ok(monkeypatch):
    _install_store(monkeypatch, lambda t: _px(list(np.linspace(1, 60, 60))))
    assert sp._signal("E")["signal"] == "BREAKOUT"


def test_universe(monkeypatch):
    monkeypatch.setattr(sp.glob, "glob", lambda p, recursive=False: [])
    assert sp._universe("US", None) == []
    monkeypatch.setattr(sp.glob, "glob", lambda p, recursive=False: ["/b.xlsx", "/a.xlsx"])
    seen = {}

    class XL:
        def __init__(self, p):
            seen["p"] = p

        def parse(self, s):
            return pd.DataFrame({"Symbol": ["A", "nan", "B", ""], "Suffix": [".NS", ".NS", ".BO", ".NS"]})
    monkeypatch.setattr(sp.pd, "ExcelFile", XL)
    assert sp._universe("US", None) == ["A", "B"] and seen["p"] == "/b.xlsx"
    assert sp._universe("US", 1) == ["A"]


def _india_universe(monkeypatch, frame):
    monkeypatch.setattr(sp.glob, "glob", lambda p, recursive=False: ["/a.xlsx"])
    monkeypatch.setattr(sp.pd, "ExcelFile", lambda p: types.SimpleNamespace(parse=lambda s: frame))
    return sp._universe("India", None)


def test_universe_india_appends_suffix(monkeypatch):
    assert _india_universe(monkeypatch, pd.DataFrame({"Symbol": ["A", "B"], "Suffix": [".NS", ".BO"]})) == ["A.NS", "B.BO"]


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="stream_pipeline.py _universe: India symbols are concatenated with the suffix BEFORE the "
                          "'nan'/empty filter, so a missing Symbol becomes the ticker 'nan.NS' / '.NS'")
def test_universe_india_missing_symbol_is_dropped(monkeypatch):
    got = _india_universe(monkeypatch, pd.DataFrame({"Symbol": ["A", None, ""], "Suffix": [".NS", ".NS", ".NS"]}))
    assert got == ["A.NS"]


class FakeMsg:
    def __init__(self, value=b"", err=None):
        self._v, self._e = value, err

    def error(self):
        return self._e

    def value(self):
        return self._v


class Kafka:
    produced = []
    flushes = 0
    to_poll = []
    closed = False
    subscribed = None
    consumer_conf = None


@pytest.fixture
def kafka(monkeypatch):
    K = Kafka
    K.produced, K.flushes, K.to_poll, K.closed, K.subscribed = [], 0, [], False, None

    class Producer:
        def __init__(self, conf):
            self.conf = conf

        def produce(self, topic, key=None, value=None):
            K.produced.append((topic, key, value))

        def flush(self):
            K.flushes += 1

    class Consumer:
        def __init__(self, conf):
            K.consumer_conf = conf

        def subscribe(self, topics):
            K.subscribed = topics

        def poll(self, t):
            return K.to_poll.pop(0) if K.to_poll else None

        def close(self):
            K.closed = True

    mod = types.ModuleType("confluent_kafka")
    mod.Producer, mod.Consumer = Producer, Consumer
    monkeypatch.setitem(sys.modules, "confluent_kafka", mod)
    return K


def test_produce_publishes_every_ticker(kafka, monkeypatch, capsys):
    monkeypatch.setattr(sp, "_universe", lambda m, l: ["A", "B", "C"])
    sp.produce("US", 3)
    assert kafka.produced == [("scan.tickers", "A", "A"), ("scan.tickers", "B", "B"), ("scan.tickers", "C", "C")]
    assert kafka.flushes == 1
    assert "produced 3 US tickers -> scan.tickers" in capsys.readouterr().err


def test_consume_processes_until_idle(kafka, monkeypatch, capsys):
    kafka.to_poll = [FakeMsg(b"AAA"), FakeMsg(err="boom"), None, FakeMsg(b"BBB")]
    monkeypatch.setattr(sp, "_signal", lambda t: {"ticker": t, "signal": "NEUTRAL"})
    sp.consume("g1", max_idle=2)
    assert [(p[0], p[1], json.loads(p[2])["ticker"]) for p in kafka.produced] == [
        ("scan.signals", "AAA", "AAA"), ("scan.signals", "BBB", "BBB")]
    assert kafka.consumer_conf == {"bootstrap.servers": "localhost:9092", "group.id": "g1",
                                   "auto.offset.reset": "earliest"}
    assert kafka.subscribed == ["scan.tickers"] and kafka.closed
    assert kafka.flushes == 1                                      # only final flush (n=2 < 25)
    assert "done, 2 signals -> scan.signals" in capsys.readouterr().err


def test_consume_idle_counter_resets_and_stops(kafka, monkeypatch):
    # idle must reset after a message: 2 Nones, msg, 2 Nones -> stops after the second pair (max_idle=2)
    kafka.to_poll = [None, None, FakeMsg(b"A"), None, None, FakeMsg(b"NEVER")]
    monkeypatch.setattr(sp, "_signal", lambda t: {"ticker": t})
    sp.consume("g", max_idle=3)
    assert [p[1] for p in kafka.produced] == ["A", "NEVER"]


def test_consume_flushes_every_25(kafka, monkeypatch, capsys):
    kafka.to_poll = [FakeMsg(f"T{i}".encode()) for i in range(26)]
    monkeypatch.setattr(sp, "_signal", lambda t: {"ticker": t})
    sp.consume("g", max_idle=1)
    assert len(kafka.produced) == 26 and kafka.flushes == 2         # at n=25 and in finally
    assert "processed 25" in capsys.readouterr().err


def test_consume_closes_on_error(kafka, monkeypatch):
    kafka.to_poll = [FakeMsg(b"A")]
    monkeypatch.setattr(sp, "_signal", lambda t: (_ for _ in ()).throw(RuntimeError("bad")))
    with pytest.raises(RuntimeError):
        sp.consume("g")
    assert kafka.closed and kafka.flushes == 1


def test_demo_roundtrip(kafka, monkeypatch, capsys):
    kafka.to_poll = [FakeMsg(b"AAPL"), None, FakeMsg(err="e"), FakeMsg(b"MSFT")]
    monkeypatch.setattr(sp, "_signal", lambda t: {"ticker": t, "signal": "X"})
    sp.demo()
    assert [p[1] for p in kafka.produced] == ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"]
    out = capsys.readouterr()
    assert out.out.count("signal:") == 2 and kafka.closed
    assert "produced 5 -> scan.tickers" in out.err


def test_main_dispatch(monkeypatch):
    calls = []
    monkeypatch.setattr(sp, "produce", lambda m, l: calls.append(("p", m, l)))
    monkeypatch.setattr(sp, "consume", lambda g: calls.append(("c", g)))
    monkeypatch.setattr(sp, "demo", lambda: calls.append(("d",)))
    for argv in (["produce", "--market", "India", "--limit", "5"], ["produce"], ["consume", "--group", "z"],
                 ["consume"], ["demo"]):
        monkeypatch.setattr(sys, "argv", ["sp", *argv])
        sp.main()
    assert calls == [("p", "India", 5), ("p", "US", None), ("c", "z"), ("c", "scan1"), ("d",)]
