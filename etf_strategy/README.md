# ETFStrategy

ETFStrategy is a daily ETF rotation research framework based on the specification in `ETFStrategy.md`.

It supports:

- ETF universe filtering
- Daily technical indicators
- Cross-sectional scoring
- Watchlist generation with asset-class and correlation constraints
- EMA20 pullback entry signals
- ATR stops, trailing stops, and rank-based exits
- Risk-budget position sizing
- Multi-ETF backtesting
- Markdown daily trade plans
- DuckDB storage with idempotent daily updates
- Multi-market OHLCV storage for A-share, HK, US, and crypto symbols across monthly, weekly, daily, hourly, 30m, 15m, and 5m bars

## Quick Start

```bash
pip install -r requirements.txt
pytest
python run_backtest.py --config config/strategy_config.yaml
python run_backtest.py --config config/strategy_config.yaml --data data/processed/etf_strategy.duckdb
python run_dashboard.py --db data/processed/etf_strategy.duckdb
python run_daily_plan.py --config config/strategy_config.yaml --date 2026-06-21
```

By default the command line tools use generated sample data when no input data path is provided.
For real data, place ETF daily bars in CSV or Parquet format with the required fields described in `ETFStrategy.md`.

## Data Storage

The primary data store is DuckDB:

```text
data/processed/etf_strategy.duckdb
```

Main tables:

- `etf_daily`: daily ETF bars, primary key `(date, symbol)`
- `etf_meta`: latest ETF metadata, primary key `symbol`
- `update_log`: update history

The update flow is idempotent: if a `(date, symbol)` row already exists, it is replaced before new data is inserted.

## Initialize Data

```bash
python scripts/update_etf_data.py \
  --start-date 2021-06-22 \
  --end-date 2026-06-22 \
  --min-amount 30000000 \
  --min-fund-size 500000000 \
  --db data/processed/etf_strategy.duckdb
```

This writes normalized ETF bars into DuckDB. CSV export is still enabled by default for inspection:
`data/raw/etf_daily_5y.csv`, `data/raw/etf_meta_latest.csv`, and `data/raw/update_summary.md`.

To import an existing normalized CSV into DuckDB:

```bash
python scripts/import_csv_to_duckdb.py \
  --csv data/raw/etf_daily_5y.csv \
  --db data/processed/etf_strategy.duckdb
```

## Daily Incremental Update

```bash
python scripts/update_etf_data.py \
  --incremental \
  --end-date 2026-06-22 \
  --min-amount 30000000 \
  --min-fund-size 500000000 \
  --db data/processed/etf_strategy.duckdb \
  --no-csv
```

With `--incremental`, the script reads the latest date already stored in DuckDB and fetches from the next calendar day.
If no bars are available, for example on a non-trading day, the script writes a summary and exits cleanly.

## Dashboard

```bash
python run_dashboard.py \
  --db data/processed/etf_strategy.duckdb \
  --host 127.0.0.1 \
  --port 8765
```

Open `http://127.0.0.1:8765` to inspect data coverage, asset-class distribution, ETF rankings,
the strategy watchlist, and per-symbol price trends.

## Multi-Market OHLCV Data

The general stock/crypto data store is separate from the ETF strategy table:

```text
data/processed/market_data.duckdb
```

Main tables:

- `market_ohlcv`: OHLCV bars keyed by `(market, symbol, timeframe, ts)`
- `market_update_log`: per-symbol update history

Supported normalized timeframes:

```text
1mo, 1w, 1d, 1h, 30m, 15m, 5m
```

Default free data providers:

- A-share: BaoStock for bulk stock bars, AKShare for the seed config symbols
- HK, US: AKShare
- Crypto: CCXT, with no-dependency OKX/Binance REST fallback when `ccxt` is not installed

The seed universe is in `config/market_data_universe.yaml`. Expand it with the individual stocks or crypto pairs needed by a backtest.

### Full Stock Data Bootstrap

Use the stock updater to build a free A-share/HK/US stock universe and write daily bars into the shared
`market_ohlcv` table. The default timeframe is `1d`, matching the ETF daily-bar granularity.

Preview the full download plan without writing bars:

```bash
python scripts/update_stock_data.py \
  --refresh-universe \
  --markets a_share hk us \
  --dry-run
```

Download every selected stock for the last 5 years. This is a large job, so `--all` is required explicitly:

```bash
python scripts/update_stock_data.py \
  --refresh-universe \
  --markets a_share hk us \
  --timeframes 1d \
  --years 5 \
  --db data/processed/market_data.duckdb \
  --all
```

Run a resumable batch instead of the whole universe:

```bash
python scripts/update_stock_data.py \
  --markets a_share hk us \
  --timeframes 1d \
  --years 5 \
  --max-symbols 500 \
  --offset 0 \
  --only-missing
```

Daily incremental stock refresh:

```bash
python scripts/update_stock_data.py \
  --markets a_share hk us \
  --timeframes 1d \
  --incremental \
  --chunk-days 1 \
  --db data/processed/market_data.duckdb \
  --all
```

### 2018—当前批量回追与覆盖审计

`update_stock_data.py` 支持按已存行情的前置/尾部日期缺口回追，不需要手动导入 CSV。默认优先选取数据库中已有日线的目标标的；首次空库时才回退到发现的 active universe。

先审计当前研究目标池的覆盖范围：

```bash
python scripts/update_stock_data.py \
  --db data/processed/market_data.duckdb \
  --markets a_share \
  --timeframes 1d \
  --start-date 2018-01-01 \
  --end-date 2026-08-29 \
  --coverage-mode edges \
  --coverage-only \
  --coverage-report data/processed/a_share_coverage.csv
```

执行可恢复的批量回追；`--max-symbols` 与 `--offset` 可将目标池拆成多批，重复执行不会产生重复主键：

```bash
python scripts/update_stock_data.py \
  --db data/processed/market_data.duckdb \
  --markets a_share \
  --timeframes 1d \
  --start-date 2018-01-01 \
  --end-date 2026-08-29 \
  --coverage-mode edges \
  --target existing-price \
  --provider baostock \
  --chunk-days 0 \
  --retries 2 \
  --all \
  --coverage-report data/processed/a_share_coverage.csv
```

日常更新使用 `--incremental --coverage-mode none`；provider 请求失败会自动重试，所有请求和写入区间会追加记录到 `market_update_log`。

Run a deterministic local example without network:

```bash
python scripts/update_market_data.py \
  --provider synthetic \
  --years 1 \
  --replace-db \
  --db data/processed/market_data_example.duckdb
```

Download the configured symbols for the last 5 years:

```bash
python scripts/update_market_data.py \
  --years 5 \
  --db data/processed/market_data.duckdb \
  --chunk-days 1 \
  --sleep 0.05
```

Daily incremental refresh:

```bash
python scripts/update_market_data.py \
  --incremental \
  --end-date 2026-06-22 \
  --db data/processed/market_data.duckdb \
  --chunk-days 1
```

Free intraday sources can have market-specific retention limits. The updater records the actual stored `start_ts` and `end_ts` per market/timeframe in DuckDB, so backtests should validate coverage before assuming five full years of intraday bars are available for every equity market.
