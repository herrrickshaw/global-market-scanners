#!/usr/bin/env python3
"""Import every importable module in this repo, offline, and fail on definite bugs.

Shared CI step (rolled out 2026-10-26). Rules:
- Only modules inside a package (directory has __init__.py) or scripts guarded by
  `if __name__ == "__main__"` are imported, so top-level script bodies never run.
- Files that mail, trade, delete, schedule, or talk to a DB/cloud are skipped.
- Sockets are blocked, so nothing reaches the network; MPLBACKEND=Agg.
- Each import runs in its own interpreter with a 20 s timeout.

Exit 1 only for definite bugs: SyntaxError, NameError, or an ImportError /
ModuleNotFoundError that names a module *inside this repo*. A third-party module
missing from the CI environment, a data file that is not checked in, a blocked
socket, or a missing env var is reported as a warning, not a failure.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = os.getcwd()
SKIP_DIRS = re.compile(r"^(tests?/|from-working-files/|from-[^/]+/|\.venv/|venv/|node_modules/|build/|dist/|.*/migrations/)")
SKIP_FILES = re.compile(r"(^|/)(test_[^/]*|[^/]*_test|conftest|setup)\.py$")
DANGEROUS = re.compile(
    r"smtplib|yagmail|sendmail|cassandra|psycopg|pymongo|boto3|rclone|kiteconnect|upstox|smartapi"
    r"|shutil\.rmtree|os\.remove|os\.unlink|\.unlink\(|subprocess|os\.system|requests\.(post|put|delete)"
    r"|selenium|playwright|schedule\.|apscheduler|launchctl|crontab",
    re.I,
)
MAIN_GUARD = re.compile(r"__name__\s*==\s*[\"']__main__[\"']")

RUNNER = r"""
import importlib, os, socket, sys
def _blocked(*a, **k): raise OSError("network blocked by import_all")
socket.socket.connect = _blocked; socket.socket.connect_ex = _blocked
socket.create_connection = _blocked; socket.getaddrinfo = _blocked
os.environ.setdefault("MPLBACKEND", "Agg")
sys.path.insert(0, os.getcwd())
try:
    importlib.import_module(sys.argv[1])
except SystemExit:
    pass
except BaseException as e:  # noqa: BLE001 - we classify by type name below
    print(type(e).__name__ + ": " + str(e)[:300].replace("\n", " "))
    sys.exit(2)
"""


def tracked_py_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "*.py"], capture_output=True, text=True, check=True).stdout
    return [f for f in out.split("\n") if f]


def candidate_modules(files: list[str]) -> list[tuple[str, str]]:
    mods = []
    for f in files:
        if SKIP_DIRS.match(f) or SKIP_FILES.search(f):
            continue
        d = os.path.dirname(f)
        in_pkg = os.path.exists(os.path.join(d, "__init__.py")) if d else False
        try:
            src = open(f, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if not (in_pkg or MAIN_GUARD.search(src)):
            continue
        if DANGEROUS.search(src):
            continue
        m = f[:-3].replace("/", ".")
        if m.endswith(".__init__"):
            m = m[: -len(".__init__")]
        mods.append((f, m))
    return mods[:400]


def local_top_levels(files: list[str]) -> set[str]:
    tops = set()
    for f in files:
        tops.add(f.split("/")[0].removesuffix(".py"))
    return tops


def classify(msg: str, local: set[str]) -> str:
    kind, _, rest = msg.partition(":")
    if kind in ("SyntaxError", "NameError", "IndentationError", "TabError"):
        return "bug"
    if kind in ("ModuleNotFoundError", "ImportError"):
        m = re.search(r"No module named '([^'.]+)", rest) or re.search(r"cannot import name '[^']+' from '([^'.]+)", rest)
        if m and m.group(1) in local:
            return "bug"
        return "dep"
    return "warn"


def main() -> int:
    files = tracked_py_files()
    local = local_top_levels(files)
    mods = candidate_modules(files)
    rows: list[tuple[str, str, str]] = []
    for f, m in mods:
        try:
            p = subprocess.run([sys.executable, "-I", "-c", RUNNER, m], capture_output=True, text=True, timeout=20)
            msg = (p.stdout + p.stderr).strip().splitlines()[-1] if (p.stdout + p.stderr).strip() else ""
            if p.returncode == 0 or not msg:
                continue
            rows.append((classify(msg, local), f, msg))
        except subprocess.TimeoutExpired:
            rows.append(("warn", f, "Timeout: import took longer than 20 s"))
    bugs = [r for r in rows if r[0] == "bug"]
    deps = [r for r in rows if r[0] == "dep"]
    warns = [r for r in rows if r[0] == "warn"]
    lines = [f"### import-all: {len(mods)} modules, {len(bugs)} bugs, {len(deps)} missing deps, {len(warns)} warnings", ""]
    if rows:
        lines += ["| class | module | error |", "|---|---|---|"]
        for k, f, msg in sorted(rows):
            cell = msg.replace("|", "\\|")  # kept out of the f-string: a backslash there is a SyntaxError before 3.12
            lines.append(f"| {k} | `{f}` | {cell} |")
    report = "\n".join(lines)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report + "\n")
    for _, f, msg in bugs:
        print(f"::error file={f}::{msg}")
    for _, f, msg in deps + warns:
        print(f"::warning file={f}::{msg}")
    return 1 if bugs else 0


if __name__ == "__main__":
    sys.exit(main())
