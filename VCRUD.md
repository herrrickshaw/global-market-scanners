# vCRUD — versioned CRUD store

[`vcrud.py`](vcrud.py) is a **versioned CRUD** store: Create / Read / Update / Delete
over SQLite where every write is *versioned* and the log is *append-only* — nothing is
ever mutated in place or physically removed. You get a full **audit trail** and can read
any past version. This mirrors the platform's own disposition principle ("nothing
deleted, superseded via new versions") and its signed, immutable-forward git history.

## The model
One append-only table; each operation inserts a new row:

| Op | Effect |
|---|---|
| `create` | version 1 (rejects a live duplicate) |
| `read` | the current payload = highest-version row, unless it's a tombstone |
| `update` | version n+1 (merge keys into, or replace, the payload) |
| `delete` | a **soft tombstone** (op=`delete`) — history preserved |
| `restore` | a new live version from the last non-deleted state |
| `history` | every version, oldest first — the audit trail |
| `read_version(v)` | the payload as it was at version *v* (time-travel) |
| `list` | all live records of a type |

Generic over an entity `type` + string `id` with a JSON `payload`. The demo entity is a
**watchlist** (`{"tickers": [...], "note": "..."}`), but any record works.

## Full lifecycle (CLI)
```bash
python vcrud.py create  watchlist momentum --set tickers=NVDA,MSFT,AAPL note="momo"
python vcrud.py update  watchlist momentum --add tickers=TSLA --remove tickers=AAPL
python vcrud.py read    watchlist momentum          # -> {tickers:[NVDA,MSFT,TSLA], note:momo}
python vcrud.py history watchlist momentum          # v1 create, v2/v3 update …
python vcrud.py delete  watchlist momentum          # soft tombstone
python vcrud.py restore watchlist momentum          # brings it back (new version)
python vcrud.py version watchlist momentum --v 1    # time-travel: original tickers
python vcrud.py list    watchlist
```

## REST surface (over the serving layer)
[`serve.py`](serve.py) exposes the watchlist CRUD as HTTP (FastAPI) — the platform's
**write** surface, alongside the read-only warehouse endpoints:

| Method | Route | Action |
|---|---|---|
| `GET` | `/watchlists` | list live watchlists |
| `POST` | `/watchlists/{name}` | create (409 if it exists) |
| `GET` | `/watchlists/{name}` | read current (404 if absent/deleted) |
| `PUT` | `/watchlists/{name}` | update: `{add:[…], remove:[…], note:…}` |
| `DELETE` | `/watchlists/{name}` | soft-delete |
| `GET` | `/watchlists/{name}/history` | the version history |

```bash
pip install fastapi uvicorn duckdb && uvicorn serve:app
curl -X POST localhost:8000/watchlists/momentum -d '{"tickers":["NVDA","MSFT"]}'
curl -X PUT  localhost:8000/watchlists/momentum -d '{"add":["TSLA"],"remove":["MSFT"]}'
curl localhost:8000/watchlists/momentum/history
```

## Design notes
- **Pure functions on a `sqlite3.Connection`** (`create`/`read`/`update`/`delete`/
  `restore`/`history`/`read_version`/`list_ids`) — so tests run on an in-memory DB and
  the store is reusable outside the CLI/REST.
- **Append-only** — no `UPDATE`/`DELETE` SQL is ever issued; the store is auditable and
  reproducible, matching the platform's governance.
- `DELETE` journal mode (not WAL) per the platform's macOS-SQLite note; `vcrud.db` is
  gitignored (user data, not source).

Covered by [`tests/`](tests/test_core.py) — the full lifecycle (create → duplicate
rejection → versioned update → time-travel → soft delete → not-listed → restore → list-
field union/remove) is unit-tested on `:memory:`.
