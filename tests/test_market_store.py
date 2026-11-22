"""market_store.py: Cassandra-backed OHLC cache with mandatory Kafka CDC (fully faked)."""
import importlib
import os
import sys
import types
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeProducer:
    instances = []
    flush_remaining = 0
    raise_on_produce = None
    deliver_error = None

    def __init__(self, conf):
        self.conf = conf
        self.sent = []
        FakeProducer.instances.append(self)

    def produce(self, topic, key=None, value=None, callback=None):
        if FakeProducer.raise_on_produce:
            raise FakeProducer.raise_on_produce
        self.sent.append((topic, key, value))
        self.cb = callback

    def flush(self, timeout=None):
        self.timeout = timeout
        if FakeProducer.deliver_error is not None and self.cb:
            self.cb(FakeProducer.deliver_error, None)
        elif self.cb:
            self.cb(None, None)
        return FakeProducer.flush_remaining


class FakeSession:
    def __init__(self):
        self.executed = []
        self.keyspace = None
        self.bars = {}          # ticker -> {date: (o,h,l,c,v)}
        self.default_consistency_level = None

    def execute(self, q, params=None):
        text = q if isinstance(q, str) else q.cql
        self.executed.append(text)
        if text.startswith("SELECT MIN"):
            d = self.bars.get(params[0], {})
            if not d:
                return types.SimpleNamespace(one=lambda: types.SimpleNamespace(n=0, mn=None, mx=None))
            ks = sorted(d)
            return types.SimpleNamespace(one=lambda: types.SimpleNamespace(
                mn=ks[0], mx=ks[-1], n=len(ks)))
        if text.startswith("SELECT d,o"):
            d = self.bars.get(params[0], {})
            return [types.SimpleNamespace(d=k, o=v[0], h=v[1], l=v[2], c=v[3], v=v[4])
                    for k, v in d.items()]
        return None

    def prepare(self, cql):
        return types.SimpleNamespace(cql=cql)

    def set_keyspace(self, ks):
        self.keyspace = ks


@pytest.fixture
def ms(monkeypatch):
    """Import market_store afresh against fake cassandra / confluent_kafka modules."""
    FakeProducer.instances = []
    FakeProducer.flush_remaining = 0
    FakeProducer.raise_on_produce = None
    FakeProducer.deliver_error = None
    sess = FakeSession()
    state = {"fail": False}

    class FakeCluster:
        def __init__(self, hosts, port=None, connect_timeout=None):
            state["args"] = (hosts, port, connect_timeout)

        def connect(self):
            if state["fail"]:
                raise OSError("no nodes")
            return sess

    def exec_conc(s, stmt, args, concurrency=None):
        state["conc"] = concurrency
        for a in args:
            s.bars.setdefault(a[0], {})[a[1]] = a[2:]

    cass = types.ModuleType("cassandra")
    cass.ConsistencyLevel = types.SimpleNamespace(LOCAL_ONE="LOCAL_ONE")
    cl = types.ModuleType("cassandra.cluster"); cl.Cluster = FakeCluster
    cc = types.ModuleType("cassandra.concurrent"); cc.execute_concurrent_with_args = exec_conc
    ck = types.ModuleType("confluent_kafka"); ck.Producer = FakeProducer
    for name, mod in (("cassandra", cass), ("cassandra.cluster", cl), ("cassandra.concurrent", cc),
                      ("confluent_kafka", ck)):
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.delitem(sys.modules, "market_store", raising=False)
    mod = importlib.import_module("market_store")
    mod._test = types.SimpleNamespace(session=sess, state=state)
    yield mod
    sys.modules.pop("market_store", None)


def _df(days=3, end=None, with_cols=True):
    end = end or date.today()
    idx = pd.to_datetime([end - timedelta(days=i) for i in range(days)][::-1])
    df = pd.DataFrame({"Open": 1.0, "High": 3.0, "Low": 0.5, "Close": 2.0, "Volume": 10.0}, index=idx)
    return df if with_cols else df[["Close"]]


def test_connect_creates_keyspace_and_table_once(ms):
    s = ms._connect()
    assert s is ms._test.session and s.keyspace == "market"
    assert s.default_consistency_level == "LOCAL_ONE"
    assert ms._test.state["args"] == (["127.0.0.1"], 9042, 4)
    assert any("CREATE KEYSPACE IF NOT EXISTS market" in q for q in s.executed)
    assert any("CREATE TABLE IF NOT EXISTS ohlc_bars" in q for q in s.executed)
    n = len(s.executed)
    assert ms._connect() is s and len(s.executed) == n    # cached


def test_connect_failure_raises_unavailable(ms):
    ms._test.state["fail"] = True
    with pytest.raises(ms.MarketStoreUnavailable, match="unreachable"):
        ms._connect()


def test_prep_caches_prepared_statements(ms):
    s = ms._connect()
    a = ms._prep(s, "k", "SELECT 1")
    assert ms._prep(s, "k", "SELECT 2") is a


def test_coverage_none_then_range(ms):
    assert ms.coverage("AAA") is None
    ms.put_ohlc("AAA", _df(3, end=date(2026, 1, 10)))
    assert ms.coverage("AAA") == (date(2026, 1, 8), date(2026, 1, 10), 3)


def test_coverage_none_when_row_missing(ms):
    s = ms._connect()
    s.execute = lambda q, p=None: types.SimpleNamespace(one=lambda: None)
    assert ms.coverage("ZZZ") is None


def test_coverage_handles_datetime_like_values(ms):
    s = ms._connect()
    mn, mx = datetime(2026, 1, 1, 5), datetime(2026, 1, 9, 5)
    s.execute = lambda q, p=None: types.SimpleNamespace(
        one=lambda: types.SimpleNamespace(mn=mn, mx=mx, n=7))
    assert ms.coverage("T") == (date(2026, 1, 1), date(2026, 1, 9), 7)


def test_put_ohlc_skips_none_and_empty(ms):
    ms.put_ohlc("A", None)
    ms.put_ohlc("A", pd.DataFrame())
    assert ms._test.session.bars == {} and not FakeProducer.instances


def test_put_ohlc_writes_rows_and_emits_cdc(ms):
    ms.put_ohlc("AAA", _df(2, end=date(2026, 3, 5)))
    rows = ms._test.session.bars["AAA"]
    assert rows[date(2026, 3, 5)] == (1.0, 3.0, 0.5, 2.0, 10.0)
    assert ms._test.state["conc"] == 64
    topic, key, val = FakeProducer.instances[0].sent[0]
    assert topic == "ohlc.cdc" and key == "AAA"
    import json
    assert json.loads(val) == {"op": "upsert", "table": "ohlc_bars", "ticker": "AAA",
                               "n_bars": 2, "latest": "2026-03-05"}
    assert FakeProducer.instances[0].timeout == 10


def test_put_ohlc_close_only_fills_other_columns(ms):
    ms.put_ohlc("C", _df(1, end=date(2026, 3, 5), with_cols=False))
    assert ms._test.session.bars["C"][date(2026, 3, 5)] == (2.0, 2.0, 2.0, 2.0, 0.0)


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="market_store.py put_ohlc: `r.get('Volume', 0) or 0` keeps NaN (truthy), "
                          "so a NaN volume is written to Cassandra as NaN instead of 0")
def test_put_ohlc_nan_volume_becomes_zero(ms):
    df = _df(1, end=date(2026, 3, 5))
    df["Volume"] = float("nan")
    ms.put_ohlc("V", df)
    assert ms._test.session.bars["V"][date(2026, 3, 5)][4] == 0.0


def test_put_ohlc_non_datetime_index_latest_none(ms):
    df = _df(1, with_cols=True)
    df.index = [date(2026, 1, 1)]
    ms.put_ohlc("N", df)
    import json
    assert json.loads(FakeProducer.instances[0].sent[0][2])["latest"] == "None"


def test_emit_cdc_producer_reused_and_bootstrap_env(ms, monkeypatch):
    monkeypatch.setenv("KAFKA_BOOTSTRAP", "k:1")
    ms._emit_cdc("A", 1, "d")
    ms._emit_cdc("B", 2, "d")
    assert len(FakeProducer.instances) == 1
    assert FakeProducer.instances[0].conf == {"bootstrap.servers": "k:1"}
    assert len(FakeProducer.instances[0].sent) == 2


def test_emit_cdc_default_bootstrap(ms, monkeypatch):
    monkeypatch.delenv("KAFKA_BOOTSTRAP", raising=False)
    ms._emit_cdc("A", 1, "d")
    assert FakeProducer.instances[0].conf == {"bootstrap.servers": "localhost:9092"}


def test_emit_cdc_failures_raise(ms):
    FakeProducer.raise_on_produce = RuntimeError("broker down")
    with pytest.raises(ms.CDCPublishError, match="failed to publish CDC event for A: broker down"):
        ms._emit_cdc("A", 1, "d")
    FakeProducer.raise_on_produce = None
    FakeProducer.flush_remaining = 3
    with pytest.raises(ms.CDCPublishError, match="not delivered within 10s \\(3 still queued\\)"):
        ms._emit_cdc("A", 1, "d")
    FakeProducer.flush_remaining = 0
    FakeProducer.deliver_error = "boom"
    with pytest.raises(ms.CDCPublishError, match="rejected by broker: boom"):
        ms._emit_cdc("A", 1, "d")


def test_get_ohlc_empty_and_sorted(ms):
    assert ms.get_ohlc("NONE").empty
    ms._test.session.bars["X"] = {date(2026, 1, 3): (1, 2, 0, 1.5, 9), date(2026, 1, 1): (1, 2, 0, 1.4, 8)}
    df = ms.get_ohlc("X")
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert list(df["Close"]) == [1.4, 1.5] and df.index.is_monotonic_increasing
    assert isinstance(df.index, pd.DatetimeIndex) and df.index.name == "Date"


def _fake_yf(monkeypatch, calls, data):
    mod = types.ModuleType("apiclient")

    def yf_download(tickers, **kw):
        calls.append((list(tickers), kw))
        return {t: data[t] for t in tickers if t in data}

    mod.yf_download = yf_download
    monkeypatch.setitem(sys.modules, "apiclient", mod)


def test_cached_download_unavailable_raises_before_yf(ms, monkeypatch):
    calls = []
    _fake_yf(monkeypatch, calls, {})
    ms._test.state["fail"] = True
    with pytest.raises(ms.MarketStoreUnavailable):
        ms.cached_download(["A"])
    assert calls == []


def test_cached_download_miss_hit_stale_paths(ms, monkeypatch, capsys):
    today = date.today()
    sess = ms._connect()
    full_start = today - timedelta(days=int(5 * 365) - 30)
    # FRESH: enough history, up to today
    sess.bars["FRESH"] = {full_start: (1, 1, 1, 1, 1), today: (1, 1, 1, 9, 1)}
    # STALE: enough history but last bar older than refresh_days+4
    old = today - timedelta(days=30)
    sess.bars["STALE"] = {full_start: (1, 1, 1, 1, 1), old: (1, 1, 1, 5, 1)}
    # SHORT: not enough history -> full pull
    sess.bars["SHORT"] = {today - timedelta(days=10): (1, 1, 1, 1, 1)}
    calls = []
    new_stale = _df(2, end=today)
    new_miss = _df(3, end=today)
    data = {"STALE": new_stale, "SHORT": new_miss, "NEW": new_miss}
    _fake_yf(monkeypatch, calls, data)
    out = ms.cached_download(["FRESH", "STALE", "SHORT", "NEW"], years=5)
    assert set(out) == {"FRESH", "STALE", "SHORT", "NEW"}
    stale_call, miss_call = calls
    assert stale_call == (["STALE"], {"start": str(old + timedelta(days=1))})
    assert miss_call == (["SHORT", "NEW"], {"period": "5y"})
    assert list(out["FRESH"]["Close"]) == [1, 9]
    assert today in {d.date() for d in out["STALE"].index}      # incremental upsert landed
    err = capsys.readouterr().err
    assert "1 stale -> fetching only new bars since last cache" in err
    assert "downloading 2 misses" in err


def test_cached_download_all_fresh_message_no_network(ms, monkeypatch, capsys):
    today = date.today()
    sess = ms._connect()
    sess.bars["F"] = {today - timedelta(days=int(5 * 365) - 30): (1, 1, 1, 1, 1), today: (1, 1, 1, 2, 1)}
    calls = []
    _fake_yf(monkeypatch, calls, {})
    out = ms.cached_download(["F"])
    assert list(out) == ["F"] and calls == []
    assert "all 1 tickers served from Cassandra (no network)" in capsys.readouterr().err


def test_cached_download_fresh_boundary_refresh_days(ms, monkeypatch):
    today = date.today()
    sess = ms._connect()
    start = today - timedelta(days=int(5 * 365) - 30)
    edge = today - timedelta(days=3 + 4)          # == fresh_cut -> still fresh
    sess.bars["EDGE"] = {start: (1, 1, 1, 1, 1), edge: (1, 1, 1, 2, 1)}
    one_over = today - timedelta(days=3 + 5)
    sess.bars["OVER"] = {start: (1, 1, 1, 1, 1), one_over: (1, 1, 1, 2, 1)}
    calls = []
    _fake_yf(monkeypatch, calls, {})
    ms.cached_download(["EDGE", "OVER"])
    assert [c[0] for c in calls] == [["OVER"]]
