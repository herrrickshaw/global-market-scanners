"""market_holidays.py, pit_panel.py, survivorship.py, net_of_cost.py: pure date/cost logic with hand-computed cases."""
import os
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import market_holidays as mh  # noqa: E402
import net_of_cost as nc  # noqa: E402
import pit_panel as pp  # noqa: E402
import survivorship as sv  # noqa: E402


# ───────────────────────────── market_holidays ─────────────────────────────
@pytest.mark.parametrize("year,easter", [(2024, date(2024, 3, 31)), (2025, date(2025, 4, 20)), (2026, date(2026, 4, 5)),
                                         (2000, date(2000, 4, 23)), (2038, date(2038, 4, 25))])
def test_easter(year, easter):
    assert mh._easter(year) == easter


def test_fixed_holidays_per_market():
    assert mh._fixed("US", 2026) == {date(2026, 1, 1), date(2026, 7, 4), date(2026, 12, 25)}
    assert date(2026, 1, 26) in mh._fixed("India", 2026) and date(2026, 10, 2) in mh._fixed("India", 2026)
    assert len(mh._fixed("Japan", 2026)) == 11 and date(2026, 12, 31) in mh._fixed("Japan", 2026)
    assert date(2026, 6, 6) in mh._fixed("Korea", 2026) and date(2026, 12, 31) in mh._fixed("Korea", 2026)
    eu = mh._fixed("Europe", 2026)
    assert date(2026, 4, 3) in eu and date(2026, 4, 6) in eu and date(2026, 5, 1) in eu and date(2026, 12, 26) in eu     # Good Fri / Easter Mon
    assert mh._fixed("Mars", 2026) == set()


def test_holiday_set_merges_table():
    s = mh._holiday_set("US", 2026)
    assert date(2026, 11, 26) in s and date(2026, 7, 4) in s and isinstance(s, frozenset)
    assert mh._holiday_set("US", 2030) == frozenset(mh._fixed("US", 2030))                         # no table for 2030
    assert mh._holiday_set("Europe", 2026) == frozenset(mh._fixed("Europe", 2026))


def test_canon_and_is_trading_day():
    assert mh._canon("NSE") == "India" and mh._canon("XETRA") == "Europe" and mh._canon("US") == "US" and mh._canon("zzz") == "zzz"
    assert mh.is_trading_day("US", "2026-03-02") is True                                          # ordinary Monday
    assert mh.is_trading_day("US", "2026-03-01") is False and mh.is_trading_day("US", "2026-02-28") is False   # weekend
    assert mh.is_trading_day("NYSE", date(2026, 11, 26)) is False                                  # Thanksgiving via alias
    assert mh.is_trading_day("NASDAQ", pd.Timestamp("2026-11-27")) is True
    assert mh.is_trading_day("US", "2026-07-03") is True and mh.is_trading_day("US", "2026-07-04") is False   # Saturday AND holiday
    assert mh.is_trading_day("Japan", "2026-05-05") is False and mh.is_trading_day("India", "2026-08-15") is False
    assert mh.is_trading_day("KOSPI", "2026-02-17") is False and mh.is_trading_day("Korea", "2026-02-19") is True
    assert mh.is_trading_day("Atlantis", "2026-03-02") is True


def test_trading_days():
    td = mh.trading_days("US", "2026-11-23", "2026-11-30")
    assert [d.strftime("%m-%d") for d in td] == ["11-23", "11-24", "11-25", "11-27", "11-30"]      # 11-26 closed
    assert isinstance(td, pd.DatetimeIndex) and len(mh.trading_days("US", "2026-01-01", "2026-01-01")) == 0
    assert len(mh.trading_days("Europe", "2026-04-01", "2026-04-08")) == 4                          # Good Friday + Easter Monday out


def test_next_trading_day():
    assert mh.next_trading_day("US", date(2026, 11, 25)) == date(2026, 11, 27)                      # skips Thanksgiving
    assert mh.next_trading_day("US", "2026-11-27") == date(2026, 11, 30)                            # Fri -> Mon
    assert mh.next_trading_day("US", date(2026, 3, 2)) == date(2026, 3, 3)
    assert mh.next_trading_day("US") > date.today()
    assert pd.Timestamp(mh.next_trading_day("India", pd.Timestamp("2026-08-14"))).date() == date(2026, 8, 17)


def test_should_run_today(monkeypatch):
    class D(date):
        @classmethod
        def today(cls):
            return cls(2026, 11, 26)
    monkeypatch.setattr(mh, "date", D)
    assert mh.should_run_today("US") is False and mh.should_run_today("Japan") is True


# ───────────────────────────── pit_panel ─────────────────────────────
def test_d_parsing():
    assert pp._d(datetime(2024, 1, 2, 5)) == date(2024, 1, 2) and pp._d(date(2024, 1, 2)) == date(2024, 1, 2)
    assert pp._d("2024-01-02T10:00") == date(2024, 1, 2) and pp._d(pd.Timestamp("2024-01-02")) == date(2024, 1, 2)


PANEL = [{"symbol": "A", "filed_date": "2023-02-10", "roe": 0.10}, {"symbol": "A", "filed_date": "2024-02-12", "roe": 0.14},
         {"symbol": "A", "filed_date": "2025-02-11", "roe": 0.18}, {"symbol": "B", "filed_date": "2024-05-01", "roe": 0.22}]


def test_as_of():
    got = pp.as_of(PANEL, "2024-06-30")
    assert got["A"]["roe"] == 0.14 and got["B"]["roe"] == 0.22
    assert pp.as_of(PANEL, "2024-02-12")["A"]["roe"] == 0.14                          # filed ON asof is known
    assert pp.as_of(PANEL, "2024-02-11")["A"]["roe"] == 0.10 and "B" not in pp.as_of(PANEL, "2024-04-30")
    assert pp.as_of(PANEL, "2022-01-01") == {} and pp.as_of([], "2024-01-01") == {}
    df = pd.DataFrame(PANEL)
    assert pp.as_of(df, "2025-12-31")["A"]["roe"] == 0.18
    shuffled = [PANEL[1], PANEL[0], PANEL[3], PANEL[2]]
    assert pp.as_of(shuffled, "2024-06-30")["A"]["roe"] == 0.14                       # order-independent
    custom = [{"cd": "x", "filed": "2024-01-01", "v": 1}]
    assert pp.as_of(custom, "2024-06-01", symbol_col="cd", filed_col="filed")["x"]["v"] == 1


def test_is_point_in_time_and_leaks():
    assert pp.is_point_in_time(PANEL) is True and pp.is_point_in_time(pd.DataFrame(PANEL)) is True
    assert pp.is_point_in_time([]) is False
    for bad in (None, "", "NaT"):
        assert pp.is_point_in_time(PANEL + [{"symbol": "C", "filed_date": bad}]) is False
    assert pp.is_point_in_time([{"symbol": "C"}]) is False
    assert pp.leaks_lookahead(PANEL, "2024-06-30") is True and pp.leaks_lookahead(PANEL, "2025-02-11") is False
    assert pp.leaks_lookahead([], "2024-01-01") is False and pp.leaks_lookahead(pd.DataFrame(PANEL), "2024-06-30") is True


def test_history_gate():
    assert pp.min_history_ok(756) is True and pp.min_history_ok(755) is False
    assert pp.min_history_ok(250, min_years=1.0, trading_days_year=250) is True
    g = pp.gate_markets({"US": 2520, "BR": 900, "JP": 240, "CN": 756})
    assert g == {"report": ["BR", "CN", "US"], "withhold": ["JP"], "min_years": 3.0, "min_days": 756}
    assert pp.gate_markets({"X": 300}, min_years=1.0) == {"report": ["X"], "withhold": [], "min_years": 1.0, "min_days": 252}
    assert pp.gate_markets({})["report"] == []


def test_pit_main(capsys):
    pp.main()
    out = capsys.readouterr().out
    assert "AAA: roe=0.14 (filed 2024-02-12)" in out and "leaks_lookahead(full panel @2024-06-30) = True" in out
    assert "report:   ['BR', 'US']" in out and "withhold: ['CN', 'JP']" in out


# ───────────────────────────── survivorship ─────────────────────────────
MEMB = [{"symbol": "ALIVE", "add_date": "2015-01-01", "remove_date": None},
        {"symbol": "DIED", "add_date": "2015-01-01", "remove_date": "2020-03-15"},
        {"symbol": "FUTURE", "add_date": "2024-01-01", "remove_date": ""},
        {"symbol": "NOADD", "add_date": None, "remove_date": None}]


def test_point_in_time_universe_boundaries():
    assert sv.point_in_time_universe(MEMB, "2019-01-01") == ["ALIVE", "DIED"]
    assert sv.point_in_time_universe(MEMB, "2021-01-01") == ["ALIVE"] and sv.point_in_time_universe(MEMB, "2025-01-01") == ["ALIVE", "FUTURE"]
    assert "FUTURE" in sv.point_in_time_universe(MEMB, "2024-01-01") and "FUTURE" not in sv.point_in_time_universe(MEMB, "2023-12-31")
    assert "DIED" in sv.point_in_time_universe(MEMB, "2020-03-14") and "DIED" not in sv.point_in_time_universe(MEMB, "2020-03-15")
    assert sv.point_in_time_universe([], "2020-01-01") == [] and sv._d(None) is None
    assert sv.point_in_time_universe(MEMB, datetime(2019, 1, 1, 12)) == ["ALIVE", "DIED"]


def test_apply_delisting_returns():
    idx = pd.date_range("2024-01-01", periods=5)
    df = pd.DataFrame({"X": [0.01] * 5, "Y": [0.02] * 5, "Z": [0.0] * 5}, index=idx)
    out = sv.apply_delisting_returns(df, {"X": {"date": "2024-01-03", "final_return": -0.6}, "NOPE": {"date": "2024-01-01", "final_return": 1}})
    assert list(out["X"].iloc[:2]) == [0.01, 0.01] and out["X"].iloc[2] == -0.6 and out["X"].iloc[3:].isna().all()
    assert list(out["Y"]) == [0.02] * 5 and list(df["X"]) == [0.01] * 5                      # input untouched, others intact
    buyout = sv.apply_delisting_returns(df, {"Y": {"date": pd.Timestamp("2024-01-05"), "final_return": "0.25"}})
    assert buyout["Y"].iloc[4] == 0.25 and buyout["Y"].iloc[:4].eq(0.02).all()
    assert sv.apply_delisting_returns(df, {}).equals(df)


def test_d_accepts_plain_date():
    assert sv._d(date(2024, 1, 2)) == date(2024, 1, 2) and sv._d("2024-01-02 junk") == date(2024, 1, 2)


def test_survivorship_gap_and_coverage():
    assert sv.survivorship_gap(0.05, 0.08) == pytest.approx(0.03) and sv.survivorship_gap(0.08, 0.05) == pytest.approx(-0.03)
    assert sv.coverage(MEMB, "2019-06-01") == {"asof": "2019-06-01", "in_universe": 2, "ever_listed": 4}


def test_survivorship_main(capsys):
    sv.main()
    out = capsys.readouterr().out
    assert "as of 2016-01-01: ['ALIVE1', 'DIED']" in out and "as of 2021-01-01: ['ALIVE1', 'ALIVE2']" in out
    assert "as of 2025-01-01: ['ALIVE1', 'ALIVE2', 'FUTURE']" in out


# ───────────────────────────── net_of_cost ─────────────────────────────
def test_amihud_impact():
    assert nc.amihud_impact(1e-9, 1e6) == pytest.approx(1e-3) and nc.amihud_impact(0, 1e6) == 0.0 and nc.amihud_impact(1e-9, 0) == 0.0
    assert nc.amihud_impact(-1, 5) == 0.0 and nc.amihud_impact(1.0, 1.0) == 0.5 and nc.amihud_impact(1e-6, 1e9) == 0.5     # capped
    assert nc.amihud_impact(0.2, 1.0) == pytest.approx(0.2)


def test_round_trip_cost():
    assert nc.round_trip_cost() == pytest.approx(0.002) and nc.round_trip_cost(0.001, 0.002) == pytest.approx(0.006)
    assert nc.round_trip_cost(amihud_illiq=1e-9, trade_value=1e6) == pytest.approx(2 * (0.001 + 0.001))
    assert nc.round_trip_cost(-0.5, -0.5) == 0.0                                       # negatives floored at 0


def test_net_spread_and_break_even():
    assert nc.net_spread(0.05, 0.01, 2.0) == pytest.approx(0.03) and nc.net_spread(0.05, 0.01) == pytest.approx(0.04)
    assert nc.net_spread(0.05, 0.01, -3) == 0.05 and nc.net_spread(0.05, -1.0, 2) == 0.05           # negatives floored
    assert nc.break_even_cost(0.06, 2.0) == pytest.approx(0.03) and nc.break_even_cost(0.06) == 0.06
    assert nc.break_even_cost(0.06, 0) == float("inf") and nc.break_even_cost(0.06, -1) == float("inf")


def test_survives_costs():
    assert nc.survives_costs(0.0011, "US") is True and nc.survives_costs(0.0010, "US") is False       # strictly greater
    assert nc.survives_costs(0.0031, "Mars") is True and nc.survives_costs(0.0030, "Mars") is False   # default 0.30%
    assert nc.survives_costs(0.0011, "US", turnover=2.0) is False and nc.survives_costs(0.01, "US", round_trip=0.004) is True
    assert nc.survives_costs(0.0036, "BR") is True and nc.survives_costs(0.0034, "BR") is False
    assert nc.survives_costs(0.1, "US", turnover=0) is True


def test_net_report():
    r = nc.net_report(0.108, "US", 0.5)
    assert r == {"market": "US", "gross_spread": 0.108, "round_trip_cost": 0.001, "turnover": 0.5, "net_spread": 0.1075,
                 "break_even_cost": 0.216, "survives": True}
    assert nc.net_report(0.004, "BR", 1.0)["survives"] is True and nc.net_report(0.003, "BR", 1.0)["survives"] is False
    z = nc.net_report(0.05, "JP", turnover=0)
    assert z["break_even_cost"] == float("inf") and z["survives"] is True and z["net_spread"] == 0.05
    c = nc.net_report(0.05, "US", 1.0, round_trip=0.02)
    assert c["round_trip_cost"] == 0.02 and c["net_spread"] == 0.03


def test_net_of_cost_main(capsys):
    nc.main()
    out = capsys.readouterr().out
    assert "net_of_cost.py" in out and "accumulation 6m" in out and out.count("YES") >= 2 and "no" in out
    assert "A signal survives iff" in out
