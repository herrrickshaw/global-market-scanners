# ml_signal_engine.py
# ====================
# ML-based directional signal for individual stocks.
#
# PAPER SOURCE
# ────────────
# AlQahtani et al. (IJACSA 2025) — "Comprehensive Analysis of Machine and
# Deep Learning Models for Stock Market Prediction"
#
# Key findings applied here:
#   1. Linear Regression outperformed LSTM and RNN on financial data
#      (test RMSE: LR 0.304 < LSTM 0.346 < RNN 0.415)
#      → Use Linear Regression as primary predictor (not LSTM)
#
#   2. Z-score normalization (Z = (X - μ) / σ) across all features
#      → Applied per feature over a rolling 252-day (1-year) window
#
#   3. Sliding window temporal sequencing
#      → 60-day lookback window → feature matrix (n_samples, 60 × n_features)
#
#   4. Nonstationarity: models must be periodically retrained
#      → Rolling retraining: model is retrained on most recent 252 days
#        before each prediction (walk-forward, no lookahead)
#
#   5. Multivariate CNN-LSTM (RMSE 0.0162) is best DL option
#      → Implemented as optional upgrade path; Linear Regression is default
#
#   6. Features beyond raw OHLC: technical indicators improve accuracy
#      → RSI(14), MACD(12,26,9), Bollinger Bands(20,2), DMA50, DMA200
#
# ML SIGNAL USAGE
# ───────────────
# The ML signal adds a DIRECTIONAL BIAS layer on top of screeners:
#   BULLISH  (predicted_return > +0.5%) → strengthens Darvas/screener signals
#   NEUTRAL  (-0.5% to +0.5%)          → no change in screener weight
#   BEARISH  (predicted_return < -0.5%) → weakens screener signals
#
# Integration with existing system:
#   screener_analysis.py    → add ML_Signal column alongside each screener result
#   full_indian_market_scan → add ML_Signal to All_Stocks and Triple_Hits sheets
#   walk_forward_backtest   → ML_Signal as an additional filter in the strategy matrix
#
# USAGE
# ─────
#   from ml_signal_engine import MLSignalEngine
#   engine = MLSignalEngine()
#
#   # Single stock
#   sig = engine.predict("RELIANCE.NS", df_ohlc)
#   # sig = {"symbol": "RELIANCE.NS", "direction": "BULLISH",
#   #        "predicted_ret%": 1.2, "confidence": 0.73, "model": "LR"}
#
#   # Batch (parallel)
#   signals = engine.predict_batch(ohlc_dict)  # ohlc_dict from MarketCache
#
# Install:
#   pip install scikit-learn pandas numpy  (no TensorFlow needed for LR model)

import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

try:
    from sklearn.linear_model import LinearRegression, Ridge
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import mean_squared_error, mean_absolute_error
    _SKLEARN_OK = True
except ImportError:
    _SKLEARN_OK = False
    print("⚠️  scikit-learn not installed: pip install scikit-learn")

# ── Constants from paper ──────────────────────────────────────────────────────

LOOKBACK      = 60    # sliding window size (days) — paper uses temporal sequencing
TRAIN_WINDOW  = 252   # rolling retraining window (1 year) — handles nonstationarity
PREDICT_DAYS  = 5     # predict return T+5d ahead (1 trading week)
MIN_ROWS      = LOOKBACK + TRAIN_WINDOW + PREDICT_DAYS + 10  # minimum data needed

# Signal thresholds
BULLISH_THRESHOLD = 0.5   # predicted return > +0.5% → BULLISH
BEARISH_THRESHOLD = -0.5  # predicted return < -0.5% → BEARISH

# Feature names (computed from OHLC)
FEATURE_NAMES = [
    "ret_1d",      # 1-day return
    "ret_5d",      # 5-day return
    "vol_20d",     # 20-day rolling volatility
    "rsi_14",      # RSI(14)
    "macd",        # MACD line
    "macd_signal", # MACD signal line
    "bb_pct",      # Bollinger Band %B position
    "dma50_gap",   # % gap from 50 DMA
    "dma200_gap",  # % gap from 200 DMA
    "vol_ratio",   # volume vs 20-day average
    "hl_ratio",    # (High - Low) / Close — intraday range as volatility proxy
    "close_norm",  # Z-score normalized close vs 20-day window
]


# ════════════════════════════════════════════════════════════════════════════════
# FEATURE ENGINEERING
# ════════════════════════════════════════════════════════════════════════════════

def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute technical indicator features from OHLC.

    AlQahtani et al. (2025) methodology:
    - Use historical price, volume, and technical indicators as features
    - Z-score normalisation applied per feature (done separately in rolling window)
    - These features capture: momentum (RSI, MACD), volatility (BB, vol),
      trend (DMA gaps), and price action (HL ratio)
    """
    d = pd.DataFrame(index=df.index)
    close = df["Close"].astype(float)
    high  = df["High"].astype(float)
    low   = df["Low"].astype(float)
    vol   = df["Volume"].astype(float).replace(0, np.nan)

    # ── Returns ───────────────────────────────────────────────────────────────
    d["ret_1d"]  = close.pct_change(1)   * 100
    d["ret_5d"]  = close.pct_change(5)   * 100
    d["vol_20d"] = close.pct_change().rolling(20).std() * np.sqrt(252) * 100

    # ── RSI (14) ──────────────────────────────────────────────────────────────
    delta  = close.diff()
    gain   = delta.clip(lower=0).rolling(14).mean()
    loss   = (-delta.clip(upper=0)).rolling(14).mean()
    rs     = gain / loss.replace(0, np.nan)
    d["rsi_14"] = 100 - 100 / (1 + rs)

    # ── MACD (12, 26, 9) ─────────────────────────────────────────────────────
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd  = ema12 - ema26
    d["macd"]        = macd
    d["macd_signal"] = macd.ewm(span=9, adjust=False).mean()

    # ── Bollinger Bands (20, 2σ) — %B position ───────────────────────────────
    bb_mid   = close.rolling(20).mean()
    bb_std   = close.rolling(20).std()
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    bb_range = (bb_upper - bb_lower).replace(0, np.nan)
    d["bb_pct"] = (close - bb_lower) / bb_range   # 0 = at lower band, 1 = at upper

    # ── Moving average gaps ───────────────────────────────────────────────────
    dma50  = close.rolling(50).mean()
    dma200 = close.rolling(200).mean()
    d["dma50_gap"]  = (close - dma50)  / dma50  * 100
    d["dma200_gap"] = (close - dma200) / dma200 * 100

    # ── Volume ratio ─────────────────────────────────────────────────────────
    vol_avg = vol.rolling(20).mean()
    d["vol_ratio"] = vol / vol_avg.replace(0, np.nan)

    # ── Intraday range ────────────────────────────────────────────────────────
    d["hl_ratio"] = (high - low) / close.replace(0, np.nan)

    # ── Z-score of close vs 20-day window ────────────────────────────────────
    close_mean = close.rolling(20).mean()
    close_std  = close.rolling(20).std().replace(0, np.nan)
    d["close_norm"] = (close - close_mean) / close_std

    return d[FEATURE_NAMES].dropna()


def z_score_normalise(X: np.ndarray) -> np.ndarray:
    """
    Z-score normalisation: Z = (X - μ) / σ
    Applied per feature column as specified in AlQahtani et al. (2025).
    """
    mean = X.mean(axis=0)
    std  = X.std(axis=0)
    std[std == 0] = 1  # avoid division by zero
    return (X - mean) / std


# ════════════════════════════════════════════════════════════════════════════════
# ML SIGNAL ENGINE
# ════════════════════════════════════════════════════════════════════════════════

class MLSignalEngine:
    """
    Walk-forward ML signal engine based on AlQahtani et al. (2025).

    For each stock:
      1. Compute 12 technical features from OHLC
      2. Create sliding window sequences (60-day lookback)
      3. Train Linear Regression on rolling 252-day window (handles nonstationarity)
      4. Predict T+5d return for the most recent window
      5. Classify: BULLISH / NEUTRAL / BEARISH based on predicted return

    Linear Regression was chosen because the paper conclusively showed
    LR outperforms LSTM and RNN on financial data:
      LR test RMSE = 0.304 (vs LSTM 0.346, RNN 0.415)
      "Simpler models can capture stock movements which seem to be relatively linear"
    """

    def __init__(self, model_type: str = "ridge", workers: int = 4):
        """
        model_type: 'lr'    → LinearRegression (paper baseline, RMSE 0.304)
                    'ridge' → Ridge regression (LR + L2 regularisation, prevents overfitting)
        """
        self.model_type = model_type
        self.workers    = workers
        self._model_cache: dict = {}  # {symbol: (model, scaler, last_trained)}

    def _make_model(self):
        """Return a fresh model instance."""
        if not _SKLEARN_OK:
            return None
        if self.model_type == "ridge":
            return Ridge(alpha=1.0, fit_intercept=True)
        return LinearRegression(fit_intercept=True)

    def _make_sequences(self, features: pd.DataFrame,
                        target: pd.Series) -> tuple:
        """
        Convert feature time series into (X_seq, y) arrays for regression.

        Sliding window approach (AlQahtani et al. 2025):
          For each row t in [LOOKBACK, N - PREDICT_DAYS]:
            X[t] = features[t - LOOKBACK : t].flatten()   (LOOKBACK × n_features)
            y[t] = target[t + PREDICT_DAYS]               (return PREDICT_DAYS ahead)

        This creates a regression problem: predict future return from
        the last LOOKBACK days of feature values.
        """
        feat_arr = features.values
        tgt_arr  = target.values
        n_feat   = feat_arr.shape[1]

        X_list, y_list = [], []
        for i in range(LOOKBACK, len(feat_arr) - PREDICT_DAYS):
            window  = feat_arr[i - LOOKBACK : i]          # shape (LOOKBACK, n_features)
            z_win   = z_score_normalise(window)            # Z-score per AlQahtani et al.
            X_list.append(z_win.flatten())                 # flatten → 1D feature vector
            y_list.append(tgt_arr[i + PREDICT_DAYS])

        if not X_list:
            return np.array([]), np.array([])
        return np.array(X_list), np.array(y_list)

    def predict(self, symbol: str, df_ohlc: pd.DataFrame) -> dict:
        """
        Run the full ML pipeline for one stock and return a signal dict.

        Walk-forward retraining (nonstationarity handling):
          The model is trained ONLY on the most recent TRAIN_WINDOW days
          before the prediction point. This prevents the model from learning
          on outdated market regimes (AlQahtani et al. 2025 recommendation).

        Returns:
          {
            "symbol":          "RELIANCE.NS",
            "direction":       "BULLISH" / "NEUTRAL" / "BEARISH",
            "predicted_ret%":  1.24,     # predicted 5-day return
            "confidence":      0.68,     # |predicted| / std(train_predictions)
            "model":           "ridge",
            "train_rmse":      0.42,
            "features_used":   12,
            "train_rows":      192,
          }
        """
        base = {"symbol": symbol, "direction": "NEUTRAL", "predicted_ret%": 0.0,
                "confidence": 0.0, "model": self.model_type,
                "train_rmse": None, "train_rows": 0}

        if not _SKLEARN_OK or df_ohlc is None or len(df_ohlc) < MIN_ROWS:
            base["direction"] = "INSUFFICIENT_DATA"
            return base

        try:
            # ── Feature engineering ───────────────────────────────────────────
            features = compute_features(df_ohlc)
            if len(features) < MIN_ROWS - 50:
                base["direction"] = "INSUFFICIENT_DATA"
                return base

            # Target: T+PREDICT_DAYS forward return (%)
            close  = df_ohlc["Close"].astype(float).reindex(features.index)
            target = close.pct_change(PREDICT_DAYS).shift(-PREDICT_DAYS) * 100

            # Align features and target
            aligned = features.join(target.rename("target"), how="inner").dropna()
            if len(aligned) < LOOKBACK + 50:
                base["direction"] = "INSUFFICIENT_DATA"
                return base

            feat_df = aligned[FEATURE_NAMES]
            tgt_ser = aligned["target"]

            # ── Rolling train on most recent TRAIN_WINDOW rows ────────────────
            # Only use last TRAIN_WINDOW rows for training (walk-forward)
            train_end  = len(aligned) - PREDICT_DAYS - 1
            train_start = max(0, train_end - TRAIN_WINDOW)
            train_feat = feat_df.iloc[train_start:train_end]
            train_tgt  = tgt_ser.iloc[train_start:train_end]

            X_train, y_train = self._make_sequences(train_feat, train_tgt)
            if len(X_train) < 30:
                base["direction"] = "INSUFFICIENT_DATA"
                return base

            # ── Train model ───────────────────────────────────────────────────
            model = self._make_model()
            model.fit(X_train, y_train)

            # In-sample RMSE (training error)
            y_pred_train = model.predict(X_train)
            train_rmse   = float(np.sqrt(mean_squared_error(y_train, y_pred_train)))

            # ── Predict: use most recent LOOKBACK window ──────────────────────
            latest_window = feat_df.iloc[-LOOKBACK:].values
            z_window      = z_score_normalise(latest_window).flatten().reshape(1, -1)
            predicted_ret = float(model.predict(z_window)[0])

            # ── Confidence: how large vs typical prediction variance ───────────
            pred_std  = float(y_pred_train.std()) if y_pred_train.std() > 0 else 1.0
            confidence = min(1.0, abs(predicted_ret) / (2 * pred_std + 1e-9))

            # ── Direction classification ───────────────────────────────────────
            if predicted_ret >= BULLISH_THRESHOLD:
                direction = "BULLISH"
            elif predicted_ret <= BEARISH_THRESHOLD:
                direction = "BEARISH"
            else:
                direction = "NEUTRAL"

            return {
                "symbol":         symbol,
                "direction":      direction,
                "predicted_ret%": round(predicted_ret, 3),
                "confidence":     round(confidence, 3),
                "model":          self.model_type,
                "train_rmse":     round(train_rmse, 4),
                "train_rows":     len(X_train),
                "features_used":  len(FEATURE_NAMES),
            }

        except Exception as e:
            base["error"] = str(e)
            return base

    def predict_batch(self, ohlc_dict: dict,
                      max_workers: int = None) -> pd.DataFrame:
        """
        Run ML signal prediction for all stocks in the ohlc_dict.
        Uses ThreadPoolExecutor for parallel processing.

        ohlc_dict: {ticker: DataFrame} — from MarketCache.get_ohlc_bulk()
        Returns DataFrame with ML signal for each stock.
        """
        n = len(ohlc_dict)
        print(f"  ML signal engine: predicting {n} stocks "
              f"({self.model_type}, {LOOKBACK}d window, {TRAIN_WINDOW}d train) …")

        results   = []
        done      = 0
        n_workers = max_workers or self.workers

        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = {
                pool.submit(self.predict, sym, df): sym
                for sym, df in ohlc_dict.items()
            }
            for future in as_completed(futures):
                sym = futures[future]
                done += 1
                try:
                    results.append(future.result())
                except Exception as e:
                    results.append({"symbol": sym, "direction": "ERROR",
                                    "predicted_ret%": 0.0, "confidence": 0.0,
                                    "model": self.model_type, "error": str(e)})
                if done % 100 == 0 or done == n:
                    bullish  = sum(1 for r in results if r.get("direction")=="BULLISH")
                    bearish  = sum(1 for r in results if r.get("direction")=="BEARISH")
                    print(f"    {done}/{n} done | BULLISH: {bullish} | BEARISH: {bearish}")

        df = pd.DataFrame(results)
        if not df.empty and "predicted_ret%" in df.columns:
            df = df.sort_values("predicted_ret%", ascending=False)
        print(f"\n  ML signals computed: {n} stocks")
        if not df.empty and "direction" in df.columns:
            counts = df["direction"].value_counts()
            for d, c in counts.items():
                print(f"    {d:<20} {c:>5} stocks ({c/n*100:.1f}%)")
        return df

    def evaluate_on_backtest(self, ohlc_dict: dict,
                             test_start: str, test_end: str) -> dict:
        """
        Evaluate ML signal accuracy on a held-out test period.
        Computes: hit rate (directional accuracy), RMSE, MAE.
        Consistent with AlQahtani et al. (2025) evaluation framework.

        test_start / test_end: ISO date strings (e.g. '2024-01-01')
        """
        start_ts = pd.Timestamp(test_start)
        end_ts   = pd.Timestamp(test_end)
        all_actual, all_predicted = [], []
        n_correct, n_total = 0, 0

        for sym, df in ohlc_dict.items():
            if df is None or len(df) < MIN_ROWS:
                continue
            try:
                features = compute_features(df)
                close    = df["Close"].astype(float).reindex(features.index)
                target   = close.pct_change(PREDICT_DAYS).shift(-PREDICT_DAYS) * 100
                aligned  = features.join(target.rename("target"), how="inner").dropna()
                feat_df  = aligned[FEATURE_NAMES]
                tgt_ser  = aligned["target"]

                # Walk-forward: for each day in test set, train on prior TRAIN_WINDOW
                test_mask = (aligned.index >= start_ts) & (aligned.index <= end_ts)
                test_idx  = [i for i, d in enumerate(aligned.index) if test_mask[i]]

                for ti in test_idx:
                    train_end   = ti
                    train_start = max(0, ti - TRAIN_WINDOW)
                    tr_feat     = feat_df.iloc[train_start:train_end]
                    tr_tgt      = tgt_ser.iloc[train_start:train_end]
                    X_tr, y_tr  = self._make_sequences(tr_feat, tr_tgt)
                    if len(X_tr) < 20:
                        continue
                    model = self._make_model()
                    model.fit(X_tr, y_tr)
                    latest = feat_df.iloc[max(0, ti-LOOKBACK):ti]
                    if len(latest) < LOOKBACK:
                        continue
                    z_win = z_score_normalise(latest.values).flatten().reshape(1, -1)
                    pred  = float(model.predict(z_win)[0])
                    act   = float(tgt_ser.iloc[ti])
                    all_actual.append(act)
                    all_predicted.append(pred)
                    # Directional accuracy
                    if (pred > 0 and act > 0) or (pred < 0 and act < 0):
                        n_correct += 1
                    n_total += 1
            except Exception:
                continue

        if not all_actual:
            return {"error": "No test predictions generated"}

        actual    = np.array(all_actual)
        predicted = np.array(all_predicted)
        rmse = float(np.sqrt(mean_squared_error(actual, predicted)))
        mae  = float(mean_absolute_error(actual, predicted))
        hit  = n_correct / n_total * 100 if n_total > 0 else 0

        print(f"\n  ML Backtest Evaluation ({test_start} – {test_end})")
        print(f"  Predictions:      {n_total}")
        print(f"  RMSE:             {rmse:.4f}  "
              f"(paper: LR 0.304, LSTM 0.346, RNN 0.415)")
        print(f"  MAE:              {mae:.4f}  "
              f"(paper: LR 0.207, LSTM 0.237, RNN 0.329)")
        print(f"  Directional Acc:  {hit:.1f}%  "
              f"(>50% = better than random; paper reports 76–85%)")

        return {
            "n_predictions":   n_total,
            "rmse":            round(rmse, 4),
            "mae":             round(mae,  4),
            "directional_acc": round(hit,  1),
            "test_period":     f"{test_start} – {test_end}",
        }


# ── Standalone comparison: LR vs Ridge vs buy-and-hold ───────────────────────

def compare_models(df_ohlc: pd.DataFrame, symbol: str = "TEST") -> pd.DataFrame:
    """
    Compare Linear Regression, Ridge, and a naive buy-and-hold baseline.
    Replicates the AlQahtani et al. (2025) evaluation framework.
    Prints RMSE and MAE for each, matching Tables III & IV in the paper.
    """
    if not _SKLEARN_OK or len(df_ohlc) < MIN_ROWS:
        return pd.DataFrame()

    features = compute_features(df_ohlc)
    close    = df_ohlc["Close"].astype(float).reindex(features.index)
    target   = close.pct_change(PREDICT_DAYS).shift(-PREDICT_DAYS) * 100
    aligned  = features.join(target.rename("target"), how="inner").dropna()
    feat_df  = aligned[FEATURE_NAMES]
    tgt_ser  = aligned["target"]

    X, y = MLSignalEngine()._make_sequences(feat_df, tgt_ser)
    if len(X) < 50:
        return pd.DataFrame()

    # 80/20 chronological split (AlQahtani et al. methodology)
    split   = int(len(X) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    rows = []
    for name, model in [
        ("LinearRegression", LinearRegression()),
        ("Ridge(alpha=1)",   Ridge(alpha=1.0)),
        ("Naive(last_ret)",  None),  # baseline: predict last observed return
    ]:
        if model is not None:
            model.fit(X_train, y_train)
            y_pred_tr = model.predict(X_train)
            y_pred_ts = model.predict(X_test)
        else:
            # Naive: predict the mean of training targets
            naive_val = y_train.mean()
            y_pred_tr = np.full_like(y_train, naive_val)
            y_pred_ts = np.full_like(y_test,  naive_val)

        rows.append({
            "Model":      name,
            "Train_RMSE": round(float(np.sqrt(mean_squared_error(y_train, y_pred_tr))), 4),
            "Train_MAE":  round(float(mean_absolute_error(y_train, y_pred_tr)), 4),
            "Test_RMSE":  round(float(np.sqrt(mean_squared_error(y_test, y_pred_ts))), 4),
            "Test_MAE":   round(float(mean_absolute_error(y_test, y_pred_ts)), 4),
        })

    df = pd.DataFrame(rows)
    print(f"\n  Model comparison for {symbol} "
          f"(train={split} rows, test={len(X_test)} rows):")
    print(f"  {'Model':<25} {'Train RMSE':>11} {'Train MAE':>10} "
          f"{'Test RMSE':>10} {'Test MAE':>9}")
    print("  " + "─"*65)
    for _, r in df.iterrows():
        print(f"  {r['Model']:<25} {r['Train_RMSE']:>11.4f} {r['Train_MAE']:>10.4f} "
              f"{r['Test_RMSE']:>10.4f} {r['Test_MAE']:>9.4f}")
    print(f"  Paper benchmark:          LR: 0.334 train / 0.304 test  "
          f"(LSTM: 0.355 / 0.346, RNN: 0.383 / 0.415)")

    return df


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE.NS"

    print(f"\nML Signal Engine — testing on {ticker}")

    # Load from cache if available, else download
    try:
        from market_data_cache import get_cache
        cache  = get_cache()
        df     = cache.get_ohlc(ticker)
    except Exception:
        import yfinance as yf
        df = yf.download(ticker, period="5y", auto_adjust=True, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df = df.xs(ticker, axis=1, level=1)

    if df.empty:
        print(f"No data for {ticker}")
        sys.exit(1)

    print(f"Data: {len(df)} bars ({df.index[0].date()} – {df.index[-1].date()})")

    # Model comparison (LR vs Ridge vs naive)
    compare_models(df, symbol=ticker)

    # Generate signal
    engine = MLSignalEngine(model_type="ridge")
    signal = engine.predict(ticker, df)
    print(f"\n  Signal for {ticker}:")
    for k, v in signal.items():
        print(f"    {k:<20}: {v}")
