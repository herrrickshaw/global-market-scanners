#!/usr/bin/env python3
"""
vcrud.py
--------
A **versioned CRUD** store (vCRUD): Create / Read / Update / Delete over SQLite where
every write is *versioned* and the log is *append-only* — nothing is ever mutated or
physically removed, so you get a full audit trail and can read any past version. This
mirrors the platform's own disposition principle ("nothing deleted, superseded via new
versions") and its signed, immutable-forward git history.

Model: one append-only table; each operation inserts a new row.
  create  -> version 1
  update  -> version n+1 (merge or replace the payload)
  delete  -> a soft tombstone (op='delete'); history is preserved
  restore -> a new live version from the last non-deleted state
The "current" state of a record is simply its highest-version row; it is live iff that
row isn't a tombstone.

Generic over an entity `type` + string `id` with a JSON payload — the demo entity is a
watchlist (`{"tickers": [...], "note": "..."}`), but any record works.

Pure functions operate on a sqlite3 connection (so tests run on an in-memory DB); the
CLI opens a file DB.

Usage:
  python vcrud.py create watchlist momentum --set tickers=NVDA,MSFT,AAPL
  python vcrud.py update watchlist momentum --add tickers=TSLA --remove tickers=AAPL
  python vcrud.py read   watchlist momentum
  python vcrud.py history watchlist momentum
  python vcrud.py delete watchlist momentum      # soft; restore brings it back
  python vcrud.py list   watchlist
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "vcrud.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS vcrud (
    seq     INTEGER PRIMARY KEY AUTOINCREMENT,
    type    TEXT NOT NULL,
    id      TEXT NOT NULL,
    version INTEGER NOT NULL,
    op      TEXT NOT NULL,            -- create | update | delete | restore
    payload TEXT,                     -- JSON
    ts      TEXT NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_vcrud_entity ON vcrud(type, id, version);
"""


# ── connection / helpers ──────────────────────────────────────────────────────
def connect(path: str = DB) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=DELETE;")            # avoid WAL (macOS Downloads I/O quirks)
    con.row_factory = sqlite3.Row
    init(con)
    return con


def init(con: sqlite3.Connection):
    con.executescript(SCHEMA)
    con.commit()


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _latest_row(con, type_: str, id_: str):
    return con.execute(
        "SELECT * FROM vcrud WHERE type=? AND id=? ORDER BY version DESC LIMIT 1",
        (type_, id_)).fetchone()


def _record(row) -> dict:
    return {"type": row["type"], "id": row["id"], "version": row["version"],
            "op": row["op"], "ts": row["ts"], "deleted": bool(row["deleted"]),
            "payload": json.loads(row["payload"]) if row["payload"] else None}


def _append(con, type_, id_, version, op, payload, deleted=0) -> dict:
    con.execute("INSERT INTO vcrud(type,id,version,op,payload,ts,deleted) VALUES (?,?,?,?,?,?,?)",
                (type_, id_, version, op, json.dumps(payload) if payload is not None else None,
                 _now(), deleted))
    con.commit()
    return _record(_latest_row(con, type_, id_))


# ── CRUD (versioned) ──────────────────────────────────────────────────────────
def create(con, type_: str, id_: str, payload: dict) -> dict:
    """Create a new record (version 1). Raises if a LIVE record already exists."""
    last = _latest_row(con, type_, id_)
    if last is not None and not last["deleted"]:
        raise ValueError(f"{type_}/{id_} already exists (version {last['version']})")
    version = (last["version"] + 1) if last is not None else 1     # re-create after delete bumps version
    return _append(con, type_, id_, version, "create", dict(payload))


def read(con, type_: str, id_: str) -> dict | None:
    """Current payload (latest version), or None if absent or deleted."""
    row = _latest_row(con, type_, id_)
    if row is None or row["deleted"]:
        return None
    return _record(row)["payload"]


def update(con, type_: str, id_: str, payload: dict, merge: bool = True) -> dict:
    """Append a new version. merge=True updates keys into the current payload;
    merge=False replaces it wholesale. Raises if the record isn't live."""
    row = _latest_row(con, type_, id_)
    if row is None or row["deleted"]:
        raise KeyError(f"{type_}/{id_} does not exist (or is deleted)")
    cur = _record(row)["payload"] or {}
    new = {**cur, **payload} if merge else dict(payload)
    return _append(con, type_, id_, row["version"] + 1, "update", new)


def delete(con, type_: str, id_: str) -> bool:
    """Soft-delete: append a tombstone version (history preserved). Returns False if
    there was nothing live to delete."""
    row = _latest_row(con, type_, id_)
    if row is None or row["deleted"]:
        return False
    _append(con, type_, id_, row["version"] + 1, "delete",
            _record(row)["payload"], deleted=1)
    return True


def restore(con, type_: str, id_: str) -> dict:
    """Undo a delete: append a live version from the last non-deleted state."""
    row = _latest_row(con, type_, id_)
    if row is None or not row["deleted"]:
        raise KeyError(f"{type_}/{id_} is not deleted")
    prev = con.execute("SELECT * FROM vcrud WHERE type=? AND id=? AND deleted=0 "
                       "ORDER BY version DESC LIMIT 1", (type_, id_)).fetchone()
    payload = _record(prev)["payload"] if prev else {}
    return _append(con, type_, id_, row["version"] + 1, "restore", payload)


def history(con, type_: str, id_: str) -> list:
    """Every version of a record, oldest first — the audit trail."""
    rows = con.execute("SELECT * FROM vcrud WHERE type=? AND id=? ORDER BY version",
                       (type_, id_)).fetchall()
    return [_record(r) for r in rows]


def read_version(con, type_: str, id_: str, version: int) -> dict | None:
    """The payload as it was at a specific version (time-travel)."""
    row = con.execute("SELECT * FROM vcrud WHERE type=? AND id=? AND version=?",
                      (type_, id_, version)).fetchone()
    return _record(row)["payload"] if row else None


def list_ids(con, type_: str) -> list:
    """Ids of all LIVE records of a type (with their current version)."""
    rows = con.execute("""
        SELECT v.id, v.version FROM vcrud v
        JOIN (SELECT id, MAX(version) mv FROM vcrud WHERE type=? GROUP BY id) m
          ON v.id=m.id AND v.version=m.mv
        WHERE v.type=? AND v.deleted=0 ORDER BY v.id""", (type_, type_)).fetchall()
    return [{"id": r["id"], "version": r["version"]} for r in rows]


# ── list-field helpers (for watchlist tickers etc.) ───────────────────────────
def add_to_list(con, type_, id_, field, values) -> dict:
    """Union `values` into a list-valued field of a record's payload (a new version)."""
    cur = read(con, type_, id_) or {}
    have = list(cur.get(field, []))
    merged = have + [v for v in values if v not in have]
    return update(con, type_, id_, {field: merged})


def remove_from_list(con, type_, id_, field, values) -> dict:
    cur = read(con, type_, id_) or {}
    drop = set(values)
    return update(con, type_, id_, {field: [v for v in cur.get(field, []) if v not in drop]})


# ── CLI ───────────────────────────────────────────────────────────────────────
def _parse_kv(pairs):
    """['tickers=A,B', 'note=hi'] -> {'tickers': ['A','B'], 'note': 'hi'}."""
    out = {}
    for p in pairs or []:
        k, _, v = p.partition("=")
        out[k] = [x.strip().upper() for x in v.split(",") if x.strip()] if "," in v or k == "tickers" else v
    return out


def main():
    ap = argparse.ArgumentParser(description="versioned CRUD store")
    ap.add_argument("op", choices=["create", "read", "update", "delete", "restore",
                                   "history", "version", "list"])
    ap.add_argument("type", help="entity type, e.g. watchlist")
    ap.add_argument("id", nargs="?", help="record id (not needed for 'list')")
    ap.add_argument("--set", nargs="*", help="field=value (value CSV -> list)")
    ap.add_argument("--add", nargs="*", help="field=CSV to union into a list field")
    ap.add_argument("--remove", nargs="*", help="field=CSV to remove from a list field")
    ap.add_argument("--v", type=int, help="version for 'version' op")
    ap.add_argument("--db", default=DB)
    args = ap.parse_args()
    con = connect(args.db)

    try:
        if args.op == "list":
            for r in list_ids(con, args.type):
                print(f"  {r['id']:24} v{r['version']}")
        elif args.op == "create":
            r = create(con, args.type, args.id, _parse_kv(args.set))
            print(f"created {args.type}/{args.id} v{r['version']}: {r['payload']}")
        elif args.op == "read":
            p = read(con, args.type, args.id)
            print(json.dumps(p, indent=2) if p is not None else "(absent or deleted)")
        elif args.op == "update":
            for kv in (_parse_kv(args.set).items() if args.set else []):
                update(con, args.type, args.id, {kv[0]: kv[1]})
            for f, vals in _parse_kv(args.add).items():
                add_to_list(con, args.type, args.id, f, vals if isinstance(vals, list) else [vals])
            for f, vals in _parse_kv(args.remove).items():
                remove_from_list(con, args.type, args.id, f, vals if isinstance(vals, list) else [vals])
            print(f"updated {args.type}/{args.id}: {read(con, args.type, args.id)}")
        elif args.op == "delete":
            print("deleted (soft)" if delete(con, args.type, args.id) else "nothing live to delete")
        elif args.op == "restore":
            r = restore(con, args.type, args.id); print(f"restored v{r['version']}: {r['payload']}")
        elif args.op == "history":
            for h in history(con, args.type, args.id):
                tomb = " [deleted]" if h["deleted"] else ""
                print(f"  v{h['version']:<3} {h['op']:8} {h['ts']}{tomb}  {h['payload']}")
        elif args.op == "version":
            print(json.dumps(read_version(con, args.type, args.id, args.v), indent=2))
    except (ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr); sys.exit(1)
    finally:
        con.close()


if __name__ == "__main__":
    main()
