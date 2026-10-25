"""serve.validate_predicate is the SQL-injection guard for GET /filter: the
predicate is interpolated into a DuckDB WHERE clause. Pin what it lets through,
and prove it cannot be stalled by a crafted input (catastrophic regex backtracking)."""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import serve  # noqa: E402


@pytest.mark.parametrize("ok", [
    "roe>15", "roe >= 15 and de < 1", "(pe<20 or pb<2) AND composite>0.5", "mktcap>1000000",
    "roa != 0", "D>=0.5 or V>=0.5",
])
def test_allowed_predicates_pass_unchanged(ok):
    assert serve.validate_predicate(ok) == ok


@pytest.mark.parametrize("bad", [
    "roe>15; DROP TABLE ohlc", "roe>15 -- x", "roe>15 /* x */", "ticker='AAPL'",
    "roe>15 union select 1", "roe>(select 1)", "roe>15 or 1=1 or name>0", "roe>1e9",
    "roe>15,", "roe+1>15", "roe>'15'",
])
def test_injection_and_unknown_tokens_rejected(bad):
    with pytest.raises(ValueError):
        serve.validate_predicate(bad)


def test_rejection_is_fast_on_long_and_chains():
    """Prove-it: the shape regex ^[...]+(and|or|[...])*$ backtracks exponentially
    in the number of 'and's before a disallowed character; 26 'and's took minutes
    (measured 17 s at 22, doubling per 'and'), freezing the API worker."""
    code = ("import sys; sys.path.insert(0, %r); import serve\n"
            "try:\n    serve.validate_predicate('roe>1 ' + 'and ' * 26 + '+')\n"
            "except ValueError:\n    pass\n") % ROOT
    subprocess.run([sys.executable, "-c", code], timeout=10, check=True)
