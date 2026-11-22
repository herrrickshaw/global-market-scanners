"""benchmark.py: Ken French factor parsing/caching, Carhart alpha, quality validation and CLI (offline)."""
import io
import os
import sys
import types
import zipfile

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import benchmark as bm  # noqa: E402

FF = """This file was created by CMPT_ME_BEME
  random header line

,Mkt-RF,SMB,HML,RMW,CMA,RF
20240102,  1.00, 0.50,-0.20, 0.10, 0.05, 0.01
20240103, -99.99, 0.10, 0.20, 0.30, 0.40, 0.01
20240104,  2.00, bad, 0.20, 0.30, 0.40, 0.01
20240105,  0.50, 0.25, 0.10, 0.20, 0.30, 0.02

Annual Factors
20240101, 9, 9, 9, 9, 9, 9
"""

MOM = """header
,Mom
20240102, 0.40
20240103, 0.60
20240105, -99.99
"""


def test_parse_ff_csv_scales_masks_and_stops_at_block_end():
    df = bm.parse_ff_csv(FF)
    assert list(df.columns) == ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"]
    assert list(df.index.strftime("%Y%m%d")) == ["20240102", "20240103", "20240105"]   # bad row skipped, annual block cut
    assert df.loc["20240102", "Mkt-RF"] == pytest.approx(0.01)                         # percent -> decimal
    assert np.isnan(df.loc["20240103", "Mkt-RF"])                                      # -99.99 -> NaN
    assert df.loc["20240105", "RF"] == pytest.approx(0.0002)


def test_parse_ff_csv_mom_header_and_errors():
    m = bm.parse_ff_csv(MOM)
    assert list(m.columns) == ["Mom"] and np.isnan(m.loc["20240105", "Mom"]) and m.loc["20240102", "Mom"] == pytest.approx(0.004)
    with pytest.raises(ValueError, match="no factor header"):
        bm.parse_ff_csv("nothing here")


def _zip(text):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("data.csv", text.encode("latin-1"))
    return b.getvalue()


def _install_http(monkeypatch, responder):
    ac = types.ModuleType("apiclient")
    ac.http_get = responder
    monkeypatch.setitem(sys.modules, "apiclient", ac)


def test_download_member_ok_non200_and_error(monkeypatch, capsys):
    seen = []
    _install_http(monkeypatch, lambda src, url, headers=None, timeout=None, retries=None: (
        seen.append((src, url, timeout, retries)) or types.SimpleNamespace(status_code=200, content=_zip("hello"))))
    assert bm._download_member("f.zip") == "hello"
    assert seen == [("kenfrench", bm.KF_BASE + "f.zip", 30, 2)]
    _install_http(monkeypatch, lambda *a, **k: types.SimpleNamespace(status_code=404, content=b""))
    assert bm._download_member("f.zip") is None
    _install_http(monkeypatch, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    assert bm._download_member("f.zip") is None and "f.zip unavailable: offline" in capsys.readouterr().err


@pytest.fixture
def fcache(monkeypatch, tmp_path):
    monkeypatch.setattr(bm, "CACHE_DIR", str(tmp_path / "kfc"))
    store = {}
    monkeypatch.setattr(pd.DataFrame, "to_parquet", lambda self, p, **k: store.__setitem__(p, self.copy()))
    monkeypatch.setattr(bm.pd, "read_parquet", lambda p: store[p])
    monkeypatch.setattr(bm.os.path, "exists", lambda p, _e=os.path.exists: p in store or _e(p))
    return store


def test_factors_unknown_region(fcache):
    assert bm.factors("Atlantis") is None


def test_factors_downloads_joins_mom_and_caches(monkeypatch, fcache):
    calls = []

    def dl(name):
        calls.append(name)
        return FF if "5_Factors" in name else MOM
    monkeypatch.setattr(bm, "_download_member", dl)
    df = bm.factors("North America")
    assert list(df.columns) == ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF", "Mom"]
    assert df.loc["20240102", "Mom"] == pytest.approx(0.004) and np.isnan(df.loc["20240105", "Mom"])
    assert len(calls) == 2
    cache = os.path.join(bm.CACHE_DIR, "kf_North_America.parquet")
    assert cache in fcache
    assert bm.factors("North America") is fcache[cache] and len(calls) == 2           # served from cache
    bm.factors("North America", use_cache=False)
    assert len(calls) == 4                                                              # use_cache=False refetches


def test_factors_without_mom_and_failed_ff5(monkeypatch, fcache):
    monkeypatch.setattr(bm, "_download_member", lambda n: FF if "5_Factors" in n else None)
    assert "Mom" not in bm.factors("Europe").columns
    monkeypatch.setattr(bm, "_download_member", lambda n: None)
    assert bm.factors("Japan") is None
    monkeypatch.setattr(bm, "_download_member", lambda n: FF if "5_Factors" in n else "garbage no header")
    assert "Mom" not in bm.factors("Emerging").columns                                  # bad mom file swallowed


def _factor_frame(n=500, seed=0, with_mom=True):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-01-03", periods=n, freq="B")
    d = {"Mkt-RF": rng.normal(0, 0.01, n), "SMB": rng.normal(0, 0.005, n), "HML": rng.normal(0, 0.005, n),
         "RF": np.full(n, 0.0001)}
    if with_mom:
        d["Mom"] = rng.normal(0, 0.005, n)
    return pd.DataFrame(d, index=idx)


def test_carhart_alpha_recovers_alpha_and_loadings():
    fac = _factor_frame(600)
    rng = np.random.default_rng(5)
    y = 0.0005 + 1.2 * fac["Mkt-RF"] - 0.5 * fac["HML"] + 0.3 * fac["Mom"] + rng.normal(0, 0.0005, len(fac))
    r = bm.carhart_alpha(y, fac)
    assert r["n"] == 600
    assert r["alpha_daily"] == pytest.approx(0.0005, abs=1e-4)
    assert r["alpha_monthly%"] == round(r["alpha_daily"] * 21 * 100, 3)
    assert r["loadings"]["Mkt-RF"][0] == pytest.approx(1.2, abs=0.05) and r["loadings"]["HML"][0] == pytest.approx(-0.5, abs=0.1)
    assert set(r["loadings"]) == {"Mkt-RF", "SMB", "HML", "Mom"} and r["alpha_reliable"] is True
    assert r["R2"] > 0.9 and abs(r["alpha_t"]) > 5


def test_carhart_alpha_without_mom_and_short_window():
    fac = _factor_frame(450, with_mom=False)
    y = fac["Mkt-RF"] * 1.0
    r = bm.carhart_alpha(y, fac)
    assert set(r["loadings"]) == {"Mkt-RF", "SMB", "HML"}
    short = bm.carhart_alpha(y.iloc[:30], fac)
    assert short["alpha_reliable"] is False and short["n"] == 30                      # exactly 30 rows is allowed
    assert bm.carhart_alpha(y.iloc[:29], fac) == {"n": 29, "alpha_daily": None}
    assert bm.carhart_alpha(y.iloc[:399], fac)["alpha_reliable"] is False
    assert bm.carhart_alpha(y.iloc[:400], fac)["alpha_reliable"] is True


def test_factor_premia():
    idx = pd.date_range("2024-01-01", periods=4)
    fac = pd.DataFrame({"A": [0.01, 0.01, 0.01, 0.01], "B": [0.01, -0.01, 0.02, -0.02], "C": [np.nan, 0.0, 0.0, 0.0]}, index=idx)
    p = bm.factor_premia(fac).set_index("factor")
    assert p.loc["A", "ann_mean%"] == 252.0 and p.loc["A", "ann_vol%"] == 0.0 and np.isnan(p.loc["A", "sharpe"])
    s = fac["B"]
    assert p.loc["B", "ann_vol%"] == round(s.std() * np.sqrt(252) * 100, 2)
    assert p.loc["B", "sharpe"] == round(s.mean() * 252 / (s.std() * np.sqrt(252)), 2)
    assert p.loc["C", "n_days"] == 3 and p.loc["C", "from"] == "2024-01-02" and p.loc["C", "to"] == "2024-01-04"


def test_clean():
    assert bm._clean("tcs.ns") == "TCS" and bm._clean(123) == "123"


def _install_qf(monkeypatch, scored=None, fundamentals_empty=False):
    qf = types.ModuleType("quality_factor")
    qf.load_fundamentals = lambda ms: pd.DataFrame() if fundamentals_empty else pd.DataFrame({"ticker": ["A", "B"]})
    qf.attach_price_risk = lambda f: f
    qf.score_universe = lambda f, by_market=True: scored if scored is not None else pd.DataFrame(
        {"ticker": ["a.NS", "b.NS", "c.NS", "x"], "quality": [1.0, 2.0, 3.0, 0.0]})
    qf.assign_deciles = lambda s: pd.Series(["quality", "quality", "quality", "junk"][:len(s)], index=s.index)
    monkeypatch.setitem(sys.modules, "quality_factor", qf)


def _px(symbols, n=10):
    rows = []
    for s in symbols:
        for i in range(n):
            rows.append({"Symbol": s, "Date": f"2024-01-{i+1:02d}", "Close": 100.0 + i * (1 if s != "B" else 2)})
    return pd.DataFrame(rows)


def test_quality_lq_returns(monkeypatch, tmp_path):
    _install_qf(monkeypatch)
    monkeypatch.setattr(bm, "SEED", str(tmp_path))
    (tmp_path / "cleaned_long_IN.parquet").write_bytes(b"x")
    monkeypatch.setattr(bm.pd, "read_parquet", lambda p: _px(["A.NS", "B.NS", "C.NS", "X"]))
    r = bm.quality_lq_returns("IN")
    assert r.index.is_monotonic_increasing and len(r) == 9 and r.index.dtype.kind == "M"
    # 'X' (junk decile) excluded from the equal-weight mean
    manual = _px(["A.NS", "B.NS", "C.NS"]).pivot_table(index="Date", columns="Symbol", values="Close").pct_change().mean(axis=1).dropna()
    assert list(r.values) == pytest.approx(list(manual.values))


def test_quality_lq_returns_none_paths(monkeypatch, tmp_path):
    _install_qf(monkeypatch, fundamentals_empty=True)
    assert bm.quality_lq_returns("IN") is None
    _install_qf(monkeypatch, scored=pd.DataFrame({"ticker": ["a"], "quality": [np.nan]}))
    assert bm.quality_lq_returns("IN") is None
    _install_qf(monkeypatch)
    monkeypatch.setattr(bm, "SEED", str(tmp_path))
    assert bm.quality_lq_returns("IN") is None                                         # parquet missing
    (tmp_path / "cleaned_long_IN.parquet").write_bytes(b"x")
    monkeypatch.setattr(bm.pd, "read_parquet", lambda p: _px(["A.NS", "B.NS"]))
    assert bm.quality_lq_returns("IN") is None                                         # fewer than 3 columns


def test_validate_quality_paths(monkeypatch):
    assert bm.validate_quality("ZZ") == {"error": "no Ken French region for ZZ"}
    monkeypatch.setattr(bm, "factors", lambda region: None)
    assert "unavailable" in bm.validate_quality("US")["error"]
    fac = _factor_frame(500)
    monkeypatch.setattr(bm, "factors", lambda region: fac)
    monkeypatch.setattr(bm, "quality_lq_returns", lambda m: None)
    assert bm.validate_quality("US") == {"error": "could not form LQ portfolio for US"}
    rng = np.random.default_rng(1)
    port = pd.Series(0.0003 + fac["Mkt-RF"] + fac["RF"] + rng.normal(0, 0.0005, len(fac)), index=fac.index)
    monkeypatch.setattr(bm, "quality_lq_returns", lambda m: port)
    r = bm.validate_quality("us")
    assert r["market"] == "us" and r["region"] == "North America" and r["n"] == 500
    assert r["loadings"]["Mkt-RF"][0] == pytest.approx(1.0, abs=0.1)


def _main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["benchmark.py", *argv])
    bm.main()


def test_main_validate_quality_output_variants(monkeypatch, capsys):
    base = {"region": "North America", "n": 500, "R2": 0.9, "alpha_daily": 0.0004, "alpha_t": 3.0, "alpha_reliable": True,
            "loadings": {"Mkt-RF": (1.0, 20.0), "HML": (-0.3, -3.1), "Mom": (0.2, 1.0)}}
    monkeypatch.setattr(bm, "validate_quality", lambda m: dict(base))
    _main(monkeypatch, "--validate-quality")
    out = capsys.readouterr().out
    assert "QUALITY (LQ) ALPHA vs real Ken French factors — US" in out
    assert "HML" in out and "(t=-3.1) *" in out and "(t=1.0)" in out and "(t=20.0) *" in out
    assert "HML − , Mom +  (matches the paper" in out and "[UNRELIABLE" not in out
    assert "0.040%/day" in out
    bad = dict(base, loadings={"HML": (0.3, 1.0), "Mom": (-0.2, 1.0)}, alpha_reliable=False)
    monkeypatch.setattr(bm, "validate_quality", lambda m: bad)
    _main(monkeypatch, "--validate-quality", "--market", "JP")
    out = capsys.readouterr().out
    assert "— JP ===" in out and "HML + , Mom −  (differs from the paper" in out and "[UNRELIABLE" in out
    monkeypatch.setattr(bm, "validate_quality", lambda m: {"error": "no data"})
    _main(monkeypatch, "--validate-quality")
    assert "  no data" in capsys.readouterr().out
    monkeypatch.setattr(bm, "validate_quality", lambda m: {"n": 12, "alpha_daily": None})
    _main(monkeypatch, "--validate-quality")
    assert "too few overlapping days (12)" in capsys.readouterr().out


def test_main_validate_missing_loadings_defaults(monkeypatch, capsys):
    monkeypatch.setattr(bm, "validate_quality", lambda m: {"region": "R", "n": 40, "R2": 0.1, "alpha_daily": 0.0,
                                                           "alpha_t": 0.0, "alpha_reliable": True, "loadings": {}})
    _main(monkeypatch, "--validate-quality")
    assert "HML + , Mom −" in capsys.readouterr().out                                  # absent loadings -> 0 -> '+' / '−'


def test_main_premia_region_resolution(monkeypatch, capsys):
    seen = []
    fac = _factor_frame(300)
    monkeypatch.setattr(bm, "factors", lambda region, use_cache=True: (seen.append((region, use_cache)) or fac))
    _main(monkeypatch)
    _main(monkeypatch, "--market", "jp", "--no-cache")
    _main(monkeypatch, "--region", "Europe", "--market", "JP")
    assert seen == [("North America", True), ("Japan", False), ("Europe", True)]
    out = capsys.readouterr().out
    assert "real Ken French factor premia — North America" in out and "Kenneth French Data Library" in out
    assert "Mkt-RF" in out
    monkeypatch.setattr(bm, "factors", lambda region, use_cache=True: None)
    _main(monkeypatch)
    assert "factors for 'North America' unavailable" in capsys.readouterr().out
    _main(monkeypatch, "--market", "XX")
    assert "factors for 'North America' unavailable" in capsys.readouterr().out       # unknown market defaults to NA
