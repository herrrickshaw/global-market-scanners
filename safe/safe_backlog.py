#!/usr/bin/env python3
"""
safe_backlog.py
---------------
A working implementation of the platform's SAFe (Scaled Agile Framework) delivery
structure — Strategic Themes -> Epics -> Features across Program Increments (PIs),
with status/size, KPIs, and a roadmap. Queries `backlog.json`.

This is the "Lean Portfolio Management + ART backlog" made operational: instead of a
static poster, the backlog is data you can filter, roll up, and track.

Usage:
  python safe_backlog.py portfolio        # themes -> epics with status
  python safe_backlog.py pi PI-3          # features planned/delivered in a PI
  python safe_backlog.py roadmap          # epics & features by PI
  python safe_backlog.py burnup           # story-point completion by PI and overall
  python safe_backlog.py kpis             # Measure & Grow KPIs
  python safe_backlog.py backlog          # unstarted (backlog) features
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
B = json.load(open(os.path.join(HERE, "backlog.json")))
EPICS = {e["id"]: e for e in B["epics"]}
THEMES = {t["id"]: t for t in B["strategic_themes"]}
PIS = {p["id"]: p for p in B["pis"]}
_MARK = {"done": "✅", "in_progress": "🔶", "backlog": "⬜"}


def portfolio():
    print(f"ART: {B['art']}\n")
    for t in B["strategic_themes"]:
        print(f"● {t['id']} — {t['name']}")
        for e in [e for e in B["epics"] if e["theme"] == t["id"]]:
            print(f"    {_MARK[e['status']]} {e['id']} {e['name']}  [{e['pi']}]")
        print()


def pi(pid):
    p = PIS.get(pid)
    if not p:
        print("PIs:", ", ".join(PIS)); return
    print(f"{pid} — {p['name']}\n  Objectives:")
    for o in p["objectives"]:
        print(f"    • {o}")
    feats = [f for f in B["features"] if f["pi"] == pid]
    done = sum(f["size"] for f in feats if f["status"] == "done")
    tot = sum(f["size"] for f in feats)
    print(f"\n  Features ({done}/{tot} pts done):")
    for f in feats:
        print(f"    {_MARK[f['status']]} {f['id']} {f['name']}  ({f['size']}) — {f['module']}")


def roadmap():
    for pid, p in PIS.items():
        feats = [f for f in B["features"] if f["pi"] == pid]
        done = sum(1 for f in feats if f["status"] == "done")
        print(f"\n▐ {pid} — {p['name']}  [{done}/{len(feats)} features]")
        for eid in dict.fromkeys(f["epic"] for f in feats):
            e = EPICS[eid]
            print(f"    {_MARK[e['status']]} {eid} {e['name']}")
            for f in [f for f in feats if f["epic"] == eid]:
                print(f"        {_MARK[f['status']]} {f['id']} {f['name']} ({f['size']})")


def burnup():
    by_pi = defaultdict(lambda: [0, 0])
    for f in B["features"]:
        by_pi[f["pi"]][1] += f["size"]
        if f["status"] == "done":
            by_pi[f["pi"]][0] += f["size"]
    print(f"  {'PI':7}{'done':>6}{'total':>7}  progress")
    gd = gt = 0
    for pid in PIS:
        d, t = by_pi[pid]; gd += d; gt += t
        bar = "█" * int(20 * d / t) + "·" * (20 - int(20 * d / t)) if t else ""
        print(f"  {pid:7}{d:>6}{t:>7}  {bar} {100*d//t if t else 0}%")
    print(f"  {'ALL':7}{gd:>6}{gt:>7}  overall {100*gd//gt}% of {gt} story points")


def kpis():
    print("Measure & Grow — KPIs\n")
    for k in B["kpis"]:
        print(f"  {k['metric']:26} {k['value']}")


def backlog():
    print("Backlog (unstarted features):")
    for f in B["features"]:
        if f["status"] != "done":
            print(f"  ⬜ {f['id']} {f['name']}  [{f['pi']}] ({f['size']}) — {f['module']}")


CMDS = {"portfolio": portfolio, "roadmap": roadmap, "burnup": burnup,
        "kpis": kpis, "backlog": backlog}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "portfolio"
    if cmd == "pi":
        pi(sys.argv[2] if len(sys.argv) > 2 else "")
    elif cmd in CMDS:
        CMDS[cmd]()
    else:
        print("commands: portfolio | pi <PI-id> | roadmap | burnup | kpis | backlog")
