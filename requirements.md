# Python Package Reference — Market, Crypto & Forex Data

A curated registry of PyPI-listed and publicly documented Python packages for
fetching market data, screening, technical/fundamental analysis, backtesting,
portfolio construction, and broker execution — across equities (16 markets),
crypto, and forex. Compiled 2026-07-17 from PyPI listings and public docs.

**Why this file exists:** this registry gets copied into every stock-market /
data-pipeline repo across the account so a new session or a new repo doesn't
have to re-research PyPI from scratch before picking a data source. It's a
menu to choose from, not a lockfile — don't `pip install` the whole thing.
Check whether `yfinance` already covers what you need (see callout below)
before reaching for a specialized package.

> Package activity, licensing, and rate limits shift. Verify current
> maintenance status and terms of service before wiring anything into a
> production pipeline — especially the scraping-based fetchers (`investpy`,
> `stockdex`, `nsetools`, `fundamentus`, `pyasx`), which are more exposure-prone
> to upstream site changes than official SDKs (`kiteconnect`,
> `upstox-python-sdk`, `smartapi-python`, `yfinance`, `twelvedata`,
> `finnhub-python`, `eodhd`, `ccxt`, `python-binance`).

**Before adding a dependency:** Yahoo Finance is genuinely global —
`yfinance` reaches most exchanges below through ticker suffixes alone:
`.AX` Australia · `.TO`/`.V` Canada · `.HK` Hong Kong · `.TW`/`.TWO` Taiwan ·
`.SI` Singapore · `.SA` Brazil · `.JO` South Africa · `BTC-USD` crypto ·
`EURUSD=X` forex. The dedicated packages below earn their keep for
fundamentals, native-language filings, order-book depth, or rate limits
Yahoo doesn't clear.

---

## Contents

1. [General & global market data](#1--general--global-market-data)
2. [India — NSE / BSE](#2--india--nse--bse)
3. [China — A-shares](#3--china--a-shares)
4. [Korea / Japan / Asia-Pac](#4--korea--japan--asia-pac)
5. [US fundamentals — SEC EDGAR](#5--us-fundamentals--sec-edgar)
6. [Technical analysis & indicators](#6--technical-analysis--indicators)
7. [Backtesting engines](#7--backtesting-engines)
8. [Portfolio construction & risk](#8--portfolio-construction--risk)
9. [Performance analytics](#9--performance-analytics)
10. [Options & derivatives pricing](#10--options--derivatives-pricing)
11. [ML-driven quant platforms](#11--ml-driven-quant-platforms)
12. [India broker execution APIs](#12--india-broker-execution-apis)
13. [Screening & fundamentals scrape](#13--screening--fundamentals-scrape)
14. [Hong Kong & Taiwan](#14--hong-kong--taiwan)
15. [Australia & Canada](#15--australia--canada)
16. [Brazil & Latin America](#16--brazil--latin-america)
17. [Southeast Asia (ASEAN)](#17--southeast-asia-asean)
18. [Middle East & Africa](#18--middle-east--africa)
19. [Multi-market aggregator APIs](#19--multi-market-aggregator-apis)
20. [Cryptocurrency data](#20--cryptocurrency-data)
21. [Forex & currency data](#21--forex--currency-data)

---

## 1 — General & global market data

Broad OHLCV / quote fetchers not tied to one exchange.

| Package | Install | What it does |
|---|---|---|
| [yfinance](https://pypi.org/project/yfinance/) | `pip install yfinance` | Yahoo Finance OHLCV, fundamentals, options chains, dividends. Default fetcher across most markets. |
| [investpy](https://pypi.org/project/investpy/) | `pip install investpy` | Investing.com — ~40k stocks, 82k funds, ETFs, indices, bonds, crypto. |
| [stockdex](https://pypi.org/project/stockdex/) | `pip install stockdex` | Pandas/Plotly interface over Yahoo Finance, Digrin, Finviz, Macrotrends, JustETF (EU ETFs). |
| [stockanalysis-py](https://pypi.org/project/stockanalysis-py/) | `pip install stockanalysis-py` | Wraps stockanalysis.com for financial statements, ratios, estimates. |
| [marketdata-sdk-py](https://pypi.org/project/marketdata-sdk-py/) | `pip install marketdata-sdk-py` | Official Market Data API SDK — real-time quotes, options data, market status. |
| [stockstats](https://pypi.org/project/stockstats/) | `pip install stockstats` | DataFrame wrapper — indicator columns (`df['macd']`) compute lazily on access. |
| [mplchart](https://pypi.org/project/mplchart/) | `pip install mplchart` | Classic TA candlestick charts over matplotlib; pandas or polars frames. |

## 2 — India — NSE / BSE

| Package | Install | What it does |
|---|---|---|
| [nsepython](https://pypi.org/project/nsepython/) | `pip install nsepython` | Merges NsepY + NSETools function surfaces; server-safe variant for Colab/AWS/DigitalOcean. |
| [nsetools](https://pypi.org/project/nsetools/) | `pip install nsetools` | Stock codes + live NSE quote data. PyPI release lags GitHub — clone for current features. |
| [nsepy](https://github.com/swapniljariwala/nsepy) | `pip install nsepy` | Historical OHLC, live indices, FNO derivatives data from the NSE website. |
| [nsefin](https://pypi.org/project/nsefin/) | `pip install nsefin` | Lightweight, clean `pandas.DataFrame` output; Python 3.9+, minimal deps. |
| [bsedata](https://pypi.org/project/bsedata/) | `pip install bsedata` | Real-time BSE quotes for stocks and indices. |
| [jugaad-data](https://pypi.org/project/jugaad-data/) | `pip install jugaad-data` | Historical + live NSE, BSE, and RBI data (bhavcopy downloads, bond yields). |

## 3 — China — A-shares

| Package | Install | What it does |
|---|---|---|
| [akshare](https://pypi.org/project/akshare/) | `pip install akshare` | Broadest open Chinese financial data interface — stocks, futures, options, funds, FX, bonds, indices. Also exposes Hong Kong endpoints (`stock_hk_spot_em`). |
| [akshare-one](https://pypi.org/project/akshare-one/) | `pip install akshare-one` | Slimmer, unified-interface wrapper over akshare's most-used endpoints. |

## 4 — Korea / Japan / Asia-Pac

| Package | Install | What it does |
|---|---|---|
| [finance-datareader](https://pypi.org/project/finance-datareader/) | `pip install finance-datareader` | KRX (KOSPI/KOSDAQ/KONEX) + NASDAQ, NYSE, AMEX, S&P500, SSE, SZSE, HKEX, TSE, HOSE, FX pairs, crypto. |
| [pandas-datareader](https://pypi.org/project/pandas-datareader/) | `pip install pandas-datareader` | FRED, Stooq, World Bank remote readers — pairs macro series with equity data. |

## 5 — US fundamentals — SEC EDGAR

Point-in-time US fundamentals for Piotroski-style scoring, avoiding restated-data lookahead bias.

| Package | Install | What it does |
|---|---|---|
| [edgartools](https://pypi.org/project/edgartools/) | `pip install edgartools` | Parses 10-K/8-K/XBRL, Forms 3/4/5, 13F, ADV into a typed API. Most actively developed. |
| [sec-edgar-api](https://pypi.org/project/sec-edgar-api/) | `pip install sec-edgar-api` | Typed wrapper over every EDGAR REST endpoint; built-in pagination + 10 req/s rate limit. |
| [sec-edgar-downloader](https://pypi.org/project/sec-edgar-downloader/) | `pip install sec-edgar-downloader` | Bulk-downloads raw filings by form type for offline parsing. |
| [edgar-sec](https://pypi.org/project/edgar-sec/) | `pip install edgar-sec` | Async-first EDGAR client — better throughput for large point-in-time backfills. |
| [sec-api](https://pypi.org/project/sec-api/) | `pip install sec-api` | Hosted (paid) service — 500+ form types back to 1993, real-time updates. |

## 6 — Technical analysis & indicators

| Package | Install | What it does |
|---|---|---|
| [TA-Lib](https://pypi.org/project/TA-Lib/) | `pip install TA-Lib` | C-backed reference implementation, 150+ indicators, fastest — needs native TA-Lib C library first. |
| [pandas-ta](https://pypi.org/project/pandas-ta/) | `pip install pandas-ta` | Pure-Python, 130+ indicators as a pandas extension — no C build step. |
| [ta](https://pypi.org/project/ta/) | `pip install ta` | Momentum/volume/volatility/trend indicators on pandas/numpy. |
| [finta](https://pypi.org/project/finta/) | `pip install finta` | Common indicators in pure pandas — smaller, easier to read/vendor than pandas-ta. |
| [stockstats](https://pypi.org/project/stockstats/) | `pip install stockstats` | Lazy indicator columns via `StockDataFrame.retype(df)`. |
| [technical-analysis](https://pypi.org/project/technical-analysis/) | `pip install technical-analysis` | Candlestick patterns + indicators + a light backtest runner in one package. |

## 7 — Backtesting engines

| Package | Install | What it does |
|---|---|---|
| [vectorbt](https://pypi.org/project/vectorbt/) | `pip install vectorbt` | Numba-accelerated, vectorized — thousands of parameter combos in seconds. Best for threshold sweeps. |
| [backtrader](https://pypi.org/project/backtrader/) | `pip install backtrader` | Event-driven, most realistic fills; native TA-Lib integration; supports live trading. |
| [backtesting.py](https://pypi.org/project/backtesting/) | `pip install backtesting` | Minimal API on pandas/numpy/Bokeh — fastest single-strategy backtest + plot. |
| [bt](https://pypi.org/project/bt/) | `pip install bt` | Tree-structured strategy composition for multi-asset portfolio rebalancing. |
| [zipline-reloaded](https://pypi.org/project/zipline-reloaded/) | `pip install zipline-reloaded` | Community-maintained Zipline fork; full trading-calendar + pipeline API, heavier setup. |

## 8 — Portfolio construction & risk

| Package | Install | What it does |
|---|---|---|
| [PyPortfolioOpt](https://pypi.org/project/PyPortfolioOpt/) | `pip install PyPortfolioOpt` | Mean-variance, Black-Litterman, shrinkage covariance, Hierarchical Risk Parity. scikit-learn-style API. |
| [Riskfolio-Lib](https://pypi.org/project/Riskfolio-Lib/) | `pip install Riskfolio-Lib` | CVXPY-based — risk-parity, CVaR/CDaR objectives, factor-risk-budgeting. |

## 9 — Performance analytics

| Package | Install | What it does |
|---|---|---|
| [QuantStats](https://pypi.org/project/QuantStats/) | `pip install QuantStats` | One call → full HTML tearsheet (Sharpe, Sortino, drawdowns, rolling beta). |
| [empyrical-reloaded](https://pypi.org/project/empyrical-reloaded/) | `pip install empyrical-reloaded` | Risk/return metric primitives underneath zipline and pyfolio. |
| [pyfolio-reloaded](https://pypi.org/project/pyfolio-reloaded/) | `pip install pyfolio-reloaded` | Full tearsheet + Bayesian performance analysis. |

## 10 — Options & derivatives pricing

| Package | Install | What it does |
|---|---|---|
| [vollib](https://pypi.org/project/vollib/) | `pip install vollib` | Black / BS / BSM pricing, implied vol, analytical + numerical Greeks. (`py_vollib` renamed; alias still works.) |
| [QuantLib](https://pypi.org/project/QuantLib/) | `pip install QuantLib` | SWIG Python bindings over C++ QuantLib — yield curves, barrier/Asian options, hundreds of models. Heavyweight. |
| [greeks-package](https://pypi.org/project/greeks-package/) | `pip install greeks-package` | Narrow, dependency-light Greeks calculator for a quick delta/gamma check. |

## 11 — ML-driven quant platforms

| Package | Install | What it does |
|---|---|---|
| [pyqlib (Qlib)](https://pypi.org/project/pyqlib/) | `pip install pyqlib` | Microsoft's AI-oriented quant platform — data processing, model training, backtesting pipeline. Steep setup. |
| [stockpy-learn](https://pypi.org/project/stockpy-learn/) | `pip install stockpy-learn` | Generalized regression/classification ML, started as stock-prediction-specific. Lighter than Qlib. |

## 12 — India broker execution APIs

| Package | Install | What it does |
|---|---|---|
| [kiteconnect](https://pypi.org/project/kiteconnect/) | `pip install kiteconnect` | Zerodha's official client — orders, positions, historical data, WebSocket ticks. Paid: ₹2,000/mo Connect subscription. |
| [upstox-python-sdk](https://pypi.org/project/upstox-python-sdk/) | `pip install upstox-python-sdk` | Official Upstox v2 API SDK (`import upstox_client`) — orders, portfolio, WebSocket data. |
| [smartapi-python](https://pypi.org/project/smartapi-python/) | `pip install smartapi-python` | Angel One's SmartAPI client — free, no subscription fee. |

## 13 — Screening & fundamentals scrape

| Package | Install | What it does |
|---|---|---|
| [stockdex](https://pypi.org/project/stockdex/) | `pip install stockdex` | Finviz/Macrotrends adapters double as a lightweight screener source when Screener.in CSVs aren't available. |

## 14 — Hong Kong & Taiwan

Thin OSS coverage — most depth is behind paid tick-data APIs (AllTick, iTick, Fubon).

| Package | Install | What it does |
|---|---|---|
| [twstock](https://pypi.org/project/twstock/) | `pip install twstock` | TWSE + TPEx price history and realtime quotes, with moving-average/BIAS helpers. Rate-limited 3 req/5s by TWSE — respect it. |
| akshare | `ak.stock_hk_spot_em()`, `ak.stock_hk_hist()` | Check before adding a new dependency — akshare's East Money-backed endpoints cover HK-listed equities too. |

## 15 — Australia & Canada

Neither has a well-maintained PyPI-published client. TSX in particular is thin outside paid feeds — use §19's aggregators for Canada.

| Package | Install | What it does |
|---|---|---|
| [pyasx](https://github.com/jericmac/pyasx) | `git+https://github.com/jericmac/pyasx` (not on PyPI) | Company info, announcements, pricing from asx.com.au's undocumented API. |
| [asxtrade](https://github.com/ozacas/asxtrade) | `git+https://github.com/ozacas/asxtrade` (not on PyPI) | Full ASX downloader + portfolio app — treat as reference implementation to lift code from. |

## 16 — Brazil & Latin America

B3/Bovespa is the one non-Yahoo-suffix market with genuine PyPI-native fundamentals coverage.

| Package | Install | What it does |
|---|---|---|
| [fundamentus](https://pypi.org/project/fundamentus/) | `pip install fundamentus` | P/E, P/B, ROE, dividend yield and other screener-style ratios for B3-listed names. Most recently maintained. |
| [pyFundamentus](https://pypi.org/project/pyfundamentus/) | `pip install pyfundamentus` | Same source, more structured typed output. |
| [BrazilianMarketDataCollector](https://github.com/gustavomoers/BrazilianMarketDataCollector) | `git+https://github.com/gustavomoers/BrazilianMarketDataCollector` | Aggregates CVM regulatory filings + B3 + yahooquery + Alpha Vantage — closest LatAm analogue to an EDGAR pipeline. |

## 17 — Southeast Asia (ASEAN)

No standalone OSS Python package cleared the bar for Singapore, Malaysia, Indonesia, Thailand, or Vietnam individually — use §19's aggregators (Twelve Data explicitly lists IDX, Bursa Malaysia, SET, SGX).

| Source | Access | What it does |
|---|---|---|
| [Sectors.app](https://sectors.app/) | REST API, no dedicated pip package | Indonesia-first (also Singapore + Malaysia) fundamentals API. Freemium — call with `requests`. |

## 18 — Middle East & Africa

Thinnest OSS coverage of any region — REST-only, no PyPI clients worth vendoring.

| Source | Access | What it does |
|---|---|---|
| [SAHMK API](https://www.sahmk.sa/en/developers/tutorials/build-saudi-stock-tracker-python) | REST API, no dedicated pip package | 350+ Tadawul (TASI + Nomu) names. Free tier: 15-min-delayed prices, 100 req/day, no card required. |
| [JSE Market Data](https://www.jse.co.za/market-data) | REST API, no dedicated pip package | Official Johannesburg Stock Exchange feed — equities, derivatives, bonds, indices. |

## 19 — Multi-market aggregator APIs

The pragmatic fix for §17–18's thin spots: one client, freemium tier, dozens of exchanges.

| Package | Install | What it does |
|---|---|---|
| [twelvedata](https://pypi.org/project/twelvedata/) | `pip install twelvedata` | Widest exchange breadth, including IDX, Bursa Malaysia, SET, SGX, Saudi Exchange, TSX. WebSocket on paid tiers. |
| [finnhub-python](https://pypi.org/project/finnhub-python/) | `pip install finnhub-python` | Most generous free tier (60 req/min); strong fundamentals + alternative-data endpoints. |
| [alpha_vantage](https://pypi.org/project/alpha-vantage/) | `pip install alpha_vantage` | Easiest onramp — simple free-tier quotes, FX, technical indicators. |
| [eodhd](https://pypi.org/project/eodhd/) | `pip install eodhd` | Official EODHD client — budget option for bulk historical EOD data, 70+ exchanges (incl. ASX, JSE). |

## 20 — Cryptocurrency data

`yfinance` already handles basic OHLC (`BTC-USD`, `ETH-USD`) — these matter for order-book depth, per-exchange spreads, or on-chain signals.

| Package | Install | What it does |
|---|---|---|
| [ccxt](https://pypi.org/project/ccxt/) | `pip install ccxt` | Unified API across 100+ exchanges (Binance, Coinbase, Kraken, OKX, …) — order books, OHLCV, execution. Default choice. |
| [python-binance](https://pypi.org/project/python-binance/) | `pip install python-binance` | Full Binance-specific wrapper (spot, margin, futures, WebSocket) beyond ccxt's unified surface. |
| [pycoingecko](https://pypi.org/project/coingecko/) | `pip install pycoingecko` | Market cap, volume, historical prices, per-exchange volume. 30 endpoints free, no key. |
| [python-coinmarketcap](https://pypi.org/project/python-coinmarketcap/) | `pip install python-coinmarketcap` | CoinMarketCap wrapper — cross-check or fall back to when CoinGecko rate-limits. Needs a free API key. |

On-chain analytics (active addresses, exchange flows, MVRV) live behind [Glassnode](https://glassnode.com/) — no free PyPI client, custom-priced REST API only.

## 21 — Forex & currency data

`yfinance` covers spot rates directly (`EURUSD=X`, `USDINR=X`) — reach for these for historical ECB reference rates without a Yahoo dependency, or a broker feed for live FX/CFD trading.

| Package | Install | What it does |
|---|---|---|
| [forex-python](https://pypi.org/project/forex-python/) | `pip install forex-python` | Currency conversion + historical rates back to 1999 off ECB daily reference rates, plus a Bitcoin price index. Free, no key. |
| [easy-exchange-rates](https://pypi.org/project/easy-exchange-rates/) | `pip install easy-exchange-rates` | Same ECB source, straight into a pandas DataFrame. |
| [CurrencyConverter](https://pypi.org/project/CurrencyConverter/) | `pip install CurrencyConverter` | Bundles ECB rate history offline — no network calls, reproducible backtests. |
| [v20 (OANDA)](https://developer.oanda.com/rest-live-v20/sample-code/) | `pip install v20` | Official OANDA v20 REST bindings — live/streaming FX quotes + execution. Requires a registered fxTrade account (demo is free). |

---

*Broker packages (§12) require live API credentials and, for Kite Connect, a paid subscription. OANDA's v20 API (§21) requires a registered fxTrade account. Aggregator APIs (§19), CoinGecko/CoinMarketCap (§20), and Tadawul/JSE sources (§18) are freemium — free tiers are rate- or delay-limited, not full replacements for paid data. None of these packages were installed or executed as part of compiling this registry — verify before depending on any of them in production.*
