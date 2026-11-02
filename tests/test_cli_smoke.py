"""Static smoke checks: scanner CLIs import offline and screeners reference no undefined names."""
from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCREENERS = ["accumulation_screener", "corporate_actions", "earnings_liquidity",
             "liquidity_factor", "pead_factor"]


@pytest.mark.parametrize("name", SCREENERS)
def test_screener_help_runs(name):
    out = subprocess.run([sys.executable, str(ROOT / f"{name}.py"), "--help"],
                         capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert out.returncode == 0 and "usage" in out.stdout.lower()


@pytest.mark.parametrize("name", SCREENERS)
def test_screener_main_imports_marketdata(name):
    """Regression: main() used `marketdata.market_list()` without importing marketdata."""
    src = (ROOT / f"{name}.py").read_text()
    idx = src.index("marketdata.market_list()")
    assert "import marketdata" in src[max(0, idx - 200):idx]


@pytest.mark.parametrize("market", ["us", "indian", "japan", "korea", "european"])
def test_scanner_modules_import_offline(market):
    mod = importlib.import_module(f"full_{market}_market_scan")
    assert callable(mod.compute_darvas_box)
