"""unlisted_enrichment.py: private-firm sourcing providers (HTTP faked) and the CLI merge flow."""
import io
import json
import os
import sys
import urllib.error

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unlisted_enrichment as ue  # noqa: E402


class Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(b""))


@pytest.fixture
def net(monkeypatch):
    """Records requests; `responses` is a list of payloads / exceptions consumed in order."""
    state = {"reqs": [], "responses": [], "sleeps": []}

    def urlopen(req, timeout=None):
        state["reqs"].append((req, timeout))
        r = state["responses"].pop(0)
        if isinstance(r, Exception):
            raise r
        return Resp(r)

    monkeypatch.setattr(ue.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(ue.time, "sleep", lambda s: state["sleeps"].append(s))
    return state


def _wd(*names):
    return {"results": {"bindings": [
        {"cLabel": {"value": n}, "website": {"value": f"http://{n}.in"}, "employees": {"value": "50"},
         "cityLabel": {"value": "Pune"}} for n in names]}}


def test_wikidata_parses_dedupes_and_builds_query(net):
    net["responses"] = [_wd("Acme Ltd", "ACME LTD", "Beta Pvt")]
    rows = ue.wikidata_unlisted("Auto Parts", ["auto parts", "Component"], limit=7)
    assert [r["company_name"] for r in rows] == ["Acme Ltd", "Beta Pvt"]       # case-insensitive dedupe
    r = rows[0]
    assert r["source"] == "wikidata" and r["is_listed"] is False and r["country"] == "India"
    assert r["segment"] == "Auto Parts" and r["city"] == "Pune" and r["employees"] == "50"
    req, timeout = net["reqs"][0]
    assert timeout == 90 and "LIMIT+7" in req.full_url and "Q668" in req.full_url
    assert "auto+parts" in req.full_url and "component" in req.full_url      # keywords lower-cased into the filter
    assert req.headers["User-agent"] == ue.UA


def test_wikidata_missing_optional_fields(net):
    net["responses"] = [{"results": {"bindings": [{"cLabel": {"value": "Solo"}}]}}]
    r = ue.wikidata_unlisted("S", ["k"])[0]
    assert r["website"] is None and r["employees"] is None and r["city"] is None


def test_wikidata_retries_429_then_succeeds(net, capsys):
    net["responses"] = [_http_error(429), _http_error(429), _wd("Ok Co")]
    rows = ue.wikidata_unlisted("S", ["k"])
    assert [r["company_name"] for r in rows] == ["Ok Co"]
    assert net["sleeps"] == [65, 65] and len(net["reqs"]) == 3
    assert "429, waiting 65s" in capsys.readouterr().err


def test_wikidata_gives_up_after_four_429s(net, capsys):
    net["responses"] = [_http_error(429)] * 4
    assert ue.wikidata_unlisted("S", ["k"]) == []
    assert net["sleeps"] == [65, 65, 65] and len(net["reqs"]) == 4
    assert "[wikidata] S:" in capsys.readouterr().err


def test_wikidata_other_http_error_not_retried(net):
    net["responses"] = [_http_error(500)]
    assert ue.wikidata_unlisted("S", ["k"]) == []
    assert len(net["reqs"]) == 1 and net["sleeps"] == []


def test_credentialed_providers_require_env(monkeypatch):
    for k in ("DNB_KEY", "DNB_SECRET", "LUSHA_API_KEY", "APOLLO_API_KEY", "OPENCORPORATES_API_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError, match="DNB_KEY and DNB_SECRET"):
        ue.dnb_unlisted("S", ["k"])
    monkeypatch.setenv("DNB_KEY", "k")
    with pytest.raises(RuntimeError, match="DNB_KEY and DNB_SECRET"):
        ue.dnb_unlisted("S", ["k"])             # secret still missing
    with pytest.raises(RuntimeError, match="LUSHA_API_KEY"):
        ue.lusha_unlisted("S", ["k"])
    with pytest.raises(RuntimeError, match="APOLLO_API_KEY"):
        ue.apollo_unlisted("S", ["k"])
    with pytest.raises(RuntimeError, match="OPENCORPORATES_API_TOKEN"):
        ue.opencorporates_unlisted("S", ["k"])


def test_dnb_flow(net, monkeypatch):
    monkeypatch.setenv("DNB_KEY", "key"); monkeypatch.setenv("DNB_SECRET", "sec")
    net["responses"] = [{"access_token": "tok"}, {"searchCandidates": [
        {"organization": {"primaryName": "D1", "websiteAddress": [{"url": "d1.com"}],
                          "numberOfEmployees": [{"value": 10}],
                          "primaryAddress": {"addressLocality": {"name": "Delhi"}},
                          "isStandalone": False}},
        {"organization": {"primaryName": "D2"}}]}]
    rows = ue.dnb_unlisted("Seg", ["first", "second"], geo="IN", limit=5)
    assert len(net["reqs"]) == 2                                  # token + exactly ONE keyword search
    body = json.loads(net["reqs"][1][0].data)
    assert body["searchTerm"] == "first" and body["pageSize"] == 5 and body["countryISOAlpha2Code"] == "IN"
    assert net["reqs"][1][0].headers["Authorization"] == "Bearer tok"
    import base64
    assert net["reqs"][0][0].headers["Authorization"] == "Basic " + base64.b64encode(b"key:sec").decode()
    a, b = rows
    assert a["company_name"] == "D1" and a["website"] == "d1.com" and a["employees"] == 10
    assert a["city"] == "Delhi" and a["is_listed"] is True and a["source"] == "dnb"
    assert b["website"] is None and b["employees"] is None and b["city"] is None and b["is_listed"] is False


def test_lusha_flow(net, monkeypatch):
    monkeypatch.setenv("LUSHA_API_KEY", "k")
    net["responses"] = [{"data": [{"name": "L1", "domain": "l1.com", "companySize": "11-50",
                                   "revenue": 5, "location": {"city": "Pune"}}, {"name": "L2"}]}]
    rows = ue.lusha_unlisted("Seg", ["kw"], geo="India", limit=9)
    body = json.loads(net["reqs"][0][0].data)
    assert body["pages"] == {"page": 0, "size": 9} and body["filters"]["companies"]["include"]["names"] == ["kw"]
    assert rows[0]["city"] == "Pune" and rows[0]["employees"] == "11-50" and rows[0]["source"] == "lusha"
    assert rows[1]["city"] is None and rows[1]["is_listed"] is False


def test_apollo_flow_and_page_cap(net, monkeypatch):
    monkeypatch.setenv("APOLLO_API_KEY", "k")
    net["responses"] = [{"organizations": [{"name": "A1", "website_url": "a1.com", "estimated_num_employees": 5,
                                            "annual_revenue": 9, "city": "X", "publicly_traded_symbol": "AAA"},
                                           {"name": "A2"}]}]
    rows = ue.apollo_unlisted("Seg", ["kw"], limit=500)
    body = json.loads(net["reqs"][0][0].data)
    assert body["per_page"] == 100 and body["page"] == 1
    assert rows[0]["is_listed"] is True and rows[1]["is_listed"] is False and rows[0]["revenue"] == 9
    net["responses"] = [{"organizations": []}]
    ue.apollo_unlisted("Seg", ["kw"], limit=30)
    assert json.loads(net["reqs"][1][0].data)["per_page"] == 30


def _oc(*cos):
    return {"results": {"companies": [{"company": c} for c in cos]}}


def test_opencorporates_dedupes_skips_errors_and_parses_city(net, monkeypatch):
    monkeypatch.setenv("OPENCORPORATES_API_TOKEN", "t")
    net["responses"] = [
        _oc({"name": "Alpha", "registered_address_in_full": "Street 1, Pune, India", "company_number": "N1",
             "current_status": "Active", "incorporation_date": "2001-01-01", "opencorporates_url": "oc/1"},
            {"name": "alpha"}, {"name": None}, {"name": "Beta", "registered_address_in_full": "OnlyCity"}),
        _http_error(401),
        _oc({"name": "Gamma", "registered_address_in_full": "a, b"}),
    ]
    rows = ue.opencorporates_unlisted("Seg", ["k1", "k2", "k3"], geo="in", limit=500)
    assert [r["company_name"] for r in rows] == ["Alpha", "Beta", "Gamma"]
    assert rows[0]["city"] == "Pune" and rows[0]["company_number"] == "N1" and rows[0]["status"] == "Active"
    assert rows[1]["city"] == "OnlyCity" and rows[2]["city"] == "a, b"     # <2 commas -> whole address
    assert "per_page=100" in net["reqs"][0][0].full_url and "api_token=t" in net["reqs"][0][0].full_url
    assert net["sleeps"] == [0.6, 0.6]                                      # sleep skipped after the failed request


def test_opencorporates_empty_address_city_none(net, monkeypatch):
    monkeypatch.setenv("OPENCORPORATES_API_TOKEN", "t")
    net["responses"] = [_oc({"name": "Nada"})]
    assert ue.opencorporates_unlisted("Seg", ["k"])[0]["city"] is None


def _gleif(*names):
    return {"data": [{"id": f"LEI{i}", "attributes": {"entity": {
        "legalName": {"name": n}, "legalAddress": {"city": "Mumbai"}, "legalForm": {"id": "F1"}, "status": "ACTIVE"}}}
        for i, n in enumerate(names)]}


def test_gleif_uses_segment_keywords_and_dedupes(net):
    net["responses"] = [_gleif("Hosp One", "HOSP ONE"), _gleif("Hosp Two")]
    rows = ue.gleif_unlisted("Medical Care Facilities", ["ignored"], geo="IN", cap=80)
    assert [r["company_name"] for r in rows] == ["Hosp One", "Hosp Two"]
    assert len(net["reqs"]) == 2                                    # HOSPITAL, HEALTHCARE
    assert "HOSPITAL" in net["reqs"][0][0].full_url and "HEALTHCARE" in net["reqs"][1][0].full_url
    assert rows[0]["lei"] == "LEI0" and rows[0]["legal_form"] == "F1" and rows[0]["city"] == "Mumbai"


def test_gleif_falls_back_to_upper_keywords_and_handles_http_error(net):
    net["responses"] = [_http_error(500), _gleif("Zed")]
    rows = ue.gleif_unlisted("Unknown Segment", ["foo", "bar"])
    assert [r["company_name"] for r in rows] == ["Zed"]
    assert "filter%5Bentity.legalName%5D=FOO" in net["reqs"][0][0].full_url
    assert "BAR" in net["reqs"][1][0].full_url


def test_gleif_cap_stops_early(net):
    net["responses"] = [_gleif("A1", "A2", "A3", "A4"), _gleif("never")]
    rows = ue.gleif_unlisted("Medical Care Facilities", ["x"], cap=2)
    assert [r["company_name"] for r in rows] == ["A1", "A2"] and len(net["reqs"]) == 1


def test_listed_names(monkeypatch, tmp_path):
    monkeypatch.setattr(ue, "MAIN_PARQUET", str(tmp_path / "none.parquet"))
    assert ue.listed_names() == set()
    p = tmp_path / "m.parquet"
    p.write_bytes(b"x")
    monkeypatch.setattr(ue, "MAIN_PARQUET", str(p))
    monkeypatch.setattr(ue.pd, "read_parquet", lambda path: pd.DataFrame({"company_name": ["Foo Ltd", "BAR"]}))
    assert ue.listed_names() == {"foo ltd", "bar"}


@pytest.fixture
def cli(monkeypatch, tmp_path):
    saved = {}
    main_df = pd.DataFrame({"company_name": ["Listed Co"], "ticker": ["L"], "code": ["c"], "exchange": ["NSE"],
                            "sector": ["S"], "industry": ["I"], "peer_count": [3], "global_peers": [["x"]],
                            "country": ["India"], "segment": ["Auto Parts"]})
    monkeypatch.setattr(ue, "MAIN_PARQUET", str(tmp_path / "main.parquet"))
    monkeypatch.setattr(ue, "OUT_PARQUET", str(tmp_path / "out.parquet"))
    (tmp_path / "main.parquet").write_bytes(b"x")
    monkeypatch.setattr(ue.pd, "read_parquet", lambda p: main_df.copy())
    monkeypatch.setattr(pd.DataFrame, "to_parquet", lambda self, p, **kw: saved.__setitem__(p, self.copy()))
    monkeypatch.setattr(ue.time, "sleep", lambda s: saved.setdefault("sleeps", []).append(s))
    monkeypatch.setattr(ue.os.path, "getsize", lambda p: 2048)
    return saved


def _run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["unlisted_enrichment.py", *argv])
    ue.main()


def test_main_filters_listed_names_dedupes_and_paces(cli, monkeypatch, capsys):
    calls = []

    def fake(seg, kws):
        calls.append((seg, kws))
        return [{"company_name": "LISTED CO", "segment": seg}, {"company_name": "Priv One", "segment": seg},
                {"company_name": "Priv One", "segment": seg}, {"company_name": None, "segment": seg}]
    monkeypatch.setitem(ue.PROVIDERS, "wikidata", fake)
    _run(monkeypatch, "--segments", "Auto Parts", "Nope", "Software - Application")
    err = capsys.readouterr().err
    assert [c[0] for c in calls] == ["Auto Parts", "Software - Application"]
    assert "[skip] unknown segment: Nope" in err
    assert cli["sleeps"] == [62.0, 62.0]
    out = cli[ue.OUT_PARQUET]
    # listed filtered, None name kept? (None -> "" not in known) -> yes kept; duplicates collapsed per segment
    assert sorted(out["company_name"].dropna().unique()) == ["Priv One"]
    assert set(out["segment"]) == {"Auto Parts", "Software - Application"}
    assert "fetched_at" in out.columns
    assert "DONE:" in err and "2 KB" in err


def test_main_non_wikidata_pace_geo_and_merge(cli, monkeypatch, capsys):
    got = []
    monkeypatch.setitem(ue.PROVIDERS, "gleif", lambda seg, kws, geo: got.append(geo) or [
        {"company_name": "New Co", "segment": seg, "country": "India"}])
    _run(monkeypatch, "--source", "gleif", "--segments", "Auto Parts", "--merge", "--pace", "0")
    assert got == ["IN"] and cli["sleeps"] == [0.0]
    merged = cli[ue.MAIN_PARQUET]
    assert len(merged) == 2
    new = merged[merged["company_name"] == "New Co"].iloc[0]
    assert new["exchange"] == "UNLISTED" and new["peer_count"] == 0 and new["industry"] == "Auto Parts"
    assert new["global_peers"] == []
    assert "merged into" in capsys.readouterr().err


def test_main_default_pace_and_geo_for_other_sources(cli, monkeypatch):
    got = []
    monkeypatch.setitem(ue.PROVIDERS, "lusha", lambda seg, kws, geo: got.append(geo) or [
        {"company_name": "Z", "segment": seg}])
    _run(monkeypatch, "--source", "lusha", "--segments", "Auto Parts", "--geo", "Brazil")
    assert got == ["Brazil"] and cli["sleeps"] == [1.0]
    assert ue.MAIN_PARQUET not in cli                    # no --merge


def test_main_missing_credentials_exit_2(cli, monkeypatch, capsys):
    def boom(seg, kws, geo):
        raise RuntimeError("Set DNB_KEY")
    monkeypatch.setitem(ue.PROVIDERS, "dnb", boom)
    with pytest.raises(SystemExit) as e:
        _run(monkeypatch, "--source", "dnb", "--segments", "Auto Parts")
    assert e.value.code == 2 and "dnb: Set DNB_KEY" in capsys.readouterr().err


def test_main_nothing_written_when_empty(cli, monkeypatch, capsys):
    monkeypatch.setitem(ue.PROVIDERS, "wikidata", lambda seg, kws: [])
    _run(monkeypatch, "--segments", "Auto Parts")
    assert ue.OUT_PARQUET not in cli
    assert "No unlisted firms returned" in capsys.readouterr().err
