"""data_sources.py: registry lookups and CLI output."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import data_sources as ds  # noqa: E402


def test_public_factor_sources_by_market():
    assert ds.public_factor_sources("IN") == ["iffm", "aqr", "ken_french"]   # India: IFFM first
    assert ds.public_factor_sources("us") == ["aqr", "ken_french"]
    assert ds.public_factor_sources("BR") == ["aqr", "ken_french"]      # emerging still gets AQR global
    assert ds.public_factor_sources("XX") == ["aqr"]                    # unknown market: AQR only


def test_raw_sources():
    assert ds.raw_sources("US") == ["yfinance", "edgar"]
    assert ds.raw_sources("in") == ["nsepython", "yfinance"]
    assert ds.raw_sources("JP") == ["yfinance"]


def test_filing_source_and_coverage():
    assert ds.filing_source("us")["system"] == "SEC EDGAR"
    assert ds.filing_source("ZZ") is None
    c = ds.filing_coverage()
    assert c["markets"] == len(ds.PLATFORM_MARKETS)
    assert c["with_api"] == len(c["api_markets"]) and set(c["api_markets"]) <= set(c["covered"])
    assert "US" in c["api_markets"] and "IN" not in c["covered"]        # IN is not a platform market here
    assert c["with_filing_dates"] == len(c["covered"])


def test_for_market_fields():
    d = ds.for_market("kr")
    assert d == {"market": "KR", "currency": "KRW", "ken_french_region": "Emerging", "aqr_country_qmj": False,
                 "public_factor_sources": ["aqr", "ken_french"], "raw_sources": ["yfinance"]}
    assert ds.for_market("JP")["aqr_country_qmj"] is True
    assert ds.for_market("IN")["aqr_country_qmj"] is True and ds.for_market("IN")["currency"] == "INR"
    assert ds.for_market("QQ")["currency"] is None


def test_paper_sources_and_coverage_rows():
    p = ds.paper_sources()
    assert p["returns_and_fundamentals"]["license"].startswith("COMMERCIAL")
    assert p["factor_benchmark"]["name"].startswith("IIMA")
    rows = ds.coverage()
    assert [r["market"] for r in rows] == ds.PLATFORM_MARKETS + ["IN"]


def test_fmt_lib():
    assert ds._fmt_lib("iffm") == "iffm (public/free)"
    assert ds._fmt_lib("aqr") == "aqr (public/free (registration))"


def _run(monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, "argv", ["ds", *argv])
    ds.main()
    return capsys.readouterr().out


def test_main_paper(monkeypatch, capsys):
    out = _run(monkeypatch, capsys, "--paper")
    assert "CMIE Prowess" in out and "Only the IFFM factor library is public" in out


def test_main_public(monkeypatch, capsys):
    out = _run(monkeypatch, capsys, "--public")
    for k in ("[iffm]", "[aqr]", "[ken_french]"):
        assert k in out


def test_main_market_detail(monkeypatch, capsys):
    out = _run(monkeypatch, capsys, "--market", "kr")
    assert "=== KR (KRW) ===" in out and "Ken French region : Emerging" in out
    assert "no (use AQR global)" in out and "Yahoo Finance (yfinance)" in out
    out = _run(monkeypatch, capsys, "--market", "us")
    assert "AQR country QMJ    : yes" in out and "SEC EDGAR XBRL" in out


def test_main_table(monkeypatch, capsys):
    out = _run(monkeypatch, capsys)
    lines = out.splitlines()
    assert lines[0].startswith("mkt ") and lines[1] == "-" * 78
    assert sum(1 for l in lines if l.startswith(("US ", "IN "))) == 2
    assert "country" in out and "global" in out and "Raw pipeline sources" in out
