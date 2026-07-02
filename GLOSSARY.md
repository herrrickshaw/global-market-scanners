# Glossary of reusable code blocks

The platform's ~35 analysis modules share a small set of building blocks, extracted
into [`marketdata.py`](marketdata.py) so the same logic lives **once** instead of being
copy-pasted. Each block is one function; this is the glossary of what each does.

## `marketdata.py` — the shared library

### Data access
| Block | Signature | What it does |
|---|---|---|
| `SEED` | constant | Path to the `cleaned_long_*.parquet` price cache. |
| `market_list()` | → `list[str]` | Every market code that has a parquet (US, JP, KR, …). Replaces a comprehension that was copied into **12 modules**. |
| `wide(market, fields=…)` | → `dict[str, DataFrame]` | Loads a market's long parquet and pivots to wide (Date × Symbol) frames, one per OHLCV field. |
| `close_volume(market)` | → `(close, volume)` | The common two-frame case (what the old `_market_wide` returned). |

### Universe
| Block | Signature | What it does |
|---|---|---|
| `liquid_symbols(close, vol, quantile=.6, min_history=250)` | → `list[str]` | The tradeable universe: top ~40% by trailing median dollar-volume with enough history (drops penny/illiquid junk). |
| `clean_key(ticker)` | → `str` | Normalises a ticker to a bare, upper-cased symbol (drops the `.NS`/`.T`/`.BO` suffix) so a company joins across data sources. Replaces 6 near-identical helpers. |
| `market_proxy(close, symbols=None, clip=.5)` | → `Series` | Equal-weight daily return of a universe, per-day clipped so penny glitches don't poison the mean. |

### Cross-sectional statistics
| Block | Signature | What it does |
|---|---|---|
| `zscore(series)` | → `Series` | Standardise a cross-section to mean 0 / sd 1 (±inf → NaN). Replaces the `_z` helper duplicated across modules. |
| `information_coefficient(signal, fwd_ret)` | → `float` | The **IC**: correlation of a signal with realised forward returns (does the signal predict?). |
| `monotonicity(curve, col)` | → `float` | +1 = a quantile curve rises perfectly Q1→Qn (a clean effect). |
| `trend_corr(x)` | → `float` | Scale-free trend of a series = correlation with time ∈ [−1,1]. |

## How the modules use it
Each module keeps its **public function names** (so imports and tests are unchanged)
but the *body* now delegates to `marketdata`. For example:

```python
# liquidity_factor.py — before: ~8 lines of parquet-load + pivot
def _market_wide(market):
    import marketdata
    return marketdata.close_volume(market)      # after: 1 line

# quality_factor.py, peer_network.py, sector_rotation.py, watchlists.py
def _clean(t):
    from marketdata import clean_key
    return clean_key(t)                          # was: str(t).split(".")[0].upper()
```

## Domain "blocks" (the reusable analytics, by module)
Beyond the shared library, each factor module exposes pure, composable functions —
the analytical vocabulary of the platform:

| Domain | Module | Key blocks |
|---|---|---|
| Quality (QMJ) | `quality_factor.py` | `z_rank`, `dimension_score`, `quality_score`, `qmj_combo`, `lq_combo` |
| PEAD | `pead_factor.py` | `detect_events`, `event_surprise`, `car`, `pead_score`, `drift_by_surprise` |
| Liquidity | `liquidity_factor.py` | `amihud_illiq`, `capacity_score`, `premium_by_illiq` |
| Accumulation | `darvas_volume.py`, `accumulation_screener.py` | `obv`, `chaikin_money_flow`, `up_down_volume_ratio`, `darvas_box`, `accumulation_signal` |
| Microstructure | `hft_selection.py` | `corwin_schultz_spread`, `efficiency_ratio`, `ou_half_life`, `lag1_autocorr` |
| Portfolio / risk | `portfolio.py`, `risk.py` | `min_variance_weights`, `cap_weights`, `max_drawdown`, `hist_var`, `sharpe` |
| Corporate actions | `corporate_actions.py` | `nearest_split_ratio`, `detect_splits`, `detect_rights`, `parse_submissions` |
| Sentiment / options / ESG | `news_sentiment.py`, `options_iv.py`, `esg_screen.py` | `score_text`, `atm_iv`, `iv_skew`, `esg_score_0_100` |

All of the above are unit-tested (97 tests) and enforced by CI. The abstraction rule:
**data-plumbing and cross-sectional stats live in `marketdata`; domain logic stays in
its module as small, pure, testable functions.**
