"""Five scanner CLIs resolve the market universe with ``marketdata.market_list()``
inside ``main()`` but only ever imported *names* from marketdata (``from marketdata
import wide``), so the bare module name was undefined: running any of them with
``--all`` (or with no ``--market``, the documented default) died with
``NameError: name 'marketdata' is not defined`` before doing any work.

The proof: stub ``market_list`` with a sentinel exception; the CLI must reach it
(sentinel propagates) rather than fail earlier with NameError."""
import importlib
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import marketdata  # noqa: E402


class _Reached(Exception):
    """Raised by the stub so main() stops right after resolving the universe."""


@pytest.mark.parametrize("modname", [
    "accumulation_screener",
    "corporate_actions",
    "earnings_liquidity",
    "liquidity_factor",
    "pead_factor",
])
def test_main_all_reaches_market_list(monkeypatch, modname):
    mod = importlib.import_module(modname)

    def _stub():
        raise _Reached()

    monkeypatch.setattr(marketdata, "market_list", _stub)
    monkeypatch.setattr(sys, "argv", [modname, "--all"])
    with pytest.raises(_Reached):
        mod.main()
