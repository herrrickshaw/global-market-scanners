"""ml_viability.py: walk-forward ML screen viability (cache + process pool faked)."""
import os
import sys
import types

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ml_viability as mv  # noqa: E402
from conftest_helpers import make_ohlcv  # noqa: E402


@pytest.fixture(autouse=True)
def _no_parquet_cache(monkeypatch):
    monkeypatch.setattr(mv.feature_cache, "cached_features", lambda t, df, fn: fn(df))


def test_min_needed_constant():
    assert mv.MIN_NEEDED == 60 + 252 + 5 + 250


def test_download_ohlc_filters_short_and_none(monkeypatch):
    ms = types.ModuleType("market_store")
    seen = {}

    def cached_download(tickers, years):
        seen["a"] = (tickers, years)
        return {"OK": make_ohlcv(mv.MIN_NEEDED + 1), "EDGE": make_ohlcv(mv.MIN_NEEDED),
                "SHORT": make_ohlcv(10), "NONE": None}

    ms.cached_download = cached_download
    monkeypatch.setitem(sys.modules, "market_store", ms)
    out = mv.download_ohlc(["OK", "EDGE", "SHORT", "NONE"], 3)
    assert set(out) == {"OK"}                       # len must be strictly > MIN_NEEDED
    assert seen["a"] == (["OK", "EDGE", "SHORT", "NONE"], 3)


def test_eval_ticker_wf_counts_are_consistent():
    df = make_ohlcv(700, seed=1)
    nc, nt, act, pred, bull, allr, bh, bn = mv._eval_ticker_wf(("T", df, 40, "ridge"))
    assert nt == len(act) == len(pred) == len(allr) > 0
    assert 0 <= nc <= nt and bn == len(bull) and 0 <= bh <= bn
    assert act == allr
    assert bh == sum(1 for a in bull if a > 0)
    # number of test points follows range(TRAIN_WINDOW+LOOKBACK, n-PREDICT, step)
    from ml_signal_engine import compute_features, PREDICT_DAYS, TRAIN_WINDOW, LOOKBACK
    feats = compute_features(df)
    n = len(feats) - PREDICT_DAYS            # aligned drops the last PREDICT_DAYS target NaNs
    expected = len(range(TRAIN_WINDOW + LOOKBACK, n - PREDICT_DAYS, 40))
    assert nt == expected


def test_eval_ticker_wf_step_changes_sample_count():
    df = make_ohlcv(700, seed=1)
    a = mv._eval_ticker_wf(("T", df, 40, "ridge"))[1]
    b = mv._eval_ticker_wf(("T", df, 80, "ridge"))[1]
    assert a > b > 0


def test_eval_ticker_wf_short_and_error_return_empty():
    empty = (0, 0, [], [], [], [], 0, 0)
    assert mv._eval_ticker_wf(("T", make_ohlcv(300), 5, "ridge")) == empty
    assert mv._eval_ticker_wf(("T", None, 5, "ridge")) == empty       # exception swallowed


class _Inline:
    def __init__(self, max_workers=None):
        self.max_workers = max_workers

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def map(self, fn, it):
        return map(fn, it)


def _patch_pool(monkeypatch, results):
    import concurrent.futures as cf
    monkeypatch.setattr(cf, "ProcessPoolExecutor", _Inline)
    monkeypatch.setattr(mv, "_eval_ticker_wf", lambda args: results[args[0]])


def test_evaluate_market_aggregates(monkeypatch):
    # ticker A: 4 preds; ticker B: 2 preds
    A = (3, 4, [1.0, -1.0, 2.0, -2.0], [0.6, -0.6, 0.7, 0.4], [1.0, 2.0], [1.0, -1.0, 2.0, -2.0], 2, 2)
    B = (1, 2, [3.0, -1.0], [0.9, 0.2], [3.0], [3.0, -1.0], 1, 1)
    _patch_pool(monkeypatch, {"A": A, "B": B})
    eng = types.SimpleNamespace(model_type="ridge")
    r = mv.evaluate_market("MK", {"A": 1, "B": 2}, eng, 5, workers=1)
    assert r["Market"] == "MK" and r["n_stocks"] == 2 and r["n_preds"] == 6
    assert r["dir_acc%"] == round(4 / 6 * 100, 1)
    base = np.mean([1.0, -1.0, 2.0, -2.0, 3.0, -1.0])
    bull = np.mean([1.0, 2.0, 3.0])
    assert r["base_fwd%"] == round(base, 3) and r["bull_fwd%"] == round(bull, 3)
    assert r["edge%"] == round(bull - base, 3)
    assert r["bull_hit%"] == 100.0 and r["bull_n"] == 3
    act = np.array([1.0, -1.0, 2.0, -2.0, 3.0, -1.0]); pr = np.array([0.6, -0.6, 0.7, 0.4, 0.9, 0.2])
    assert r["rmse"] == round(float(np.sqrt(np.mean((act - pr) ** 2))), 3)
    assert r["mae"] == round(float(np.mean(np.abs(act - pr))), 3)
    assert r["VIABLE"] == "YES"


@pytest.mark.parametrize("edge_neg,hit,dacc,expected", [
    (False, (1, 2), (3, 4), "no"),     # bull hit exactly 50% -> not > 50
    (False, (2, 2), (2, 4), "no"),     # dir acc exactly 50% -> not > 50
    (True, (2, 2), (4, 4), "no"),      # negative edge
    (False, (2, 2), (3, 4), "YES"),
])
def test_evaluate_market_viable_requires_all_three(monkeypatch, edge_neg, hit, dacc, expected):
    bull_rets = [-5.0, -5.0] if edge_neg else [5.0, 5.0]
    bh, bn = hit
    nc, nt = dacc
    res = (nc, nt, [1.0] * nt, [1.0] * nt, bull_rets[:bn] if bn == 2 else [5.0, -5.0][:bn], [0.0] * nt, bh, bn)
    if edge_neg:
        res = (nc, nt, [1.0] * nt, [1.0] * nt, bull_rets, [0.0] * nt, bh, bn)
    _patch_pool(monkeypatch, {"A": res})
    r = mv.evaluate_market("M", {"A": 1}, types.SimpleNamespace(model_type="lr"), 5)
    assert r["VIABLE"] == expected


def test_evaluate_market_no_predictions(monkeypatch):
    _patch_pool(monkeypatch, {"A": (0, 0, [], [], [], [], 0, 0)})
    r = mv.evaluate_market("M", {"A": 1}, types.SimpleNamespace(model_type="ridge"), 5)
    assert r == {"Market": "M", "n_stocks": 1, "n_preds": 0, "VIABLE": "n/a"}


def test_evaluate_market_no_bull_calls(monkeypatch):
    _patch_pool(monkeypatch, {"A": (1, 2, [1.0, 2.0], [0.1, 0.1], [], [1.0, 2.0], 0, 0)})
    r = mv.evaluate_market("M", {"A": 1}, types.SimpleNamespace(model_type="ridge"), 5)
    assert r["bull_fwd%"] == 0.0 and r["bull_hit%"] == 0.0 and r["bull_n"] == 0 and r["VIABLE"] == "no"


def _run_main(monkeypatch, tmp_path, *argv):
    out = str(tmp_path / "res.xlsx")
    monkeypatch.setattr(sys, "argv", ["ml_viability.py", "--out", out, *argv])
    mv.main()
    return out


def test_main_runs_markets_and_writes_csv_fallback(monkeypatch, tmp_path, capsys):
    got = {}
    monkeypatch.setattr(mv, "download_ohlc", lambda t, y: got.setdefault(tuple(t), {"x": 1}) and {"x": 1})
    monkeypatch.setattr(mv, "evaluate_market",
                        lambda name, ohlc, eng, step, workers: {"Market": name, "n_preds": step, "VIABLE": "no"})
    monkeypatch.setattr(pd.DataFrame, "to_excel", lambda self, p, **kw: (_ for _ in ()).throw(ImportError("no openpyxl")))
    out = _run_main(monkeypatch, tmp_path, "--markets", "US", "Atlantis", "--top", "2", "--step", "7", "--workers", "1")
    cap = capsys.readouterr()
    assert ("AAPL", "MSFT") in got                        # --top 2 truncates the universe
    assert "[skip] unknown market Atlantis" in cap.err
    assert "downloading 2 tickers (5y)" in cap.err
    csv = out.replace(".xlsx", ".csv")
    df = pd.read_csv(csv)
    assert list(df["Market"]) == ["US"] and list(df["n_preds"]) == [7]
    assert "5-YEAR ML SCREEN VIABILITY BY MARKET" in cap.out and "VIABLE = edge>0" in cap.out


def test_main_excel_success_writes_no_csv(monkeypatch, tmp_path):
    monkeypatch.setattr(mv, "download_ohlc", lambda t, y: {"x": 1})
    monkeypatch.setattr(mv, "evaluate_market", lambda *a, **k: {"Market": "US"})
    saved = []
    monkeypatch.setattr(pd.DataFrame, "to_excel", lambda self, p, **kw: saved.append(p))
    out = _run_main(monkeypatch, tmp_path, "--markets", "US", "--model", "lr")
    assert saved == [out] and not os.path.exists(out.replace(".xlsx", ".csv"))
