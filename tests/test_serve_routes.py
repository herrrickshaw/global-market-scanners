"""Characterisation of the FastAPI routes built by ``serve.create_app`` (no DB, no network)."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

import serve  # noqa: E402

GOLDEN = Path(__file__).parent / "golden_serve.json"


class _Res:
    def __init__(self, q):
        self.q = q

    def df(self):
        return self

    def to_dict(self, _orient):
        return [{"query": self.q}]


class _Con:
    def __init__(self, log):
        self.log = log

    def execute(self, q):
        self.log.append(q)
        return _Res(q)

    def close(self):
        self.log.append("closed")


def _fake_store(log):
    m = types.ModuleType("watchlist_store")
    m.connect = lambda: log.append("vc.connect") or _Con(log)
    m.list_ids = lambda con, kind: ["a", "b"]
    def create(con, kind, name, payload):
        if name == "dup":
            raise ValueError("exists")
        return {"name": name, **payload}
    m.create = create
    m.read = lambda con, kind, name: None if name == "missing" else {"name": name, "tickers": ["X"]}
    m.add_to_list = lambda *a: log.append(("add", a[2:]))
    m.remove_from_list = lambda *a: log.append(("remove", a[2:]))
    m.update = lambda *a: log.append(("update", a[2:]))
    m.delete = lambda con, kind, name: True
    m.history = lambda con, kind, name: [{"v": 1}]
    return m


def collect(monkeypatch):
    log = []
    monkeypatch.setattr(serve, "_connect", lambda: _Con(log))
    monkeypatch.setitem(sys.modules, "watchlist_store", _fake_store(log))
    client = TestClient(serve.create_app())
    calls = [
        ("GET", "/health", None), ("GET", "/markets", None), ("GET", "/ggg?market=IN&limit=3", None),
        ("GET", "/ggg", None), ("GET", "/screen/nonexistent", None), ("GET", "/filter?predicate=roe%20%3E%2015", None),
        ("GET", "/filter?predicate=drop%20table%3B", None), ("GET", "/watchlists", None),
        ("POST", "/watchlists/w1", {"tickers": ["aapl"], "note": "n"}),
        ("POST", "/watchlists/dup", {}), ("GET", "/watchlists/w1", None), ("GET", "/watchlists/missing", None),
        ("PUT", "/watchlists/w1", {"add": ["x"], "remove": ["y"], "note": "z"}),
        ("DELETE", "/watchlists/w1", None), ("GET", "/watchlists/w1/history", None),
    ]
    out = {"routes": sorted(f"{m} {p}" for p, v in client.get("/openapi.json").json()["paths"].items()
                            for m in v)}
    for method, url, body in calls:
        r = client.request(method, url, json=body)
        out[f"{method} {url}"] = [r.status_code, r.json()]
    out["log"] = json.loads(json.dumps(log, default=str))
    return out


def test_routes_match_golden(monkeypatch):
    assert json.dumps(collect(monkeypatch)) == json.dumps(json.loads(GOLDEN.read_text()))
