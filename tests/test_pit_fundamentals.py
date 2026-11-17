"""
Point-in-time (PIT) contract of pit_fundamentals.py.

_annual_asof() is the rule that keeps the US backtest free of look-ahead: only
10-K / FY facts with filed <= as-of count, and for each fiscal period-end the
latest version that was public by then wins. These tests pin that rule, the
SQLite cache round-trip that feeds it, and the Piotroski / Coffee-Can scoring
built on it. No network: _load_facts is monkeypatched and the cache DB is an
in-memory SQLite connection.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pit_fundamentals as pf  # noqa: E402


def _fy(end, filed, val, start=None, form="10-K", fp="FY"):
    r = {"form": form, "fp": fp, "end": end, "filed": filed, "val": val}
    if start is not None:
        r["start"] = start
    return r


def _facts(**concepts):
    return {"us-gaap": {c: {"units": {"USD": rows}} for c, rows in concepts.items()}}


# ── the PIT filter itself ─────────────────────────────────────────────────────
def test_fact_filed_on_asof_date_is_known_but_not_the_day_before():
    f = _facts(Assets=[_fy("2021-12-31", "2022-02-15", 500)])
    assert pf._annual_asof(f, "Assets", "2022-02-15") == [500]
    assert pf._annual_asof(f, "Assets", "2022-02-14") == []


def test_restatement_is_invisible_until_it_is_filed():
    # FY2020 originally 100; restated to 90 in the FY2021 10-K filed 2022-02-01.
    f = _facts(NetIncomeLoss=[
        _fy("2020-12-31", "2021-02-01", 100, start="2020-01-01"),
        _fy("2020-12-31", "2022-02-01", 90, start="2020-01-01"),
        _fy("2021-12-31", "2022-02-01", 130, start="2021-01-01"),
    ])
    assert pf._annual_asof(f, "NetIncomeLoss", "2021-06-30") == [100]
    assert pf._annual_asof(f, "NetIncomeLoss", "2022-06-30") == [130, 90]


def test_non_annual_forms_and_periods_are_ignored():
    f = _facts(NetIncomeLoss=[
        _fy("2021-12-31", "2022-02-01", 120, start="2021-01-01"),
        _fy("2021-09-30", "2021-11-01", 40, start="2021-07-01", form="10-Q", fp="Q3"),
        _fy("2021-06-30", "2021-08-01", 999, start="2020-07-01", form="10-K", fp="Q2"),
        _fy("2019-12-31", "2020-02-01", None, start="2019-01-01"),           # no value
    ])
    assert pf._annual_asof(f, "NetIncomeLoss", "2023-01-01") == [120]


@pytest.mark.parametrize("start,kept", [
    ("2021-01-16", False),   # 349 days to 2021-12-31: stub period, dropped
    ("2021-01-15", True),    # 350 days: lower bound inclusive
    ("2020-12-11", True),    # 385 days: 53-week year, upper bound inclusive
    ("2020-12-10", False),   # 386 days: dropped
])
def test_duration_window_350_to_385_days(start, kept):
    f = _facts(Revenues=[_fy("2021-12-31", "2022-02-01", 7, start=start)])
    assert (pf._annual_asof(f, "Revenues", "2023-01-01") == [7]) is kept


def test_instant_balance_sheet_concepts_have_no_start_and_are_accepted():
    f = _facts(Assets=[_fy("2020-12-31", "2021-02-01", 1), _fy("2021-12-31", "2022-02-01", 2)])
    assert pf._annual_asof(f, "Assets", "2022-12-31") == [2, 1]


def test_newest_first_truncated_to_n_and_units_merged():
    f = {"us-gaap": {"Assets": {"units": {
        "USD": [_fy(f"{y}-12-31", f"{y + 1}-02-01", y) for y in range(2015, 2020)],
        "EUR": [_fy(f"{y}-12-31", f"{y + 1}-02-01", y) for y in range(2020, 2023)],
    }}}}
    assert pf._annual_asof(f, "Assets", "2030-01-01", n=3) == [2022, 2021, 2020]
    assert pf._annual_asof(f, "Assets", "2030-01-01", n=10) == list(range(2022, 2014, -1))


def test_missing_concept_and_first_fallback_order():
    f = _facts(Revenues=[_fy("2021-12-31", "2022-02-01", 50, start="2021-01-01")])
    assert pf._annual_asof(f, "NetIncomeLoss", "2023-01-01") == []
    assert pf._first(f, "2023-01-01", "RevenueFromContractWithCustomerExcludingAssessedTax",
                     "Revenues") == [50]
    assert pf._first(f, "2023-01-01", "GrossProfit") == []


# ── SQLite cache round-trip keeps the filing date ─────────────────────────────
def test_prune_store_read_roundtrip_preserves_pit_answers(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
      CREATE TABLE facts(cik TEXT, concept TEXT, unit TEXT, end TEXT, start TEXT, filed TEXT, val REAL);
      CREATE TABLE fetched(cik TEXT PRIMARY KEY);""")
    monkeypatch.setattr(pf, "_conn", conn)
    raw = _facts(
        NetIncomeLoss=[_fy("2020-12-31", "2021-02-01", 100, start="2020-01-01"),
                       _fy("2020-12-31", "2022-02-01", 90, start="2020-01-01"),
                       _fy("2021-03-31", "2021-05-01", 25, start="2021-01-01", form="10-Q", fp="Q1")],
        SomeUnusedConcept=[_fy("2020-12-31", "2021-02-01", 1)],
    )
    pruned = pf._prune(raw)
    assert set(pruned["us-gaap"]) == {"NetIncomeLoss"}
    assert all(r["form"] == "10-K" for r in pruned["us-gaap"]["NetIncomeLoss"]["units"]["USD"])
    assert pf._read("0000000001") is None                        # not fetched yet
    pf._store("0000000001", pruned)
    back = pf._read("0000000001")
    for asof in ("2021-01-31", "2021-06-30", "2022-06-30"):
        assert pf._annual_asof(back, "NetIncomeLoss", asof) == pf._annual_asof(raw, "NetIncomeLoss", asof)


# ── scoring on top of the PIT facts ───────────────────────────────────────────
def _company():
    """Two fiscal years, FY2022 filed 2023-02-15. Hand-computed Piotroski:
    roa 2022 = 120/1000 = .12 > 0 (+1), cfo 150 > 0 (+1), roa up from 100/1000 (+1),
    cfo 150 > ni 120 (+1), leverage 400/1000 < 500/1000 (+1), current 300/150=2 > 250/150 (+1),
    shares 100 <= 100*1.01 (+1), gross margin 400/1000 > 300/900 (+1),
    asset turnover 1000/1000 > 900/1000 (+1)  -> F = 9."""
    d = lambda y, f, v: _fy(f"{y}-12-31", f, v, start=f"{y}-01-01")      # duration
    i = lambda y, f, v: _fy(f"{y}-12-31", f, v)                         # instant
    return _facts(
        NetIncomeLoss=[d(2021, "2022-02-15", 100), d(2022, "2023-02-15", 120)],
        Revenues=[d(2021, "2022-02-15", 900), d(2022, "2023-02-15", 1000)],
        GrossProfit=[d(2021, "2022-02-15", 300), d(2022, "2023-02-15", 400)],
        NetCashProvidedByUsedInOperatingActivities=[d(2021, "2022-02-15", 90), d(2022, "2023-02-15", 150)],
        PaymentsToAcquirePropertyPlantAndEquipment=[d(2022, "2023-02-15", 50)],
        Assets=[i(2021, "2022-02-15", 1000), i(2022, "2023-02-15", 1000)],
        Liabilities=[i(2021, "2022-02-15", 500), i(2022, "2023-02-15", 400)],
        StockholdersEquity=[i(2021, "2022-02-15", 500), i(2022, "2023-02-15", 600)],
        AssetsCurrent=[i(2021, "2022-02-15", 250), i(2022, "2023-02-15", 300)],
        LiabilitiesCurrent=[i(2021, "2022-02-15", 150), i(2022, "2023-02-15", 150)],
        CommonStockSharesOutstanding=[i(2021, "2022-02-15", 100), i(2022, "2023-02-15", 100)],
    )


@pytest.fixture
def company(monkeypatch):
    monkeypatch.setattr(pf, "_load_facts", lambda t: _company() if t == "TEST" else None)


def test_piotroski_hand_computed(company):
    score, det = pf.piotroski_asof("TEST", "2023-06-01")
    assert score == 9 and all(det.values())


def test_as_of_day_before_filing_sees_only_prior_year(company):
    d = pf.as_of("TEST", "2023-02-14")
    assert d["ni"] == [100] and d["assets"] == [1000]
    assert d["roa"] == pytest.approx(0.10) and d["roa_prev"] is None
    score, det = pf.piotroski_asof("TEST", "2023-02-14")
    # one year only: no deltas can be scored; roa>0, cfo>0 (90), cfo<ni (90<100) -> 2
    assert score == 2 and det["roa_pos"] and det["cfo_pos"] and not det["accrual"]


def test_unknown_ticker_and_pre_history_return_empty(company):
    assert pf.as_of("NOPE", "2023-06-01") == {}
    assert pf.piotroski_asof("TEST", "2020-01-01") == (None, {})
    assert pf.coffeecan_asof("TEST", "2023-06-01") == (False, {})   # < 3 years of NI


def test_coffeecan_needs_three_years_and_all_criteria(monkeypatch):
    facts = _company()
    facts["us-gaap"]["NetIncomeLoss"]["units"]["USD"].append(
        _fy("2020-12-31", "2021-02-15", 80, start="2020-01-01"))
    facts["us-gaap"]["StockholdersEquity"]["units"]["USD"].append(_fy("2020-12-31", "2021-02-15", 450))
    facts["us-gaap"]["Revenues"]["units"]["USD"].append(
        _fy("2020-12-31", "2021-02-15", 800, start="2020-01-01"))
    monkeypatch.setattr(pf, "_load_facts", lambda t: facts)
    ok, det = pf.coffeecan_asof("TEST", "2023-06-01", mktcap=5e9)
    # ROE: 120/600=20%, 100/500=20%, 80/450=17.8% -> avg 19.3 > 15; FCF 150-50 > 0;
    # revenue 1000 > 800; leverage 0.4 < 0.6
    assert ok, det
    ok_small, det_small = pf.coffeecan_asof("TEST", "2023-06-01", mktcap=5e8)
    assert not ok_small and det_small["cap>1B"] is False


@pytest.mark.xfail(strict=True, reason="confirmed bug, not fixed: as_of pairs concepts by list "
                   "position, not by fiscal period-end, so a year missing from one concept "
                   "shifts ratios onto the wrong year")
def test_ratios_pair_values_from_the_same_fiscal_year(monkeypatch):
    facts = _company()
    # NetIncomeLoss not tagged for FY2021; FY2020 present. Assets has 2021 + 2022.
    facts["us-gaap"]["NetIncomeLoss"]["units"]["USD"] = [
        _fy("2020-12-31", "2021-02-15", 10, start="2020-01-01"),
        _fy("2022-12-31", "2023-02-15", 120, start="2022-01-01"),
    ]
    monkeypatch.setattr(pf, "_load_facts", lambda t: facts)
    d = pf.as_of("TEST", "2023-06-01")
    # FY2021 net income is unknown, so prior-year ROA must be unknown — not FY2020 NI / FY2021 assets.
    assert d["roa_prev"] is None
