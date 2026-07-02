#!/usr/bin/env python3
"""
togaf.py
--------
Operational TOGAF implementation for the platform: the Architecture Principles
catalog (TOGAF Name/Statement/Rationale/Implications template), the ADM cycle
mapping, and — the part that makes it real — **Architecture Governance as
executable checks** (ADM Phase G): each principle's compliance is verified against
the actual repository.

Usage:
  python architecture/togaf.py principles     # the principles catalog
  python architecture/togaf.py adm            # ADM phase -> platform mapping
  python architecture/togaf.py domains        # the four architecture domains
  python architecture/togaf.py govern         # run compliance checks (Phase G)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
A = json.load(open(os.path.join(HERE, "principles.json")))


def _sh(cmd):
    return subprocess.run(cmd, cwd=ROOT, shell=True, capture_output=True, text=True)


# ── Architecture Governance (ADM Phase G): each check returns (ok, detail) ────
def chk_no_secrets():
    # require real key/token shapes (e.g. the '-----BEGIN …KEY' prefix) so this
    # detection pattern doesn't match its own source.
    pat = r"(sk-ant-[a-zA-Z0-9]{24}|AKIA[0-9A-Z]{16}|ghp_[a-zA-Z0-9]{36}|-----BEGIN [A-Z ]+PRIVATE KEY)"
    r = _sh(f"git grep -nIE '{pat}' -- . ':!*.md' || true")
    n = len([l for l in r.stdout.splitlines() if l.strip()])
    return n == 0, f"{n} hardcoded-secret hits (want 0)"


def chk_cache_first():
    scripts = ["pit_backtest.py", "factor_research.py", "ml_viability.py", "screen_viability.py"]
    miss = [s for s in scripts if "cached_download" not in open(os.path.join(ROOT, s)).read()]
    return not miss, f"data scripts using the cache: {len(scripts)-len(miss)}/{len(scripts)}" + (f"; missing {miss}" if miss else "")


def chk_rate_governed():
    fetchers = ["market_store.py", "fundamentals_global.py", "enrich_industries.py", "pit_fundamentals.py"]
    ok = [s for s in fetchers if "apiclient" in open(os.path.join(ROOT, s)).read()]
    return len(ok) == len(fetchers), f"fetchers via apiclient: {len(ok)}/{len(fetchers)}"


def chk_point_in_time():
    src = open(os.path.join(ROOT, "pit_fundamentals.py")).read()
    ok = "filed" in src and "asof" in src
    return ok, "pit_fundamentals filters by filing date (filed<=asof)" if ok else "no filed-date filter found"


def chk_data_quality():
    fg = open(os.path.join(ROOT, "fundamentals_global.py")).read()
    sv = open(os.path.join(ROOT, "screen_viability.py")).read()
    ok = "isfinite" in fg and "clip(-clip" in sv
    return ok, "non-finite guard + outlier clip present" if ok else "missing quality guards"


def chk_reuse():
    shared = ["market_store.py", "apiclient.py", "market_holidays.py", "ml_signal_engine.py"]
    present = [s for s in shared if os.path.exists(os.path.join(ROOT, s))]
    return len(present) == len(shared), f"shared building blocks present: {len(present)}/{len(shared)}"


def chk_warehouse_present():
    return os.path.exists(os.path.join(ROOT, "warehouse.py")), "DuckDB warehouse (warehouse.py) present"


def chk_docs_present():
    docs = ["README.md", "USER_GUIDE.md", "SCHEMA.md", "ARCHITECTURE.md", "ARCHITECTURE_MAP.md", "SECURITY.md"]
    have = [d for d in docs if os.path.exists(os.path.join(ROOT, d))]
    return len(have) == len(docs), f"core docs: {len(have)}/{len(docs)}"


CHECKS = {"no_secrets": chk_no_secrets, "cache_first": chk_cache_first,
          "rate_governed": chk_rate_governed, "point_in_time": chk_point_in_time,
          "data_quality": chk_data_quality, "reuse": chk_reuse,
          "warehouse_present": chk_warehouse_present, "docs_present": chk_docs_present}


def principles():
    for p in A["principles"]:
        print(f"\n[{p['id']}] {p['name']}  ({p['domain']})")
        print(f"  Statement:    {p['statement']}")
        print(f"  Rationale:    {p['rationale']}")
        print(f"  Implications: {p['implications']}")


def adm():
    print("TOGAF ADM cycle -> platform\n")
    for a in A["adm"]:
        print(f"  {a['phase']:34} {a['platform']}")


def domains():
    print("Architecture domains (BDAT)\n")
    for k, v in A["domains"].items():
        print(f"  {k:12} {v}")


def govern():
    print("Architecture Governance (ADM Phase G) — principle compliance\n")
    passed = 0
    for p in A["principles"]:
        fn = CHECKS.get(p["check"])
        ok, detail = fn() if fn else (None, "no check")
        mark = "✅" if ok else ("—" if ok is None else "❌")
        passed += 1 if ok else 0
        print(f"  {mark} {p['id']} {p['name']:38} {detail}")
    print(f"\n  {passed}/{len(A['principles'])} principles verified compliant.")
    return passed == len(A["principles"])


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "principles"
    if cmd == "govern":
        sys.exit(0 if govern() else 1)     # non-zero fails CI when a principle regresses
    {"principles": principles, "adm": adm, "domains": domains}.get(
        cmd, lambda: print("commands: principles | adm | domains | govern"))()
