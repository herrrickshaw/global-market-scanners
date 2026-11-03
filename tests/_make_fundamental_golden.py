"""Regenerate golden_fundamental.json from the CURRENT code (run once, before refactoring)."""
import importlib, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _fundamental_fixtures as fx
from test_fundamental_scan import CALLS, run_all

json.dump(run_all(), open(Path(__file__).parent / "golden_fundamental.json", "w"), indent=0, default=str)
