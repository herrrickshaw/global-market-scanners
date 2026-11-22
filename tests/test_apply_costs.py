"""apply_costs.py: net-of-cost viability tables (tabulate/markdown and SQLite faked/tmp)."""
import os
import sqlite3
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apply_costs as ac  # noqa: E402


def _summary(path, rows):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE market_screen_summary(market TEXT, screen TEXT, avg_hit_pct REAL, avg_edge REAL)")
    con.executemany("INSERT INTO market_screen_summary VALUES (?,?,?,?)", rows)
    con.commit()
    con.close()


def test_costs_table_values():
    assert ac.COSTS_PCT == {"India": 0.30, "US": 0.10, "Japan": 0.20, "Korea": 0.25, "Europe": 0.40}


def test_load_missing_returns_none(tmp_path):
    assert ac.load(str(tmp_path / "nope.db")) is None


def test_load_reads_columns(tmp_path):
    p = str(tmp_path / "s.db")
    _summary(p, [("US", "s1", 55.0, 0.5)])
    df = ac.load(p)
    assert list(df.columns) == ["market", "screen", "avg_hit_pct", "avg_edge"] and len(df) == 1


def test_net_table_math_viability_and_order():
    df = pd.DataFrame({"market": ["US", "US", "India", "India", "US"],
                       "screen": ["a", "b", "c", "d", "e"],
                       "avg_hit_pct": [60.0, 60.0, 51.0, 50.0, 49.0],
                       "avg_edge": [0.5, 0.10, 0.31, 0.9, 5.0]})
    out = ac.net_table(df)
    assert list(out.columns) == ["market", "screen", "gross_edge", "cost%", "net_edge", "hit%", "net_viable"]
    by = out.set_index("screen")
    assert by.loc["a", "net_edge"] == pytest.approx(0.4) and by.loc["a", "net_viable"] == "YES"
    assert by.loc["b", "net_edge"] == 0.0 and by.loc["b", "net_viable"] == "no"        # net must be strictly > 0
    assert by.loc["c", "net_edge"] == pytest.approx(0.01) and by.loc["c", "net_viable"] == "YES"
    assert by.loc["d", "net_viable"] == "no"                                          # hit exactly 50 -> no
    assert by.loc["e", "net_viable"] == "no"                                          # hit < 50
    assert by.loc["a", "cost%"] == 0.10 and by.loc["c", "cost%"] == 0.30
    # sorted by market asc, then net_edge desc
    assert list(out["market"]) == ["India", "India", "US", "US", "US"]
    assert list(out[out.market == "US"]["screen"]) == ["e", "a", "b"]


def test_net_table_does_not_mutate_input():
    df = pd.DataFrame({"market": ["US"], "screen": ["a"], "avg_hit_pct": [60.0], "avg_edge": [0.5]})
    ac.net_table(df)
    assert list(df.columns) == ["market", "screen", "avg_hit_pct", "avg_edge"]


def test_main_writes_csvs_and_markdown(tmp_path, monkeypatch, capsys):
    s5 = str(tmp_path / "viability_summary.db")
    _summary(s5, [("US", "s1", 60.0, 0.5), ("India", "s2", 40.0, 0.1)])
    monkeypatch.setattr(ac, "HERE", str(tmp_path))
    monkeypatch.setattr(ac, "SUMMARIES", {"5d": s5, "21d": str(tmp_path / "missing.db")})
    monkeypatch.setattr(pd.DataFrame, "to_markdown", lambda self, index=False: "|TABLE|")
    ac.main()
    err = capsys.readouterr().err
    assert "[skip]" in err and "missing.db" in err and "[5d] wrote" in err
    csv = pd.read_csv(tmp_path / "net_viability_5d.csv")
    assert list(csv["market"]) == ["India", "US"]
    assert not (tmp_path / "net_viability_21d.csv").exists()
    md = (tmp_path / "NET_OF_COST.md").read_text()
    assert "## 5d horizon  (1/2 screens keep positive edge net of cost; 2 were positive pre-cost)" in md
    assert "|TABLE|" in md and "## Bottom line" in md and "| Europe | 0.40% |" in md
