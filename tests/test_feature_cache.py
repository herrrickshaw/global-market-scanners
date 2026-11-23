"""feature_cache.py: content-keyed feature memoisation (parquet IO faked; pyarrow not required)."""
import os
import pickle
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import feature_cache as fc  # noqa: E402
from conftest_helpers import make_ohlcv  # noqa: E402


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(fc, "CACHE_DIR", str(tmp_path / "fc"))

    def to_parquet(self, path, **kw):
        with open(path, "wb") as f:
            pickle.dump(self, f)

    def read_parquet(path):
        with open(path, "rb") as f:
            return pickle.load(f)

    monkeypatch.setattr(pd.DataFrame, "to_parquet", to_parquet)
    monkeypatch.setattr(fc.pd, "read_parquet", read_parquet)
    return tmp_path


def test_fingerprint_changes_with_data_length_and_columns():
    a = make_ohlcv(50)
    assert fc.frame_fingerprint(a) == fc.frame_fingerprint(a.copy())
    b = a.copy(); b.iloc[10, b.columns.get_loc("Close")] += 1
    assert fc.frame_fingerprint(a) != fc.frame_fingerprint(b)
    assert fc.frame_fingerprint(a) != fc.frame_fingerprint(a.iloc[:-1])
    o = a.copy(); o.iloc[10, o.columns.get_loc("Open")] += 1     # Open not in key columns
    assert fc.frame_fingerprint(a) == fc.frame_fingerprint(o)
    assert len(fc.frame_fingerprint(a)) == 16


def test_fingerprint_empty_frame():
    e = pd.DataFrame({"Close": []})
    assert fc.frame_fingerprint(e) == fc.frame_fingerprint(pd.DataFrame({"Close": []}))


def test_cache_key_sanitises_ticker():
    df = make_ohlcv(20)
    k = fc.cache_key("RELIANCE.NS/x", df)
    assert k.startswith("RELIANCE_NS_x__") and k.endswith(fc.frame_fingerprint(df))


def test_load_missing_and_save_roundtrip(cache):
    assert fc.load("nope") is None
    f = pd.DataFrame({"a": [1, 2]})
    p = fc.save("k", f)
    assert p == os.path.join(fc.CACHE_DIR, "k.parquet") and os.path.exists(p)
    assert fc.load("k").equals(f)


def test_cached_features_computes_once(cache):
    df = make_ohlcv(30)
    calls = []

    def compute(d):
        calls.append(1)
        return pd.DataFrame({"f": [1.0]})

    a = fc.cached_features("T", df, compute)
    b = fc.cached_features("T", df, compute)
    assert calls == [1] and a.equals(b)
    changed = df.copy(); changed.iloc[-1, changed.columns.get_loc("Close")] += 1
    fc.cached_features("T", changed, compute)
    assert calls == [1, 1]
