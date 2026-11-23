"""warehouse.py: DuckDB analytical layer, exercised against a recording fake duckdb."""
import importlib
import os
import sys
import types

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeResult:
    def __init__(self, rows=None, df=None):
        self._rows = rows or []
        self._df = df if df is not None else pd.DataFrame({"x": [1]})

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0]

    def df(self):
        return self._df


class FakeCon:
    def __init__(self, path, fail_views=()):
        self.path = path
        self.sql = []
        self.closed = False
        self.fail_views = fail_views
        self.tables = [("ohlc",), ("companies",), ("fundamentals",)]

    def execute(self, q, *a):
        self.sql.append(q)
        if any(f"VIEW {v} " in q for v in self.fail_views):
            raise RuntimeError("catalog error")
        if q.strip() == "SHOW TABLES":
            return FakeResult(self.tables)
        if q.startswith("SELECT count(*) FROM"):
            name = q.split()[-1]
            if name == "companies":
                raise RuntimeError("no")
            return FakeResult([(12345,)])
        return FakeResult()

    def close(self):
        self.closed = True


@pytest.fixture
def wh(monkeypatch, tmp_path):
    fake = types.ModuleType("duckdb")
    fake.connect = lambda path: FakeCon(path)
    monkeypatch.setitem(sys.modules, "duckdb", fake)
    monkeypatch.delitem(sys.modules, "warehouse", raising=False)
    mod = importlib.import_module("warehouse")
    monkeypatch.setattr(mod, "HERE", str(tmp_path))
    monkeypatch.setattr(mod, "SEED", str(tmp_path / "seed"))
    yield mod
    sys.modules.pop("warehouse", None)


def test_build_with_no_optional_sources(wh, capsys):
    con = FakeCon("x")
    wh.build(con)
    assert con.sql[0] == "INSTALL sqlite; LOAD sqlite;"
    ohlc = con.sql[1]
    assert "CREATE OR REPLACE VIEW ohlc" in ohlc and "cleaned_long_*.parquet" in ohlc
    assert not any("VIEW companies" in q for q in con.sql)
    err = capsys.readouterr().err
    for fn in ("fundamentals_cache.db", "dvm_global.db", "dvm_composite.db", "viability_summary.db"):
        assert f"[skip] {fn} not present" in err
    assert "warehouse built: ['ohlc', 'companies', 'fundamentals']" in err


def test_build_attaches_existing_sources(wh, tmp_path):
    (tmp_path / "companies_industry.parquet").write_bytes(b"x")
    for fn in ("fundamentals_cache.db", "dvm_global.db", "dvm_composite.db", "viability_summary.db"):
        (tmp_path / fn).write_bytes(b"x")
    con = FakeCon("x")
    wh.build(con)
    joined = "\n".join(con.sql)
    assert "CREATE OR REPLACE VIEW companies AS SELECT * FROM read_parquet(" in joined
    assert f"ATTACH IF NOT EXISTS '{tmp_path}/fundamentals_cache.db' AS fund_db (TYPE sqlite)" in joined
    assert "CREATE OR REPLACE VIEW fundamentals AS SELECT ticker, market," in joined
    assert "TRY_CAST(roe AS DOUBLE) roe" in joined and "FROM fund_db.fund" in joined
    assert "CREATE OR REPLACE VIEW dvm_global AS SELECT * FROM dvmg_db.dvm_global" in joined
    assert "CREATE OR REPLACE VIEW dvm_composite AS SELECT * FROM dvmc_db.dvm_composite" in joined
    assert "CREATE OR REPLACE VIEW viability AS SELECT * FROM viab_db.market_screen_summary" in joined


def test_build_view_failure_is_skipped_not_fatal(wh, tmp_path, capsys):
    (tmp_path / "dvm_global.db").write_bytes(b"x")
    con = FakeCon("x", fail_views=("dvm_global",))
    wh.build(con)
    assert "[skip] dvm_global: catalog error" in capsys.readouterr().err


def test_shows_sql_is_defined_for_all_choices(wh):
    assert {"markets", "ggg_global", "dvm_dist", "momentum_by_market", "high_roe_low_de"} <= set(wh.SHOWS)
    assert "code='GGG'" in wh.SHOWS["ggg_global"]


def test_refresh_ohlc_partition_appends_only_new(wh, tmp_path, monkeypatch):
    os.makedirs(wh.SEED)
    base_path = os.path.join(wh.SEED, "cleaned_long_US.parquet")
    open(base_path, "wb").write(b"x")
    base = pd.DataFrame({"Date": pd.to_datetime(["2026-01-01", "2026-01-02"]), "Symbol": ["A", "A"], "Close": [1.0, 2.0]})
    new = pd.DataFrame({"Date": pd.to_datetime(["2026-01-02", "2026-01-03"]), "Symbol": ["A", "A"], "Close": [9.0, 3.0]})
    saved = {}
    monkeypatch.setattr(wh.pd if hasattr(wh, "pd") else pd, "read_parquet",
                        lambda p: base if p == base_path else new)
    monkeypatch.setattr(pd.DataFrame, "to_parquet",
                        lambda self, p, **kw: saved.update(p=p, df=self, kw=kw))
    r = wh.refresh_ohlc_partition("US", "/new.parquet")
    assert r == {"market": "US", "rows_before": 2, "rows_added": 1, "rows_after": 3}
    assert saved["p"] == base_path and saved["kw"] == {"index": False, "compression": "snappy"}
    assert list(saved["df"]["Close"]) == [1.0, 2.0, 3.0]       # existing date not overwritten


def test_refresh_ohlc_partition_without_base(wh, monkeypatch):
    new = pd.DataFrame({"Date": pd.to_datetime(["2026-01-02"]), "Symbol": ["A"], "Close": [3.0]})
    monkeypatch.setattr(pd, "read_parquet", lambda p: new)
    monkeypatch.setattr(pd.DataFrame, "to_parquet", lambda self, p, **kw: None)
    r = wh.refresh_ohlc_partition("EU", "/n.parquet")
    assert r == {"market": "EU", "rows_before": 0, "rows_added": 1, "rows_after": 1}


def _main(wh, monkeypatch, *argv, con=None):
    con = con or FakeCon("x")
    monkeypatch.setattr(wh.duckdb, "connect", lambda p: con)
    monkeypatch.setattr(sys, "argv", ["warehouse.py", *argv])
    wh.main()
    return con


def test_main_refresh_short_circuits(wh, monkeypatch, capsys):
    monkeypatch.setattr(wh, "refresh_ohlc_partition", lambda m, p: {"market": m, "p": p})
    called = []
    monkeypatch.setattr(wh.duckdb, "connect", lambda p: called.append(p))
    monkeypatch.setattr(sys, "argv", ["warehouse.py", "--refresh-ohlc", "US", "/n.parquet"])
    wh.main()
    assert called == [] and "'market': 'US'" in capsys.readouterr().out


def test_main_sql(wh, monkeypatch, capsys):
    con = _main(wh, monkeypatch, "--sql", "SELECT 1")
    assert "SET max_expression_depth=10000" in con.sql and "SELECT 1" in con.sql
    assert con.closed
    assert capsys.readouterr().out.split() == ["x", "1"]      # FakeResult frame, no index


def test_main_show(wh, monkeypatch):
    con = _main(wh, monkeypatch, "--show", "markets")
    assert wh.SHOWS["markets"] in con.sql


def test_main_filter_builds_join(wh, monkeypatch):
    con = _main(wh, monkeypatch, "--filter", "roe>15 and M>=70")
    q = con.sql[-1]
    assert "FROM dvm_composite c LEFT JOIN fundamentals f ON c.ticker=f.ticker" in q
    assert "WHERE roe>15 and M>=70 ORDER BY c.composite DESC LIMIT 30" in q


def test_main_default_lists_tables_with_counts(wh, monkeypatch, capsys):
    _main(wh, monkeypatch)
    out = capsys.readouterr().out
    assert "=== warehouse tables ===" in out
    assert "ohlc" in out and "12,345 rows" in out
    assert "companies        (view)" in out
