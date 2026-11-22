"""kafka_cdc_consumer / kafka_signal_consumer / flink_cdc_consumer / flink_screens with fake brokers."""
import importlib
import json
import os
import sys
import types
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class Msg:
    def __init__(self, value=b"", err=None):
        self._v, self._e = value, err

    def value(self):
        return self._v

    def error(self):
        return self._e


def seq(*vals):
    it = iter(vals)
    return lambda: next(it)


class Clock:
    def __init__(self, step=1.0, start=1000.0):
        self.t = start - step
        self.step = step

    def __call__(self):
        self.t += self.step
        return self.t


def _load(monkeypatch, name, polls):
    state = {"closed": False, "conf": None, "subscribed": None, "polls": list(polls)}

    class Consumer:
        def __init__(self, conf):
            state["conf"] = conf

        def subscribe(self, t):
            state["subscribed"] = t

        def poll(self, timeout=None):
            if not state["polls"]:
                raise KeyboardInterrupt
            item = state["polls"].pop(0)
            if isinstance(item, BaseException):
                raise item
            return item

        def close(self):
            state["closed"] = True

    ck = types.ModuleType("confluent_kafka")
    ck.Consumer = Consumer
    monkeypatch.setitem(sys.modules, "confluent_kafka", ck)
    monkeypatch.delitem(sys.modules, name, raising=False)
    mod = importlib.import_module(name)
    yield_state = state
    return mod, yield_state


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    for n in ("kafka_cdc_consumer", "kafka_signal_consumer", "flink_cdc_consumer", "flink_screens"):
        sys.modules.pop(n, None)


def cdc(ticker, n):
    return Msg(json.dumps({"ticker": ticker, "n_bars": n}).encode())


# ───────────────────────────── kafka_cdc_consumer ─────────────────────────────
def test_parse_cdc(monkeypatch):
    kc, _ = _load(monkeypatch, "kafka_cdc_consumer", [])
    assert kc.parse_cdc(b'{"ticker": "A", "n_bars": "7"}') == ("A", 7)
    assert kc.parse_cdc(b'{"ticker": "A"}') == ("A", 0)
    for bad in (b"not json", b"{}", b'{"ticker": "A", "n_bars": "x"}', None):
        assert kc.parse_cdc(bad) is None


def test_cdc_run_max_messages_flushes_sorted_totals(monkeypatch, capsys):
    kc, st = _load(monkeypatch, "kafka_cdc_consumer", [cdc("B", 2), cdc("A", 1), Msg(err="e"), Msg(b"garbage"),
                                                       cdc("B", 3), cdc("C", 9)])
    monkeypatch.setattr(kc.time, "time", Clock(0.001))
    kc.run(window_seconds=999, max_messages=3)
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("[kafka-cdc] consuming topic=ohlc.cdc")
    assert out[1:] == ["[kafka-cdc] ticker=A bars_mutated_in_window=1", "[kafka-cdc] ticker=B bars_mutated_in_window=5"]
    assert st["closed"] and st["subscribed"] == ["ohlc.cdc"]
    assert st["conf"]["group.id"] == "kafka-cdc-consumer" and st["conf"]["auto.offset.reset"] == "earliest"


def test_cdc_run_window_rolls_over_and_resets(monkeypatch, capsys):
    kc, _ = _load(monkeypatch, "kafka_cdc_consumer", [cdc("A", 1), cdc("A", 2), cdc("A", 4)])
    # time.time() calls: window_start, last_msg_at, then once per loop iteration
    monkeypatch.setattr(kc.time, "time", seq(0, 0, 1, 2, 3))
    kc.run(window_seconds=2)                                      # ends via KeyboardInterrupt when polls run out
    lines = [l for l in capsys.readouterr().out.splitlines() if "ticker=" in l]
    assert [l.split("=")[-1] for l in lines] == ["3", "4"]        # window closes at t=2 (1+2), next starts at 0


def test_cdc_run_idle_exit_requires_a_message_first(monkeypatch, capsys):
    kc, st = _load(monkeypatch, "kafka_cdc_consumer", [None, None, None])
    monkeypatch.setattr(kc.time, "time", Clock(10.0))
    kc.run(window_seconds=10_000, idle_exit_after=1.0)            # never seen a message -> keeps looping until Ctrl-C
    assert "idle for" not in capsys.readouterr().out
    kc, st = _load(monkeypatch, "kafka_cdc_consumer", [cdc("A", 1), None, None])
    monkeypatch.setattr(kc.time, "time", Clock(10.0))
    kc.run(window_seconds=10_000, idle_exit_after=1.0)
    out = capsys.readouterr().out
    assert "ticker=A bars_mutated_in_window=1" in out and "idle for 1.0s, exiting" in out
    assert st["closed"]


def test_cdc_flush_empty_prints_nothing(monkeypatch, capsys):
    kc, _ = _load(monkeypatch, "kafka_cdc_consumer", [])
    kc._flush({}, 0.0)
    assert capsys.readouterr().out == ""


def test_cdc_main_args(monkeypatch):
    kc, _ = _load(monkeypatch, "kafka_cdc_consumer", [])
    got = {}
    monkeypatch.setattr(kc, "run", lambda **k: got.update(k))
    monkeypatch.setattr(sys, "argv", ["x", "--window", "7", "--max-messages", "3", "--idle-exit-after", "2.5"])
    kc.main()
    assert got == {"window_seconds": 7, "max_messages": 3, "idle_exit_after": 2.5}
    monkeypatch.setattr(sys, "argv", ["x"])
    kc.main()
    assert got == {"window_seconds": 30, "max_messages": None, "idle_exit_after": None}


# ───────────────────────────── kafka_signal_consumer ─────────────────────────────
def sig(s):
    return Msg(json.dumps({"signal": s} if s else {}).encode())


def test_signal_run_counts_and_flushes_on_interrupt(monkeypatch, capsys):
    ks, st = _load(monkeypatch, "kafka_signal_consumer",
                   [sig("BREAKOUT"), sig("NEUTRAL"), sig("BREAKOUT"), sig(None), Msg(b"junk"), Msg(err="e")])
    monkeypatch.setattr(ks.time, "time", Clock(0.001))
    ks.run(window_seconds=999)
    lines = capsys.readouterr().out.splitlines()
    body = [l for l in lines if "signal=" in l]
    assert [l.split("signal=")[1] for l in body] == ["BREAKOUT n=2", "NEUTRAL n=1", "UNKNOWN n=1"]
    assert "window_end=" in body[0] and st["closed"]
    assert st["conf"]["group.id"] == "kafka-signal-consumer" and st["subscribed"] == ["scan.signals"]


def test_signal_window_rollover(monkeypatch, capsys):
    ks, _ = _load(monkeypatch, "kafka_signal_consumer", [sig("A"), sig("A"), sig("B")])
    monkeypatch.setattr(ks.time, "time", seq(0, 0, 1, 6, 7))
    ks.run(window_seconds=5)
    body = [l.split("signal=")[1] for l in capsys.readouterr().out.splitlines() if "signal=" in l]
    assert body == ["A n=2", "B n=1"]                             # rollover at t=6 flushes A,A; B lands in new window


def test_signal_idle_exit(monkeypatch, capsys):
    ks, st = _load(monkeypatch, "kafka_signal_consumer", [None, None])
    monkeypatch.setattr(ks.time, "time", Clock(10.0))
    ks.run(window_seconds=10_000, idle_exit_after=1.0)
    assert "idle for" not in capsys.readouterr().out
    ks, st = _load(monkeypatch, "kafka_signal_consumer", [sig("A"), None, None])
    monkeypatch.setattr(ks.time, "time", Clock(10.0))
    ks.run(window_seconds=10_000, idle_exit_after=1.0)
    out = capsys.readouterr().out
    assert "signal=A n=1" in out and "idle for 1.0s, exiting" in out


def test_signal_flush_empty_and_main(monkeypatch, capsys):
    ks, _ = _load(monkeypatch, "kafka_signal_consumer", [])
    ks._flush(ks.Counter(), 0.0)
    assert capsys.readouterr().out == ""
    got = {}
    monkeypatch.setattr(ks, "run", lambda **k: got.update(k))
    monkeypatch.setattr(sys, "argv", ["x"])
    ks.main()
    assert got == {"window_seconds": 5, "idle_exit_after": None}
    monkeypatch.setattr(sys, "argv", ["x", "--window", "9", "--idle-exit-after", "4"])
    ks.main()
    assert got == {"window_seconds": 9, "idle_exit_after": 4.0}


# ───────────────────────────── flink_cdc_consumer ─────────────────────────────
@pytest.fixture
def flink(monkeypatch):
    names = ["pyflink", "pyflink.common", "pyflink.common.typeinfo", "pyflink.datastream",
             "pyflink.datastream.connectors", "pyflink.datastream.connectors.kafka",
             "pyflink.datastream.window", "pyflink.common.time", "pyflink.table"]
    mods = {n: mock.MagicMock(name=n) for n in names}
    for n, m in mods.items():
        monkeypatch.setitem(sys.modules, n, m)
    monkeypatch.delitem(sys.modules, "flink_cdc_consumer", raising=False)
    mod = importlib.import_module("flink_cdc_consumer")
    return mod, mods


def test_flink_parse_cdc(flink):
    fc, _ = flink
    assert fc.parse_cdc('{"ticker": "A", "n_bars": 3}') == ("A", 3)
    assert fc.parse_cdc('{"ticker": "A"}') == ("A", 0)
    assert fc.parse_cdc("bad") is None and fc.parse_cdc('{"n_bars": 1}') is None


def test_flink_build_pipeline_wiring_and_lambdas(flink):
    fc, mods = flink
    env = mock.MagicMock()
    stream = env.from_source.return_value
    result = fc.build_pipeline(env)
    assert result is stream
    builder = mods["pyflink.datastream.connectors.kafka"].KafkaSource.builder.return_value
    builder.set_bootstrap_servers.assert_called_with(fc.KAFKA_BOOTSTRAP)
    builder.set_bootstrap_servers.return_value.set_topics.assert_called_with("ohlc.cdc")
    # exercise the lambdas handed to the chain
    parsed = stream.map.return_value
    flt = parsed.filter.call_args[0][0]
    assert flt(("A", 1)) is True and flt(None) is False
    keyed = parsed.filter.return_value.key_by
    assert keyed.call_args[0][0](("T", 5)) == "T"
    reduce_fn = keyed.return_value.window.return_value.reduce.call_args[0][0]
    assert reduce_fn(("T", 2), ("T", 3)) == ("T", 5)
    fmt = reduce_fn  # noqa
    map_fn = keyed.return_value.window.return_value.reduce.return_value.map.call_args[0][0]
    assert map_fn(("T", 5)) == "[flink-cdc] ticker=T bars_mutated_in_window=5"


def test_flink_main_executes_job(flink, monkeypatch, capsys):
    fc, mods = flink
    env = mods["pyflink.datastream"].StreamExecutionEnvironment.get_execution_environment.return_value
    monkeypatch.setattr(sys, "argv", ["x"])
    fc.main()
    env.add_jars.assert_called_with(f"file://{fc.KAFKA_JAR}")
    env.set_parallelism.assert_called_with(1)
    env.execute.assert_called_with("ohlc-cdc-consumer")
    assert "starting job" in capsys.readouterr().out


# ───────────────────────────── flink_screens ─────────────────────────────
def test_flink_screens_without_pyflink_exits(monkeypatch, capsys):
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name.startswith("pyflink"):
            raise ImportError("nope")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    monkeypatch.delitem(sys.modules, "flink_screens", raising=False)
    fs = importlib.import_module("flink_screens")
    with pytest.raises(SystemExit) as e:
        fs.main()
    assert e.value.code == 1 and "PyFlink not installed" in capsys.readouterr().err


def test_flink_screens_runs_three_statements(monkeypatch):
    tbl = types.ModuleType("pyflink.table")
    env = mock.MagicMock()
    tbl.TableEnvironment = mock.MagicMock()
    tbl.TableEnvironment.create.return_value = env
    tbl.EnvironmentSettings = mock.MagicMock()
    monkeypatch.setitem(sys.modules, "pyflink", types.ModuleType("pyflink"))
    monkeypatch.setitem(sys.modules, "pyflink.table", tbl)
    monkeypatch.delitem(sys.modules, "flink_screens", raising=False)
    fs = importlib.import_module("flink_screens")
    fs.main()
    sqls = [c.args[0] for c in env.execute_sql.call_args_list]
    assert len(sqls) == 3
    assert "`close` DOUBLE" in sqls[0] and "'topic' = 'scan.signals'" in sqls[0]
    assert "'connector' = 'print'" in sqls[1]
    assert "TUMBLE(ts, INTERVAL '5' SECOND)" in sqls[2]
    env.execute_sql.return_value.wait.assert_called_once()
    env.get_config.return_value.set.assert_called_with("pipeline.jars", f"file://{fs.KAFKA_JAR}")
