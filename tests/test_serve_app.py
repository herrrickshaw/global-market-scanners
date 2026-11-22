"""serve.py: catalog queries, validator, and every FastAPI route (run against a minimal fake fastapi)."""
import importlib
import os
import sys
import types

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class HTTPException(Exception):
    def __init__(self, status_code, detail=None):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


class FakeApp:
    def __init__(self, title=None, version=None):
        self.title, self.version, self.routes = title, version, {}

    def _reg(self, method):
        def deco_factory(path):
            def deco(fn):
                self.routes[(method, path)] = fn
                return fn
            return deco
        return deco_factory

    def __getattr__(self, name):
        if name in ("get", "post", "put", "delete"):
            return self._reg(name.upper())
        raise AttributeError(name)


@pytest.fixture
def sv(monkeypatch):
    fa = types.ModuleType("fastapi")
    fa.FastAPI, fa.HTTPException = FakeApp, HTTPException
    fa.Query = lambda default=..., **k: default
    fa.Body = lambda default=..., **k: default
    monkeypatch.setitem(sys.modules, "fastapi", fa)
    monkeypatch.delitem(sys.modules, "serve", raising=False)
    mod = importlib.import_module("serve")
    yield mod
    sys.modules.pop("serve", None)


class Con:
    def __init__(self):
        self.sql, self.closed = [], False

    def execute(self, q):
        self.sql.append(q)
        return types.SimpleNamespace(df=lambda: pd.DataFrame({"a": [1, 2]}))

    def close(self):
        self.closed = True


@pytest.fixture
def app(sv, monkeypatch):
    con = Con()
    monkeypatch.setattr(sv, "_connect", lambda: con)
    a = sv.create_app()
    a.con = con
    return a


def test_build_query(sv):
    assert "LIMIT 7" in sv.build_query("ggg", 7) and sv.build_query("ggg").endswith("LIMIT 25")
    assert "LIMIT" not in sv.build_query("markets")
    assert sv.build_query("ggg", "9").endswith("LIMIT 9")                       # int-coerced (injection guard)
    with pytest.raises(ValueError):
        sv.build_query("ggg", "1; drop")
    with pytest.raises(KeyError, match="unknown query 'zzz'"):
        sv.build_query("zzz")


def test_validate_predicate_branches(sv):
    assert sv.validate_predicate("roe > 15 AND de < 1 or pe<2") == "roe > 15 AND de < 1 or pe<2"
    with pytest.raises(ValueError, match="unknown identifiers.*\\['password'\\]"):
        sv.validate_predicate("password > 1")
    with pytest.raises(ValueError, match="illegal characters"):
        sv.validate_predicate("roe>1 -- x")
    with pytest.raises(ValueError, match="shape not allowed"):
        sv.validate_predicate("roe>1 + 2")


def test_connect_builds_views(sv, monkeypatch, tmp_path):
    con = Con()
    duck = types.ModuleType("duckdb"); duck.connect = lambda p: (con.sql.append("CONNECT " + os.path.basename(p)), con)[1]
    wh = types.ModuleType("warehouse"); wh.build = lambda c: con.sql.append("BUILD")
    monkeypatch.setitem(sys.modules, "duckdb", duck); monkeypatch.setitem(sys.modules, "warehouse", wh)
    assert sv._connect() is con
    assert con.sql == ["CONNECT market.duckdb", "BUILD", "SET max_expression_depth=10000"]


def test_basic_routes(app):
    r = app.routes
    assert r[("GET", "/health")]() == {"status": "ok"}
    assert r[("GET", "/markets")]() == [{"a": 1}, {"a": 2}] and app.con.closed
    out = r[("GET", "/ggg")](None, 5)
    assert out == [{"a": 1}, {"a": 2}] and "LIMIT 5" in app.con.sql[-1] and "market=" not in app.con.sql[-1]
    r[("GET", "/ggg")]("US", 3)
    assert "WHERE code='GGG' AND market='US'" in app.con.sql[-1]
    assert r[("GET", "/screen/{name}")]("dvm_dist", 10) == [{"a": 1}, {"a": 2}]
    with pytest.raises(HTTPException) as e:
        r[("GET", "/screen/{name}")]("nope", 10)
    assert e.value.status_code == 404


def test_filter_route(app):
    f = app.routes[("GET", "/filter")]
    assert f("roe>15", 4) == [{"a": 1}, {"a": 2}]
    assert "WHERE roe>15 ORDER BY c.composite DESC LIMIT 4" in app.con.sql[-1]
    with pytest.raises(HTTPException) as e:
        f("roe>15; drop table x", 4)
    assert e.value.status_code == 400 and "illegal" in e.value.detail
    n = len(app.con.sql)
    with pytest.raises(HTTPException):
        f("secret>1", 4)
    assert len(app.con.sql) == n                                               # no query executed on rejection


class FakeVC:
    def __init__(self):
        self.store, self.calls, self.closed = {}, [], False
        FakeVC.last = self

    def connect(self):
        return types.SimpleNamespace(close=lambda: setattr(self, "closed", True))

    def list_ids(self, con, kind):
        return sorted(self.store)

    def create(self, con, kind, name, payload):
        if name in self.store:
            raise ValueError(f"{name} exists")
        self.store[name] = payload
        return payload

    def read(self, con, kind, name):
        return self.store.get(name)

    def add_to_list(self, con, kind, name, field, vals):
        if name not in self.store:
            raise KeyError(name)
        self.store[name][field] += vals

    def remove_from_list(self, con, kind, name, field, vals):
        if name not in self.store:
            raise KeyError(name)
        self.store[name][field] = [x for x in self.store[name][field] if x not in vals]

    def update(self, con, kind, name, patch):
        if name not in self.store:
            raise KeyError(name)
        self.store[name].update(patch)

    def delete(self, con, kind, name):
        return self.store.pop(name, None) is not None

    def history(self, con, kind, name):
        return [{"v": 1}]


@pytest.fixture
def wl(app, monkeypatch):
    vc = FakeVC()
    mod = types.ModuleType("watchlist_store")
    mod.connect = vc.connect
    for n in ("list_ids", "create", "read", "add_to_list", "remove_from_list", "update", "delete", "history"):
        setattr(mod, n, getattr(vc, n))
    monkeypatch.setitem(sys.modules, "watchlist_store", mod)
    app.vc = vc
    return app


def test_watchlist_crud(wl):
    r = wl.routes
    assert r[("GET", "/watchlists")]() == []
    out = r[("POST", "/watchlists/{name}")]("w1", {"tickers": ["aapl", "msft"], "note": "n"})
    assert out == {"tickers": ["AAPL", "MSFT"], "note": "n"} and wl.vc.closed
    with pytest.raises(HTTPException) as e:
        r[("POST", "/watchlists/{name}")]("w1", {})
    assert e.value.status_code == 409
    assert r[("POST", "/watchlists/{name}")]("w2", {})["tickers"] == [] and r[("GET", "/watchlists")]() == ["w1", "w2"]
    assert r[("GET", "/watchlists/{name}")]("w1")["note"] == "n"
    with pytest.raises(HTTPException) as e:
        r[("GET", "/watchlists/{name}")]("zz")
    assert e.value.status_code == 404 and "watchlist/zz not found" in e.value.detail
    upd = r[("PUT", "/watchlists/{name}")]("w1", {"add": ["nvda"], "remove": ["aapl"], "note": "new"})
    assert upd == {"tickers": ["MSFT", "NVDA"], "note": "new"}
    assert r[("PUT", "/watchlists/{name}")]("w1", {}) == upd                        # empty body is a no-op
    assert r[("PUT", "/watchlists/{name}")]("w1", {"note": ""})["note"] == ""       # "note" key present even if empty
    with pytest.raises(HTTPException) as e:
        r[("PUT", "/watchlists/{name}")]("zz", {"add": ["x"]})
    assert e.value.status_code == 404
    assert r[("GET", "/watchlists/{name}/history")]("w1") == [{"v": 1}]
    assert r[("DELETE", "/watchlists/{name}")]("w1") == {"deleted": True} and r[("DELETE", "/watchlists/{name}")]("w1") == {"deleted": False}


def test_lazy_app(sv, monkeypatch):
    created = []
    monkeypatch.setattr(sv, "create_app", lambda: (created.append(1), FakeApp("t"))[1])
    monkeypatch.setattr(sv._LazyApp, "_app", None)
    assert sv.app.title == "t" and sv.app.title == "t" and created == [1]            # built once, cached
