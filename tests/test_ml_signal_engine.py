"""ml_signal_engine.py: features, sequencing, per-stock prediction, batch + backtest evaluation."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ml_signal_engine as mse  # noqa: E402
from conftest_helpers import make_ohlcv  # noqa: E402


def test_z_score_normalise_columns_and_zero_std():
    X = np.array([[1.0, 5.0], [3.0, 5.0], [5.0, 5.0]])
    Z = mse.z_score_normalise(X)
    assert Z[:, 0].mean() == pytest.approx(0) and Z[:, 0].std() == pytest.approx(1)
    assert (Z[:, 1] == 0).all()                # constant column -> 0, not NaN
    assert not np.isnan(Z).any()


def test_compute_features_shape_and_ranges():
    f = mse.compute_features(make_ohlcv(400))
    assert list(f.columns) == mse.FEATURE_NAMES
    assert not f.isna().any().any()
    assert len(f) == 400 - 199                 # 200-DMA warm-up dropped
    assert f["rsi_14"].between(0, 100).all()
    assert (f["hl_ratio"] > 0).all()


def test_compute_features_zero_volume_rows_dropped():
    df = make_ohlcv(400)
    df.iloc[-1, df.columns.get_loc("Volume")] = 0.0
    f = mse.compute_features(df)
    assert df.index[-1] not in f.index         # zero volume -> NaN vol_ratio -> dropped


def test_make_sequences_shapes_and_alignment():
    eng = mse.MLSignalEngine()
    n = mse.LOOKBACK + mse.PREDICT_DAYS + 10
    feats = pd.DataFrame(np.random.default_rng(0).standard_normal((n, 12)), columns=mse.FEATURE_NAMES)
    tgt = pd.Series(np.arange(n, dtype=float))
    X, y = eng._make_sequences(feats, tgt)
    assert X.shape == (n - mse.LOOKBACK - mse.PREDICT_DAYS, mse.LOOKBACK * 12)
    assert y[0] == mse.LOOKBACK + mse.PREDICT_DAYS      # y[i] = target[i + PREDICT_DAYS]
    assert y[-1] == n - 1


def test_make_sequences_too_short_returns_empty():
    eng = mse.MLSignalEngine()
    feats = pd.DataFrame(np.zeros((mse.LOOKBACK + mse.PREDICT_DAYS, 12)), columns=mse.FEATURE_NAMES)
    X, y = eng._make_sequences(feats, pd.Series(np.zeros(len(feats))))
    assert X.size == 0 and y.size == 0


def test_make_model_types():
    from sklearn.linear_model import LinearRegression, Ridge
    assert isinstance(mse.MLSignalEngine("ridge")._make_model(), Ridge)
    assert isinstance(mse.MLSignalEngine("lr")._make_model(), LinearRegression)
    assert mse.MLSignalEngine("ridge")._make_model().alpha == 1.0


def test_make_model_without_sklearn(monkeypatch):
    monkeypatch.setattr(mse, "_SKLEARN_OK", False)
    assert mse.MLSignalEngine()._make_model() is None


def test_predict_insufficient_data_paths(monkeypatch):
    eng = mse.MLSignalEngine()
    assert eng.predict("X", None)["direction"] == "INSUFFICIENT_DATA"
    short = make_ohlcv(mse.MIN_ROWS - 1)
    r = eng.predict("X", short)
    assert r["direction"] == "INSUFFICIENT_DATA" and r["train_rows"] == 0 and r["model"] == "ridge"
    monkeypatch.setattr(mse, "_SKLEARN_OK", False)
    assert eng.predict("X", make_ohlcv(700))["direction"] == "INSUFFICIENT_DATA"


def test_predict_full_pipeline_fields():
    eng = mse.MLSignalEngine("ridge")
    r = eng.predict("SYM", make_ohlcv(700, seed=2))
    assert r["symbol"] == "SYM" and r["model"] == "ridge"
    assert r["direction"] in {"BULLISH", "NEUTRAL", "BEARISH"}
    assert r["features_used"] == 12 and r["train_rows"] >= 30
    assert 0 <= r["confidence"] <= 1 and r["train_rmse"] >= 0
    p = r["predicted_ret%"]
    expect = ("BULLISH" if p >= mse.BULLISH_THRESHOLD else
              "BEARISH" if p <= mse.BEARISH_THRESHOLD else "NEUTRAL")
    assert r["direction"] == expect


@pytest.mark.parametrize("pred,direction", [(0.5, "BULLISH"), (0.4999, "NEUTRAL"),
                                            (-0.5, "BEARISH"), (-0.4999, "NEUTRAL"), (0.0, "NEUTRAL")])
def test_predict_threshold_boundaries(monkeypatch, pred, direction):
    class FakeModel:
        def fit(self, X, y):
            pass

        def predict(self, X):
            return np.full(len(X), pred) + (np.arange(len(X)) * 0.0)
    eng = mse.MLSignalEngine()
    monkeypatch.setattr(eng, "_make_model", lambda: FakeModel())
    r = eng.predict("S", make_ohlcv(700, seed=3))
    assert r["direction"] == direction
    assert r["predicted_ret%"] == pytest.approx(round(pred, 3))
    assert 0 <= r["confidence"] <= 1


def test_predict_exception_is_captured(monkeypatch):
    eng = mse.MLSignalEngine()
    monkeypatch.setattr(mse, "compute_features", lambda d: (_ for _ in ()).throw(ValueError("bad")))
    r = eng.predict("S", make_ohlcv(700))
    assert r["error"] == "bad" and r["direction"] == "NEUTRAL"


def test_predict_few_aligned_rows_insufficient(monkeypatch):
    eng = mse.MLSignalEngine()
    monkeypatch.setattr(mse, "compute_features",
                        lambda d: pd.DataFrame(0.0, index=d.index[:20], columns=mse.FEATURE_NAMES))
    assert eng.predict("S", make_ohlcv(700))["direction"] == "INSUFFICIENT_DATA"


def test_predict_batch_sorted_and_error_wrapped(capsys):
    eng = mse.MLSignalEngine(workers=2)
    out = eng.predict_batch({"A": make_ohlcv(700, seed=1), "B": make_ohlcv(100), "C": None})
    assert set(out["symbol"]) == {"A", "B", "C"}
    assert out["predicted_ret%"].is_monotonic_decreasing
    assert (out.set_index("symbol").loc[["B", "C"], "direction"] == "INSUFFICIENT_DATA").all()
    text = capsys.readouterr().out
    assert "ML signals computed: 3 stocks" in text and "3/3 done" in text


def test_predict_batch_future_exception(monkeypatch):
    eng = mse.MLSignalEngine()
    monkeypatch.setattr(eng, "predict", lambda s, d: (_ for _ in ()).throw(RuntimeError("x")))
    out = eng.predict_batch({"A": make_ohlcv(50)}, max_workers=1)
    assert out.iloc[0]["direction"] == "ERROR" and out.iloc[0]["error"] == "x"


def test_predict_batch_empty():
    out = mse.MLSignalEngine().predict_batch({})
    assert out.empty


def test_evaluate_on_backtest_metrics():
    eng = mse.MLSignalEngine()
    df = make_ohlcv(700, seed=4)
    # pick a short test window inside the aligned range
    feats = mse.compute_features(df)
    start = feats.index[320]
    end = feats.index[330]
    r = eng.evaluate_on_backtest({"A": df, "SHORT": make_ohlcv(50), "NONE": None}, str(start.date()), str(end.date()))
    assert 1 <= r["n_predictions"] <= 11
    assert r["rmse"] >= r["mae"] >= 0
    assert 0 <= r["directional_acc"] <= 100
    assert r["test_period"] == f"{start.date()} – {end.date()}"


def test_evaluate_on_backtest_no_predictions():
    eng = mse.MLSignalEngine()
    assert eng.evaluate_on_backtest({"A": make_ohlcv(50)}, "2020-01-01", "2021-01-01") == {
        "error": "No test predictions generated"}


def test_evaluate_on_backtest_swallows_per_symbol_errors(monkeypatch):
    eng = mse.MLSignalEngine()
    monkeypatch.setattr(mse, "compute_features", lambda d: (_ for _ in ()).throw(KeyError("k")))
    assert "error" in eng.evaluate_on_backtest({"A": make_ohlcv(700)}, "2020-01-01", "2024-01-01")


def test_compare_models_table(capsys):
    t = mse.compare_models(make_ohlcv(700, seed=5), symbol="ZZ")
    assert list(t["Model"]) == ["LinearRegression", "Ridge(alpha=1)", "Naive(last_ret)"]
    assert (t[["Train_RMSE", "Train_MAE", "Test_RMSE", "Test_MAE"]] >= 0).all().all()
    # regularised model cannot fit train better than OLS
    assert t.iloc[0]["Train_RMSE"] <= t.iloc[1]["Train_RMSE"] + 1e-9
    assert "Model comparison for ZZ" in capsys.readouterr().out


def test_compare_models_short_or_no_sklearn(monkeypatch):
    assert mse.compare_models(make_ohlcv(100)).empty
    monkeypatch.setattr(mse, "_SKLEARN_OK", False)
    assert mse.compare_models(make_ohlcv(700)).empty


def test_compare_models_too_few_sequences(monkeypatch):
    monkeypatch.setattr(mse.MLSignalEngine, "_make_sequences",
                        lambda self, f, t: (np.zeros((10, 3)), np.zeros(10)))
    assert mse.compare_models(make_ohlcv(700)).empty
