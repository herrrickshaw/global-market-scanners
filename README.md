# Global Market Scanners

Multi-market equity scanner applying Darvas Box breakout detection + Piotroski F-Score + Coffee Can screening across five major global markets.

> **Scope:** this repo is **stock-market screening only**. Retail-outlet (fuel
> station) data monitoring and highway/coverage heatmaps live in a separate repo:
> [`retail-outlet-monitoring`](https://github.com/herrrickshaw/retail-outlet-monitoring).

## Markets covered

| Script | Market | Universe | Data source |
|---|---|---|---|
| `full_us_market_scan.py` | USA (NYSE + NASDAQ) | ~5,400 stocks | yfinance |
| `full_european_market_scan.py` | Europe (Euro Stoxx 50) | 50 stocks | yfinance |
| `full_indian_market_scan.py` | India (NSE + BSE) | ~4,600 stocks | nsepython + bseindia + yfinance |
| `full_japan_market_scan.py` | Japan (TSE Prime + Standard) | ~3,600 stocks | kabupy (JPX) + yfinance |
| `full_korea_market_scan.py` | South Korea (KOSPI + KOSDAQ) | ~2,600 stocks | pykrx (KRX/Naver) + yfinance |

## Pipeline

Each scanner runs the same 5-stage pipeline:

1. **Universe fetch** — pull the full equity list for the market
2. **Bulk OHLC download** — 3-month price history for all tickers
3. **Darvas Box screen** — classify every stock as `BREAKOUT_BUY`, `BREAKDOWN_SELL`, or `IN_BOX`
4. **Fundamental scan** (breakout candidates only) — Piotroski F-Score + Coffee Can
5. **Excel export** — 4-sheet styled workbook: All Stocks, Darvas Signals, Fundamentals, Triple Hits

## Triple Hit criteria

A stock must pass all three simultaneously:
- **Darvas breakout** — price closes above the confirmed box top
- **Piotroski F-Score ≥ 7/9** — strong financial health
- **Coffee Can pass** — Revenue CAGR > 10%, avg ROCE > 15%, D/E < 1, positive earnings every year, positive FCF

## Install

```bash
pip install -r requirements.txt
```

Pinned in [`requirements.txt`](requirements.txt).

The **US and India scanners are the full system-integrated versions** — they
emit extra sheets (Magic Formula, Golden Crossover, Multi-Screen Hits, and
**ML Bullish/Bearish**) and are backed by helper modules `stock_utils.py`,
`nse_data_fetcher.py`, `market_data_cache.py`, and `ml_signal_engine.py`.
The **Japan/Korea/Europe scanners are self-contained**. The ML signal engine is
market-agnostic — see [`ml_viability.py`](ml_viability.py) for the cross-market
5-year viability backtest.

## Usage

```bash
python full_indian_market_scan.py                    # full run
python full_indian_market_scan.py --top 200          # first 200 tickers
python full_indian_market_scan.py --no-scans         # Darvas only, skip fundamentals
python full_japan_market_scan.py --workers 10
python full_korea_market_scan.py --kospi-only
python full_european_market_scan.py --top 10
```

## Sample results (13 Jun 2026)

| Market | Scanned | Breakouts | Breakdowns | Triple Hits |
|---|---|---|---|---|
| USA | 5,406 | 1,818 | 242 | 0 |
| Europe | 50 | 27 | 1 | 3 (Ferrari, ASML, Hermès) |
| India | 4,587 | 757 | 255 | 14 |
| Japan | 3,566 | 798 | 276 | 2 (東テク, Fast Retailing) |
| Korea | 2,606 | 640 | 20 | 2 (JW생명과학, 아이비김영) |
