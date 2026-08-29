# alphaLab Handoff

Date: 2026-07-02
Workspace: `/Users/zhoulin/Documents/alphaLab`

## Current State

This workspace currently contains an ETF rotation research app under `etf_strategy/`.
The repository has no commits yet; all project files are untracked from Git's point of view.

Primary capability:

- ETF daily rotation framework based on `ETFStrategy.md`.
- Local DuckDB-backed ETF data store and multi-market OHLCV store.
- Command-line backtest and daily plan generation.
- Local HTTP dashboard for ETF overview, watchlists, rankings, rolling position simulations, and stock industry/market overview.
- Free-source data update scripts for ETF, A-share, HK, US, and crypto data.

## Important Paths

- `ETFStrategy.md`: root strategy specification.
- `etf_strategy/README.md`: current runbook and major commands.
- `etf_strategy/config/strategy_config.yaml`: ETF strategy parameters.
- `etf_strategy/config/market_data_universe.yaml`: seed universe for stock/crypto market data.
- `etf_strategy/run_dashboard.py`: dashboard backend and API handlers.
- `etf_strategy/web/static/`: dashboard frontend.
- `etf_strategy/src/`: strategy, scoring, data store, providers, backtester, and risk modules.
- `etf_strategy/scripts/`: update, import, validation, and universe-building scripts.
- `etf_strategy/tests/`: current test suite.

## Main Features Implemented

### ETF Strategy

- Universe filtering by listing age, liquidity, fund size, premium/discount, missing-data ratio, and exclusion rules.
- Indicator calculation: EMA, ATR, RSI, trailing returns, MA gap/stability, close-position quality, liquidity.
- Cross-sectional scoring with configurable weights and overheat penalties.
- Watchlist construction with theme, asset-class, and correlation constraints.
- Entry rule around EMA20 pullback/reclaim.
- Exit logic with ATR stop, EMA20 breakdown, rank exit, rotation exit, break-even behavior, and trailing stop.
- Position sizing based on max per-ETF value, max loss per trade, total position cap, asset-class cap, minimum trade value, commission, slippage, and lot size.
- Multi-ETF backtest outputting trades, equity curve, watchlists, and signals.
- Markdown daily plan generation.

### ETF Data Store

- Main DB: `etf_strategy/data/processed/etf_strategy.duckdb`.
- Tables include `etf_daily`, `etf_meta`, and `update_log`.
- Upserts are idempotent on `(date, symbol)`.
- Latest observed summary from local DB:
  - 400,191 ETF daily rows.
  - 1,514 ETF symbols.
  - Data window: 2021-06-22 to 2026-07-01.
  - Largest classes: A-share industry, A-share broad, HK broad, dividend, HK tech.

Last ETF update summary in `etf_strategy/data/raw/update_summary.md`:

- Request window: 2026-06-27 to 2026-07-01.
- Actual data window: 2026-06-29 to 2026-07-01.
- 4,542 rows written for 1,514 ETFs.
- 5 fetch failures from Eastmoney/curl empty replies.
- Update mode: `carry_forward`.

### Multi-Market OHLCV Data

- Main DB: `etf_strategy/data/processed/market_data.duckdb`.
- Tables include `market_ohlcv`, `market_universe`, and `market_update_log`.
- Supported timeframes: `1mo`, `1w`, `1d`, `1h`, `30m`, `15m`, `5m`.
- Markets covered by the code path: A-share, HK, US, crypto.
- Providers:
  - A-share: BaoStock and AKShare paths.
  - HK/US: AKShare paths.
  - Crypto: CCXT with OKX/Binance REST fallback.

Latest observed summary from local `market_data.duckdb`:

- 39,115,093 OHLCV rows.
- 3,159 instruments.
- Overall window: 2021-01-04 to 2026-06-30.
- A-share has broad intraday plus daily/monthly/weekly coverage for about 384 instruments.
- HK daily coverage is broad in this DB, but HK intraday is currently only one instrument.
- US daily coverage is currently small in this DB, around 10 instruments.
- Crypto currently has one instrument with full multi-timeframe coverage.

There is also `etf_strategy/data/processed/stock_market_data_2021.duckdb`:

- 17,262,561 daily rows.
- 15,237 instruments.
- A-share: 5,528 instruments.
- HK: 2,769 instruments.
- US: 6,940 instruments.
- Window: 2021-01-04 to 2026-06-25.

### Dashboard

Run:

```bash
cd /Users/zhoulin/Documents/alphaLab/etf_strategy
python run_dashboard.py --db data/processed/etf_strategy.duckdb --market-db data/processed/market_data.duckdb
```

Default URL:

```text
http://127.0.0.1:8765
```

Backend endpoints currently include:

- `/api/summary`
- `/api/leaderboard`
- `/api/timeseries`
- `/api/watchlist`
- `/api/theme_heatmap`
- `/api/stock_overview`
- `/api/rolling_plans`
- `POST /api/update`

Frontend views currently include:

- ETF overview with price chart, market coverage, heatmap, watchlist, and leaderboard.
- ETF rolling position simulation with different rebalance windows and holding counts.
- Stock overview grouped by industry level, market, and scoring/sorting controls.
- ETF and stock product tabs.

### Desktop Launcher

There is launcher-related code under `etf_strategy/launchers/`:

- `launch_dashboard.sh`
- `start_dashboard_daemon.py`
- `ETFStrategyDashboardLauncher.c`
- `AppIcon.icns`

Treat this as local convenience tooling for starting the dashboard; verify exact macOS app packaging behavior before relying on it in a fresh environment.

## Common Commands

Install dependencies:

```bash
cd /Users/zhoulin/Documents/alphaLab/etf_strategy
pip install -r requirements.txt
```

Run dashboard:

```bash
python run_dashboard.py --db data/processed/etf_strategy.duckdb --market-db data/processed/market_data.duckdb
```

Run backtest:

```bash
python run_backtest.py --config config/strategy_config.yaml --data data/processed/etf_strategy.duckdb
```

Generate a daily plan:

```bash
python run_daily_plan.py --config config/strategy_config.yaml --date 2026-06-21
```

Update ETF data incrementally:

```bash
python scripts/update_etf_data.py \
  --incremental \
  --end-date 2026-07-01 \
  --min-amount 30000000 \
  --min-fund-size 500000000 \
  --db data/processed/etf_strategy.duckdb \
  --no-csv
```

Preview full stock data download:

```bash
python scripts/update_stock_data.py \
  --refresh-universe \
  --markets a_share hk us \
  --dry-run
```

Run a synthetic local market-data example:

```bash
python scripts/update_market_data.py \
  --provider synthetic \
  --years 1 \
  --replace-db \
  --db data/processed/market_data_example.duckdb
```

Validate market data:

```bash
python scripts/validate_market_data.py --db data/processed/market_data.duckdb
```

Check market scope:

```bash
python scripts/check_market_scope.py --db data/processed/market_data.duckdb
```

## Current Data Caveats

- `etf_strategy/data/processed/` is large, about 7.6 GB locally.
- `market_data.duckdb` and `stock_market_data_2021.duckdb` are local data artifacts and may not be suitable for Git.
- `market_scope_report.md` appears older than the latest DB state; regenerate it before using it as the source of truth.
- Free intraday sources have retention/source limits, especially for HK and US intraday.
- `market_data_validation.md` reports good price quality for checked rows, but expected-window coverage gaps for several intraday samples and missing US intraday sample data.
- HK/US QFQ verification had prior proxy/network failures against Eastmoney.

## Verification Status

Commands run on 2026-07-02:

```bash
python -m pytest
```

Result:

- Failed before collecting/running tests with exit code 139.
- `PYTHONFAULTHANDLER=1` shows a segmentation fault during pytest startup in `/opt/miniconda3/lib/python3.13/site-packages/_pytest/capture.py`.
- Default Python is `/opt/miniconda3/bin/python`, version 3.13.2.
- This looks like an environment/interpreter/package-level issue rather than a business assertion failure.

Data summary scripts using project modules did run successfully and could read the DuckDB files.

Recommended next verification step:

- Create a clean Python 3.11 or 3.12 virtual environment.
- Reinstall `requirements.txt`.
- Re-run `python -m pytest`.
- Then smoke-test dashboard endpoints against the local DB.

## Git / Repo Hygiene

Current `git status --short` from workspace root:

```text
?? .DS_Store
?? ETFStrategy.md
?? etf_strategy/
```

There are no commits on `main` yet.

Before the next serious work session:

- Add a `.gitignore` before staging.
- Exclude `.DS_Store`, local DuckDB files, raw CSV exports, and generated outputs unless intentionally versioning small fixtures.
- Commit code/config/docs separately from heavy local data artifacts.

Suggested `.gitignore` themes:

```gitignore
.DS_Store
__pycache__/
.pytest_cache/
*.pyc
etf_strategy/data/raw/
etf_strategy/data/processed/*.duckdb
etf_strategy/outputs/
```

Keep `.gitkeep` files where needed if preserving empty directory structure.

## Suggested Next Priorities

1. Stabilize the Python/test environment and get the existing test suite running.
2. Add `.gitignore`, stage code/config/docs, and make the first clean commit.
3. Regenerate market validation/scope reports from the current DB state.
4. Smoke-test dashboard endpoints with the real ETF and market DBs.
5. Decide whether stock dashboard should read `market_data.duckdb` or the broader daily-only `stock_market_data_2021.duckdb` by default.
6. Improve provider/update observability around partial failures and free-source coverage limits.
7. Decide how to version large local data: keep outside Git, use a documented local path, or introduce an artifact storage convention.

## Notes For The Next Agent

- Do not assume the Git state tells the project history; there is no commit history yet.
- The README is mostly accurate for command discovery, but local DB summaries should be regenerated when exact numbers matter.
- The dashboard backend is large and currently concentrated in `run_dashboard.py`; read the API helper functions before changing frontend behavior.
- The strategy implementation is modular under `src/`, so prefer changing those modules for core logic rather than embedding more strategy behavior into dashboard code.
- Be careful with data edits: the local DB files are large and valuable working artifacts.
