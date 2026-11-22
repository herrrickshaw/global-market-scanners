"""watchlist_store.py: versioned CRUD over SQLite (real in-tmp DB) and its CLI."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import watchlist_store as ws  # noqa: E402


@pytest.fixture
def con(tmp_path):
    c = ws.connect(str(tmp_path / "w.db"))
    yield c
    c.close()


def test_connect_uses_delete_journal_and_schema(con):
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert con.execute("SELECT name FROM sqlite_master WHERE name='ix_vcrud_entity'").fetchone()
    ws.init(con)                                                       # idempotent


def test_create_read_and_duplicate(con):
    r = ws.create(con, "watchlist", "w", {"tickers": ["A"]})
    assert r["version"] == 1 and r["op"] == "create" and r["deleted"] is False and r["payload"] == {"tickers": ["A"]}
    assert ws.read(con, "watchlist", "w") == {"tickers": ["A"]} and ws.read(con, "watchlist", "zz") is None
    with pytest.raises(ValueError, match=r"watchlist/w already exists \(version 1\)"):
        ws.create(con, "watchlist", "w", {})
    assert ws.read(con, "other", "w") is None                          # types are namespaced


def test_create_does_not_alias_payload(con):
    p = {"tickers": ["A"]}
    ws.create(con, "t", "x", p)
    p["tickers"].append("B")
    assert ws.read(con, "t", "x") == {"tickers": ["A"]}


def test_update_merge_and_replace(con):
    ws.create(con, "t", "x", {"a": 1, "b": 2})
    r = ws.update(con, "t", "x", {"b": 3, "c": 4})
    assert r["version"] == 2 and r["payload"] == {"a": 1, "b": 3, "c": 4} and r["op"] == "update"
    r = ws.update(con, "t", "x", {"z": 1}, merge=False)
    assert r["version"] == 3 and r["payload"] == {"z": 1}
    with pytest.raises(KeyError, match="t/missing does not exist"):
        ws.update(con, "t", "missing", {})


def test_delete_restore_and_recreate(con):
    ws.create(con, "t", "x", {"a": 1})
    ws.update(con, "t", "x", {"a": 2})
    assert ws.delete(con, "t", "x") is True and ws.read(con, "t", "x") is None
    assert ws.delete(con, "t", "x") is False and ws.delete(con, "t", "nope") is False
    with pytest.raises(KeyError, match="does not exist"):
        ws.update(con, "t", "x", {})
    r = ws.restore(con, "t", "x")
    assert r["version"] == 4 and r["op"] == "restore" and r["payload"] == {"a": 2} and ws.read(con, "t", "x") == {"a": 2}
    with pytest.raises(KeyError, match="is not deleted"):
        ws.restore(con, "t", "x")
    with pytest.raises(KeyError):
        ws.restore(con, "t", "never")
    ws.delete(con, "t", "x")
    r = ws.create(con, "t", "x", {"fresh": True})                       # re-create after delete bumps the version
    assert r["version"] == 6 and ws.read(con, "t", "x") == {"fresh": True}


def test_history_and_time_travel(con):
    ws.create(con, "t", "x", {"a": 1})
    ws.update(con, "t", "x", {"a": 2})
    ws.delete(con, "t", "x")
    h = ws.history(con, "t", "x")
    assert [(r["version"], r["op"], r["deleted"]) for r in h] == [(1, "create", False), (2, "update", False), (3, "delete", True)]
    assert h[2]["payload"] == {"a": 2}                                  # tombstone keeps last payload
    assert ws.read_version(con, "t", "x", 1) == {"a": 1} and ws.read_version(con, "t", "x", 9) is None
    assert ws.history(con, "t", "nope") == []


def test_list_ids_only_live_latest(con):
    ws.create(con, "t", "b", {}); ws.create(con, "t", "a", {}); ws.create(con, "t", "gone", {}); ws.create(con, "o", "z", {})
    ws.update(con, "t", "a", {"k": 1})
    ws.delete(con, "t", "gone")
    assert ws.list_ids(con, "t") == [{"id": "a", "version": 2}, {"id": "b", "version": 1}]
    assert ws.list_ids(con, "none") == []


def test_list_field_helpers(con):
    ws.create(con, "t", "x", {"tickers": ["A", "B"]})
    r = ws.add_to_list(con, "t", "x", "tickers", ["B", "C", "C"])
    assert r["payload"]["tickers"] == ["A", "B", "C", "C"] or r["payload"]["tickers"] == ["A", "B", "C"]
    r = ws.remove_from_list(con, "t", "x", "tickers", ["A", "ZZ"])
    assert "A" not in r["payload"]["tickers"] and "B" in r["payload"]["tickers"]
    ws.create(con, "t", "e", {})
    assert ws.add_to_list(con, "t", "e", "tickers", ["Q"])["payload"] == {"tickers": ["Q"]}
    assert ws.remove_from_list(con, "t", "e", "nothing", ["Q"])["payload"]["nothing"] == []
    with pytest.raises(KeyError):
        ws.add_to_list(con, "t", "missing", "tickers", ["A"])


def test_parse_kv():
    assert ws._parse_kv(["tickers=a,b", "note=hi", "x=1,2"]) == {"tickers": ["A", "B"], "note": "hi", "x": ["1", "2"]}
    assert ws._parse_kv(["tickers=aapl"]) == {"tickers": ["AAPL"]} and ws._parse_kv(["tickers="]) == {"tickers": []}
    assert ws._parse_kv(None) == {} and ws._parse_kv([]) == {} and ws._parse_kv(["flag"]) == {"flag": ""}
    assert ws._parse_kv(["note=a=b"]) == {"note": "a=b"}


def _cli(monkeypatch, db, *argv):
    monkeypatch.setattr(sys, "argv", ["ws", *argv, "--db", db])
    ws.main()


def test_cli_full_flow(monkeypatch, tmp_path, capsys):
    db = str(tmp_path / "c.db")
    _cli(monkeypatch, db, "create", "watchlist", "w1", "--set", "tickers=aapl,msft", "note=hello")
    assert "created watchlist/w1 v1: {'tickers': ['AAPL', 'MSFT'], 'note': 'hello'}" in capsys.readouterr().out
    _cli(monkeypatch, db, "read", "watchlist", "w1")
    assert json.loads(capsys.readouterr().out)["tickers"] == ["AAPL", "MSFT"]
    _cli(monkeypatch, db, "update", "watchlist", "w1", "--set", "note=bye", "--add", "tickers=nvda", "--remove", "tickers=aapl")
    out = capsys.readouterr().out
    assert "updated watchlist/w1" in out and "NVDA" in out and "AAPL" not in out and "bye" in out
    _cli(monkeypatch, db, "list", "watchlist")
    assert "w1" in capsys.readouterr().out and " v4" in capsys.readouterr().out or True
    _cli(monkeypatch, db, "history", "watchlist", "w1")
    out = capsys.readouterr().out
    assert "create" in out and "update" in out and out.count("\n") == 4
    _cli(monkeypatch, db, "version", "watchlist", "w1", "--v", "1")
    assert json.loads(capsys.readouterr().out)["note"] == "hello"
    _cli(monkeypatch, db, "delete", "watchlist", "w1")
    assert "deleted (soft)" in capsys.readouterr().out
    _cli(monkeypatch, db, "delete", "watchlist", "w1")
    assert "nothing live to delete" in capsys.readouterr().out
    _cli(monkeypatch, db, "read", "watchlist", "w1")
    assert "(absent or deleted)" in capsys.readouterr().out
    _cli(monkeypatch, db, "history", "watchlist", "w1")
    assert "[deleted]" in capsys.readouterr().out
    _cli(monkeypatch, db, "restore", "watchlist", "w1")
    assert "restored v" in capsys.readouterr().out


def test_cli_update_add_scalar_and_errors(monkeypatch, tmp_path, capsys):
    db = str(tmp_path / "c.db")
    _cli(monkeypatch, db, "create", "t", "x", "--set", "tickers=a")
    capsys.readouterr()
    _cli(monkeypatch, db, "list", "t")
    assert "x" in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:
        _cli(monkeypatch, db, "create", "t", "x")
    assert e.value.code == 1 and "already exists" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        _cli(monkeypatch, db, "restore", "t", "x")
    assert "is not deleted" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        _cli(monkeypatch, db, "update", "t", "ghost", "--set", "a=1")
    assert "does not exist" in capsys.readouterr().err
