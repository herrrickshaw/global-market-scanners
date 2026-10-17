"""Offline parser tests for the keyless open-data collectors (no network)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_finra_parse():
    from finra_short_interest import parse
    txt = ('"accountingYearMonthNumber"|"symbolCode"|"issueName"|"issuerServicesGroupExchangeCode"|'
           '"marketClassCode"|"currentShortPositionQuantity"|"previousShortPositionQuantity"|'
           '"stockSplitFlag"|"averageDailyVolumeQuantity"|"daysToCoverQuantity"|"revisionFlag"|'
           '"changePercent"|"changePreviousNumber"|"settlementDate"\n'
           '"20260930"|"A"|"Agilent"|"A"|"NYSE"|"5240309"|"5078629"||"2950874"|"1.78"||"3.18"|"161680"|"2026-09-30"\n')
    df = parse(txt)
    assert df.loc[0, "symbol"] == "A" and df.loc[0, "days_to_cover"] == 1.78


def test_ecb_parse():
    from ecb_fx import parse
    xml = ("<E xmlns='x'><Cube><Cube time='2026-10-16'><Cube currency='USD' rate='1.1253'/>"
           "<Cube currency='JPY' rate='177.82'/></Cube></Cube></E>")
    df = parse(xml)
    assert len(df) == 2 and df.per_eur.iloc[0] == 1.1253
