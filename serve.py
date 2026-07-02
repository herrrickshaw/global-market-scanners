#!/usr/bin/env python3
"""
serve.py
--------
Serving layer (SAFe F8.1): the platform generated results but there was no query
surface — you had to run a script. This exposes the DuckDB warehouse as a small
read-only HTTP API (FastAPI), so results are consumable by a dashboard, a
notebook, or another service.

Endpoints:
  GET /health                      liveness
  GET /markets                     per-market coverage
  GET /ggg?market=US&limit=25      GGG Strong Performers
  GET /screen/{name}               any named warehouse query (see QUERY_CATALOG)
  GET /filter?predicate=roe>15     ad-hoc DVM⋈fundamentals filter (validated)

FastAPI and duckdb are imported lazily inside create_app() so this module (and
its pure query-builder, which is unit-tested) imports with no heavy deps. The
filter predicate is validated against an allow-list to prevent SQL injection.

Usage:
  pip install fastapi uvicorn duckdb
  uvicorn serve:app --reload            # or: python serve.py
"""

from __future__ import annotations

import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

# Named, parameter-free result queries (kept here so the module needs no duckdb).
QUERY_CATALOG = {
    "markets": "SELECT market, count(DISTINCT ticker) tickers, count(*) bars "
               "FROM ohlc GROUP BY 1 ORDER BY tickers DESC",
    "ggg": "SELECT market, ticker, D, V, M, composite, label FROM dvm_composite "
           "WHERE code='GGG' ORDER BY composite DESC LIMIT {limit}",
    "dvm_dist": "SELECT code, label, count(*) n FROM dvm_composite GROUP BY 1,2 ORDER BY n DESC",
    "high_roe_low_de": "SELECT market, ticker, roe, de, pe, sector FROM fundamentals "
                       "WHERE roe>15 AND de<1 AND de IS NOT NULL ORDER BY roe DESC LIMIT {limit}",
}

# tokens allowed in an ad-hoc filter predicate (columns, numbers, operators)
_ALLOWED_COLS = {"roe", "de", "pe", "pb", "D", "V", "M", "composite", "roa",
                 "rev_growth", "earn_growth", "op_margin", "div_yield", "mktcap"}
_PRED_RE = re.compile(r"^[\w\s\.><=!()]+(and|or|[\w\s\.><=!()])*$", re.IGNORECASE)


def build_query(name: str, limit: int = 25) -> str:
    """Return the SQL for a named catalog query (pure; safe to unit-test)."""
    if name not in QUERY_CATALOG:
        raise KeyError(f"unknown query {name!r}; choices={sorted(QUERY_CATALOG)}")
    return QUERY_CATALOG[name].format(limit=int(limit))


def validate_predicate(pred: str) -> str:
    """Allow only comparisons over known columns joined by and/or — reject anything
    with a semicolon, comment, or unknown identifier (SQL-injection guard)."""
    if ";" in pred or "--" in pred or "/*" in pred:
        raise ValueError("illegal characters in predicate")
    idents = set(re.findall(r"[A-Za-z_]\w*", pred))
    bad = idents - _ALLOWED_COLS - {"and", "or", "AND", "OR"}
    if bad:
        raise ValueError(f"unknown identifiers in predicate: {sorted(bad)}")
    if not _PRED_RE.match(pred):
        raise ValueError("predicate shape not allowed")
    return pred


def _connect():
    import duckdb
    import warehouse
    con = duckdb.connect(os.path.join(HERE, "market.duckdb"))
    warehouse.build(con)                      # views are per-session
    con.execute("SET max_expression_depth=10000")
    return con


def create_app():
    from fastapi import FastAPI, HTTPException, Query
    app = FastAPI(title="Global Market Scanners API", version="1.0")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/markets")
    def markets():
        con = _connect()
        try:
            return con.execute(build_query("markets")).df().to_dict("records")
        finally:
            con.close()

    @app.get("/ggg")
    def ggg(market: str | None = None, limit: int = 25):
        con = _connect()
        try:
            q = build_query("ggg", limit)
            if market:
                q = q.replace("WHERE code='GGG'", f"WHERE code='GGG' AND market='{market}'")
            return con.execute(q).df().to_dict("records")
        finally:
            con.close()

    @app.get("/screen/{name}")
    def screen(name: str, limit: int = 25):
        try:
            q = build_query(name, limit)
        except KeyError as e:
            raise HTTPException(404, str(e))
        con = _connect()
        try:
            return con.execute(q).df().to_dict("records")
        finally:
            con.close()

    @app.get("/filter")
    def filter_(predicate: str = Query(...), limit: int = 30):
        try:
            pred = validate_predicate(predicate)
        except ValueError as e:
            raise HTTPException(400, str(e))
        con = _connect()
        try:
            q = (f"SELECT c.market, c.ticker, c.D, c.V, c.M, c.composite, c.code, "
                 f"f.roe, f.de, f.pe FROM dvm_composite c "
                 f"LEFT JOIN fundamentals f ON c.ticker=f.ticker "
                 f"WHERE {pred} ORDER BY c.composite DESC LIMIT {int(limit)}")
            return con.execute(q).df().to_dict("records")
        finally:
            con.close()

    # ── watchlist CRUD (versioned) — the write surface, over vcrud ──────────────
    from fastapi import Body

    def _vc():
        import watchlist_store as vcrud
        return vcrud, vcrud.connect()

    @app.get("/watchlists")
    def wl_list():
        vc, con = _vc()
        try:
            return vc.list_ids(con, "watchlist")
        finally:
            con.close()

    @app.post("/watchlists/{name}")
    def wl_create(name: str, body: dict = Body(...)):
        vc, con = _vc()
        try:
            payload = {"tickers": [t.upper() for t in body.get("tickers", [])],
                       "note": body.get("note", "")}
            return vc.create(con, "watchlist", name, payload)
        except ValueError as e:
            raise HTTPException(409, str(e))
        finally:
            con.close()

    @app.get("/watchlists/{name}")
    def wl_read(name: str):
        vc, con = _vc()
        try:
            p = vc.read(con, "watchlist", name)
            if p is None:
                raise HTTPException(404, f"watchlist/{name} not found")
            return p
        finally:
            con.close()

    @app.put("/watchlists/{name}")
    def wl_update(name: str, body: dict = Body(...)):
        vc, con = _vc()
        try:
            if body.get("add"):
                vc.add_to_list(con, "watchlist", name, "tickers",
                               [t.upper() for t in body["add"]])
            if body.get("remove"):
                vc.remove_from_list(con, "watchlist", name, "tickers",
                                    [t.upper() for t in body["remove"]])
            if "note" in body:
                vc.update(con, "watchlist", name, {"note": body["note"]})
            return vc.read(con, "watchlist", name)
        except KeyError as e:
            raise HTTPException(404, str(e))
        finally:
            con.close()

    @app.delete("/watchlists/{name}")
    def wl_delete(name: str):
        vc, con = _vc()
        try:
            return {"deleted": vc.delete(con, "watchlist", name)}
        finally:
            con.close()

    @app.get("/watchlists/{name}/history")
    def wl_history(name: str):
        vc, con = _vc()
        try:
            return vc.history(con, "watchlist", name)
        finally:
            con.close()

    return app


# Lazily-built module-level app for `uvicorn serve:app`.
class _LazyApp:
    _app = None

    def __getattr__(self, item):
        if _LazyApp._app is None:
            _LazyApp._app = create_app()
        return getattr(_LazyApp._app, item)


app = _LazyApp()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_app(), host="127.0.0.1", port=8000)
