"""brazil_cvm.py + brazil_quality.py: CVM filing parsing, PIT panel and quality quintiles (offline)."""
import io
import os
import sys
import types
import zipfile

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brazil_cvm as cvm  # noqa: E402
import brazil_quality as bq  # noqa: E402


def csv_bytes(header, rows):
    return ("\n".join([";".join(header)] + [";".join(r) for r in rows])).encode("latin-1")


H = ["CD_CVM", "DT_REFER", "VERSAO", "DT_RECEB", "DENOM_CIA", "CNPJ_CIA", "CATEG_DOC"]
S = ["CD_CVM", "DT_REFER", "VERSAO", "DT_FIM_EXERC", "CD_CONTA", "DS_CONTA", "VL_CONTA", "ESCALA_MOEDA"]


def test_helpers():
    assert cvm._norm("Patrimônio Líquido") == "patrimonio liquido" and cvm._norm(None) == ""
    assert cvm._d("2024-03-31 extra") == cvm.date(2024, 3, 31)
    assert cvm._num("1,5") == 1.5 and cvm._num("  ") is None and cvm._num(None) is None and cvm._num("x") is None
    assert cvm._scale("MIL") == 1000.0 and cvm._scale(" mil ") == 1000.0 and cvm._scale("UNIDADE") == 1.0
    assert cvm._scale(None) == 1.0


@pytest.mark.parametrize("roe,ok", [(None, False), (-1.0, True), (1.0, True), (1.0001, False), (-1.01, False), (0.0, True)])
def test_plausible_roe(roe, ok):
    assert cvm.plausible_roe(roe) is ok


def test_parse_header_and_latin1():
    data = csv_bytes(H, [["9512", "2024-12-31", "1", "2025-03-01", "Petrobrás SA", " 33 ", "DFP"]])
    h = cvm.parse_header(data)
    assert h[("9512", "2024-12-31", "1")] == {"filed_date": "2025-03-01", "name": "Petrobrás SA",
                                              "cnpj": "33", "categ": "DFP"}
    assert cvm.parse_header(data.decode("latin-1")) == h        # str input accepted too


def test_parse_statement_scales_and_filters_period():
    rows = [["1", "2024-12-31", "1", "2024-12-31", "3.01", "Receita", "10,5", "MIL"],
            ["1", "2024-12-31", "1", "2024-12-31", "3.11", "Lucro", "2", "UNIDADE"],
            ["1", "2024-12-31", "1", "2023-12-31", "3.01", "Receita ant", "99", "MIL"],     # prior period -> skipped
            ["1", "2024-12-31", "1", "2024-12-31", "9.99", "Other", "5", "MIL"],             # not wanted
            ["1", "2024-12-31", "1", "2024-12-31", "3.01", "dup", "", "MIL"]]                # blank value skipped
    out = cvm.parse_statement(csv_bytes(S, rows), {"3.01": "revenue", "3.11": "net_income"})
    assert out == {("1", "2024-12-31", "1"): {"revenue": 10500.0, "net_income": 2.0}}


def test_parse_equity_picks_depth2_pl_by_description():
    rows = [["1", "2024-12-31", "1", "2024-12-31", "2.03", "Patrimônio Líquido", "50", "MIL"],
            ["1", "2024-12-31", "1", "2024-12-31", "2.03.01", "Patrimônio Líquido Capital", "7", "MIL"],   # depth 3
            ["2", "2024-12-31", "1", "2024-12-31", "2.07", "Patrimonio Liquido Consolidado", "8", "UNIDADE"],
            ["3", "2024-12-31", "1", "2024-12-31", "2.01", "Passivo Circulante", "9", "MIL"],
            ["4", "2024-12-31", "1", "2023-12-31", "2.03", "Patrimônio Líquido", "9", "MIL"],
            ["5", "2024-12-31", "1", "2024-12-31", "2.03", "Patrimônio Líquido", "", "MIL"]]
    out = cvm.parse_equity(csv_bytes(S, rows))
    assert out == {("1", "2024-12-31", "1"): {"equity": 50000.0}, ("2", "2024-12-31", "1"): {"equity": 8.0}}


def test_build_panel_versions_roe_and_filters():
    header = {("1", "2024-12-31", "1"): {"filed_date": "2025-03-01", "name": "A"},
              ("1", "2024-12-31", "2"): {"filed_date": "2025-06-01", "name": "A"},     # restatement kept separately
              ("2", "2024-12-31", "1"): {"filed_date": "", "name": "B"},               # no filed date -> dropped
              ("3", "2024-12-31", "1"): {"filed_date": "2025-03-01", "name": "C"}}     # no metrics -> dropped
    st1 = {("1", "2024-12-31", "1"): {"net_income": 10.0, "revenue": 100.0},
           ("1", "2024-12-31", "2"): {"net_income": 20.0},
           ("2", "2024-12-31", "1"): {"net_income": 1.0}}
    st2 = {("1", "2024-12-31", "1"): {"equity": 50.0}, ("1", "2024-12-31", "2"): {"equity": 0.0}}
    panel = cvm.build_panel(header, [st1, st2])
    by = {(r["cd_cvm"], r["version"]): r for r in panel}
    assert set(by) == {("1", "1"), ("1", "2")}
    assert by[("1", "1")]["roe"] == 0.2 and by[("1", "1")]["revenue"] == 100.0
    assert by[("1", "2")]["roe"] is None                        # zero equity -> no ROE
    assert by[("1", "1")]["fiscal_end"] == "2024-12-31" and by[("1", "1")]["assets"] is None


def _zip_bytes(members):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, d in members.items():
            z.writestr(n, d)
    return b.getvalue()


def _year_members(year=2024, with_ind=False, with_bpp=True):
    p = "dfp_cia_aberta"
    m = {f"{p}_{year}.csv": csv_bytes(H, [["1", f"{year}-12-31", "1", f"{year+1}-03-01", "A", "c", "DFP"]]),
         f"{p}_DRE_con_{year}.csv": csv_bytes(S, [["1", f"{year}-12-31", "1", f"{year}-12-31", "3.11", "L", "10", "UNIDADE"]]),
         f"{p}_BPA_ind_{year}.csv": csv_bytes(S, [["1", f"{year}-12-31", "1", f"{year}-12-31", "1", "Ativo", "500", "UNIDADE"]])}
    if with_bpp:
        m[f"{p}_BPP_con_{year}.csv"] = csv_bytes(S, [["1", f"{year}-12-31", "1", f"{year}-12-31", "2.03", "Patrimônio Líquido", "50", "UNIDADE"]])
    return m


def test_fetch_zip_downloads_once_then_uses_cache(monkeypatch, tmp_path):
    blob = _zip_bytes({"a.csv": b"x"})
    calls = []
    ac = types.ModuleType("apiclient")
    ac.http_get = lambda src, url, headers=None: (calls.append((src, url)) or types.SimpleNamespace(content=blob))
    monkeypatch.setitem(sys.modules, "apiclient", ac)
    out = cvm.fetch_zip(2023, "DFP", cache_dir=str(tmp_path))
    assert out == {"a.csv": b"x"}
    assert calls == [("cvm", "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_2023.zip")]
    assert cvm.fetch_zip(2023, "dfp", cache_dir=str(tmp_path)) == {"a.csv": b"x"}
    assert len(calls) == 1


def test_fetch_zip_falls_back_to_requests(monkeypatch, tmp_path):
    ac = types.ModuleType("apiclient")
    ac.http_get = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gov down"))
    monkeypatch.setitem(sys.modules, "apiclient", ac)
    rq = types.ModuleType("requests")
    rq.get = lambda url, headers=None, timeout=None: types.SimpleNamespace(content=_zip_bytes({"b.csv": b"y"}))
    monkeypatch.setitem(sys.modules, "requests", rq)
    assert cvm.fetch_zip(2022, "ITR", cache_dir=str(tmp_path / "new")) == {"b.csv": b"y"}
    assert os.path.exists(tmp_path / "new" / "itr_cia_aberta_2022.zip")


def test_filed_panel_end_to_end_with_prefer_fallback(monkeypatch):
    monkeypatch.setattr(cvm, "fetch_zip", lambda y, d, c: _year_members(y))
    panel = cvm.filed_panel(2024, "DFP")
    r = panel[0]
    assert len(panel) == 1 and r["net_income"] == 10.0 and r["assets"] == 500.0 and r["equity"] == 50.0
    assert r["roe"] == 0.2 and r["filed_date"] == "2025-03-01"      # BPA came from the *_ind fallback
    monkeypatch.setattr(cvm, "fetch_zip", lambda y, d, c: _year_members(y, with_bpp=False))
    assert cvm.filed_panel(2024)[0]["equity"] is None


def test_cvm_main_prints_and_handles_failure(monkeypatch, capsys):
    rec = lambda cd, assets, roe: {"cd_cvm": cd, "filed_date": "2025-03-01", "fiscal_end": "2024-12-31",
                                   "assets": assets, "roe": roe, "name": "Co " + cd}
    monkeypatch.setattr(cvm, "filed_panel", lambda y, d: [rec("1", 10, 0.123), rec("2", 99, None)])
    pp = types.ModuleType("pit_panel")
    pp.as_of = lambda panel, asof, symbol_col, filed_col: {"x": 1}
    pp.leaks_lookahead = lambda panel, asof, filed_col: False
    monkeypatch.setitem(sys.modules, "pit_panel", pp)
    monkeypatch.setattr(sys, "argv", ["x", "2024", "itr"])
    cvm.main()
    out = capsys.readouterr().out
    assert "CVM ITR filed-date panel for 2024" in out and "companies with a dated filing: 2" in out
    assert out.index("Co 2") < out.index("Co 1")                          # largest assets first
    assert "ROE  12.3%" in out and "ROE   n/a" in out
    assert "pit_panel.as_of(2025-06-30) -> 1 companies" in out and "look-ahead leak: False" in out
    monkeypatch.setattr(cvm, "filed_panel", lambda y, d: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(sys, "argv", ["x"])
    cvm.main()
    assert "(fetch failed: offline)" in capsys.readouterr().out


# ───────────────────────── brazil_quality ─────────────────────────
def test_norm_cd_and_to_tickers():
    assert bq._norm_cd("009512") == "9512" and bq._norm_cd(" 0 ") == "0" and bq._norm_cd("000") == "0"
    out = bq.to_tickers({"009512": 0.2, "4170": 0.1, "99999": 0.5})
    assert out == {"PETR4.SA": 0.2, "VALE3.SA": 0.1}
    assert bq.to_tickers({"4170": 0.1, "9512": 0.2}, valid_tickers={"VALE3.SA"}) == {"VALE3.SA": 0.1}
    assert bq.to_tickers({"1": 3}, ticker_map={"001": "X.SA"}) == {"X.SA": 3}


def test_quality_asof_filters_implausible(monkeypatch):
    pp = types.ModuleType("pit_panel")
    seen = {}
    pp.as_of = lambda panel, asof, symbol_col, filed_col: (seen.update(a=(asof, symbol_col, filed_col)) or
                                                          {"1": {"roe": 0.2}, "2": {"roe": 5.0}, "3": {"roe": None},
                                                           "4": {"roe": -1.0}})
    monkeypatch.setitem(sys.modules, "pit_panel", pp)
    assert bq.quality_asof([], "2024-01-01") == {"1": 0.2, "4": -1.0}
    assert seen["a"] == ("2024-01-01", "cd_cvm", "filed_date")
    pp.as_of = lambda *a, **k: {"1": {"assets": 7.0}, "2": {"assets": None}}
    assert bq.quality_asof([], "d", quality_key="assets") == {"1": 7.0}          # plausibility only for roe


def _close(n=300):
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({"A": np.linspace(10, 20, n), "B": np.linspace(10, 10, n), "C": np.linspace(10, 5, n),
                         "D": np.nan}, index=idx)


def test_forward_returns():
    c = _close()
    f = bq.forward_returns(c, c.index[10], horizon_days=100)
    assert f["A"] == pytest.approx(c["A"].iloc[110] / c["A"].iloc[10] - 1)
    assert f["B"] == 0.0 and "D" not in f                                     # all-NaN ticker dropped
    last = bq.forward_returns(c, c.index[10])                                  # no horizon -> last bar
    assert last["C"] == pytest.approx(c["C"].iloc[-1] / c["C"].iloc[10] - 1)
    assert bq.forward_returns(c, "2099-01-01") == {}
    cap = bq.forward_returns(c, c.index[290], horizon_days=500)                # horizon past the end -> clipped
    assert cap["A"] == pytest.approx(c["A"].iloc[-1] / c["A"].iloc[290] - 1)
    c2 = c.copy(); c2.iloc[10, 0] = 0.0
    assert "A" not in bq.forward_returns(c2, c.index[10], horizon_days=5)      # non-positive start price dropped


def test_quality_quintiles():
    q = {f"T{i}": float(i) for i in range(50)}
    r = {f"T{i}": i / 100 for i in range(50)}
    out = bq.quality_quintiles(q, r)
    assert out["n"] == 50 and out["IC"] == pytest.approx(1.0, abs=1e-3)
    assert out["Q5_minus_Q1%"] == pytest.approx(out["Q5_ret%"] - out["Q1_ret%"], abs=0.01)
    assert out["Q1_ret%"] == pytest.approx(4.5) and out["Q5_ret%"] == pytest.approx(44.5)
    assert out["monotonicity"] == pytest.approx(1.0)
    assert bq.quality_quintiles({"a": 1.0}, {"a": 0.1}) == {"n": 1, "note": "too few names to bucket"}
    assert bq.quality_quintiles({"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0, "e": 5.0}, {"a": 0.1, "b": 0.2, "c": 0.3,
                                                                                       "d": 0.4, "e": 0.5})["n"] == 5
    assert bq.quality_quintiles({"a": 1.0}, {"z": 0.1})["n"] == 0                # no overlap


def test_bq_main_success_and_failure(monkeypatch, capsys):
    close = _close()
    close = close.drop(columns=["D"])
    close.columns = ["VALE3.SA", "PETR4.SA", "ITUB4.SA"]
    monkeypatch.setattr(cvm, "filed_panel", lambda y, d: ([] if y == 2023 else (_ for _ in ()).throw(RuntimeError("no 2024")))
                        if y != 2022 else [{"cd_cvm": "4170"}])
    md = types.ModuleType("marketdata_stub")
    import marketdata
    monkeypatch.setattr(marketdata, "close_volume", lambda m: (close, None))
    monkeypatch.setattr(bq, "quality_asof", lambda panel, asof: {"4170": 0.3, "9512": 0.2, "906": 0.1, "19348": 0.4,
                                                                  "1023": 0.5})
    monkeypatch.setattr(sys, "argv", ["bq", "2022", "2023", "2024"])
    bq.main()
    out = capsys.readouterr().out
    assert "(skip 2024: no 2024)" in out and "fundamentals: 1 filings over [2022, 2023, 2024]" in out
    assert "quality known as of 2024-01-01: 5 companies (3 mapped to liquid B3 tickers)" in out
    assert "point-in-time quality quintiles (6-month forward): {'n': 3, 'note': 'too few names to bucket'}" in out
    monkeypatch.setattr(marketdata, "close_volume", lambda m: (_ for _ in ()).throw(RuntimeError("no cache")))
    bq.main()
    assert "(live run unavailable: no cache)" in capsys.readouterr().out
