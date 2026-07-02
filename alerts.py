#!/usr/bin/env python3
"""
alerts.py
---------
The platform regenerates rankings every run but never told you *what changed*.
This snapshots a result set (default: the GGG Strong-Performer list from
dvm_composite.db) each day and diffs today's set against the most recent prior
snapshot, emitting the new entrants and drop-outs as JSON — the raw material for
a daily "what's new" mailer. Snapshots live under alerts_state/ keyed by date, so
this is the consumption-side twin of the warehouse's partition-incremental
refresh (F9.1).

Pure set-diff core (unit-testable). Optional email delivery reads SMTP settings
from environment variables only — no credentials are ever stored in the repo; if
they're unset, it just writes the JSON and prints a summary.

Usage:
  python alerts.py --snapshot                 # snapshot today + diff vs prior
  python alerts.py --snapshot --email you@example.com
  python alerts.py --history                  # list stored snapshots
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import smtplib
import sqlite3
import sys
from email.mime.text import MIMEText

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "alerts_state")


# ── pure diff core ────────────────────────────────────────────────────────────
def diff_sets(prev: set, curr: set) -> dict:
    """New entrants, drop-outs and retained members between two snapshots."""
    prev, curr = set(prev), set(curr)
    return {"new": sorted(curr - prev), "dropped": sorted(prev - curr),
            "retained": sorted(curr & prev),
            "n_new": len(curr - prev), "n_dropped": len(prev - curr),
            "n_total": len(curr)}


def format_alert(diff: dict, label: str, date: str) -> str:
    lines = [f"[{date}] {label}: {diff['n_total']} names "
             f"(+{diff['n_new']} new, -{diff['n_dropped']} dropped)"]
    if diff["new"]:
        lines.append("  NEW:     " + ", ".join(diff["new"][:40]))
    if diff["dropped"]:
        lines.append("  DROPPED: " + ", ".join(diff["dropped"][:40]))
    return "\n".join(lines)


# ── snapshot storage ──────────────────────────────────────────────────────────
def _snapshot_path(label: str, date: str) -> str:
    os.makedirs(STATE, exist_ok=True)
    return os.path.join(STATE, f"{label}_{date}.json")


def save_snapshot(label: str, members: set, date: str) -> str:
    path = _snapshot_path(label, date)
    json.dump(sorted(members), open(path, "w"))
    return path


def latest_prior(label: str, before: str) -> set | None:
    files = sorted(glob.glob(os.path.join(STATE, f"{label}_*.json")))
    files = [f for f in files if os.path.basename(f) < f"{label}_{before}.json"]
    if not files:
        return None
    return set(json.load(open(files[-1])))


# ── result-set source ─────────────────────────────────────────────────────────
def ggg_members(db: str) -> set:
    con = sqlite3.connect(db)
    try:
        rows = con.execute(
            "SELECT market||':'||ticker FROM dvm_composite WHERE code='GGG'").fetchall()
    finally:
        con.close()
    return {r[0] for r in rows}


def send_email(to_addr: str, subject: str, body: str) -> bool:
    """Send via SMTP using env vars only. Returns False (and is a no-op) if unset."""
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    pwd = os.environ.get("SMTP_PASS")
    if not (host and user and pwd):
        print("  [email] SMTP_HOST/SMTP_USER/SMTP_PASS not set — skipping send",
              file=sys.stderr)
        return False
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587"))) as s:
        s.starttls()
        s.login(user, pwd)
        s.sendmail(user, [to_addr], msg.as_string())
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", action="store_true", help="snapshot today and diff")
    ap.add_argument("--history", action="store_true", help="list stored snapshots")
    ap.add_argument("--label", default="ggg_global")
    ap.add_argument("--db", default=os.path.join(HERE, "dvm_composite.db"))
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--email", default=None)
    args = ap.parse_args()

    if args.history:
        for f in sorted(glob.glob(os.path.join(STATE, "*.json"))):
            n = len(json.load(open(f)))
            print(f"  {os.path.basename(f):40} {n:>5} names")
        return

    if not args.snapshot:
        ap.print_help(); return
    if not os.path.exists(args.db):
        raise SystemExit("no dvm_composite.db — run dvm_composite.py first")

    curr = ggg_members(args.db)
    prior = latest_prior(args.label, args.date)
    path = save_snapshot(args.label, curr, args.date)
    print(f"snapshot: {len(curr)} names -> {path}", file=sys.stderr)

    if prior is None:
        print("  (no prior snapshot — baseline stored, nothing to diff yet)")
        return
    diff = diff_sets(prior, curr)
    body = format_alert(diff, args.label, args.date)
    json.dump(diff, open(os.path.join(HERE, f"alert_{args.label}_{args.date}.json"), "w"), indent=2)
    print("\n" + body)
    if args.email:
        ok = send_email(args.email, f"[{args.label}] {diff['n_new']} new / "
                        f"{diff['n_dropped']} dropped ({args.date})", body)
        print(f"  email {'sent' if ok else 'skipped'}", file=sys.stderr)


if __name__ == "__main__":
    main()
