# alphaLab 当前项目总览

更新时间：2026-07-04

工作区：`/Users/zhoulin/Documents/alphaLab`

## 1. 当前状态

本地当前主要项目是 `etf_strategy/`，一个围绕 ETF 轮动策略、多市场行情数据、回测、每日计划和本地 Dashboard 的研究型应用。

Git 状态：

- 当前分支：`main`
- 仓库尚无提交
- 现有文件整体处于未跟踪状态
- 本地数据文件较大，`etf_strategy/data/processed/` 约 7.6GB，不建议直接纳入 Git

核心文档：

- `ETFStrategy.md`：根目录策略规格说明，描述原始需求和策略逻辑
- `HANDOFF.md`：上一版交接记录
- `etf_strategy/README.md`：运行手册和主要命令
- `etf_strategy/ETFStrategy.md`：指向根目录规格文档的简短说明

## 2. 已实现需求

### 2.1 ETF 轮动策略框架

已实现基于 `ETFStrategy.md` 的日线级 ETF 轮动研究框架：

- ETF 池过滤：上市时间、20日成交额、基金规模、溢价率、缺失率、杠杆/反向/主动/个股型排除
- 技术指标：EMA、ATR、RSI、20/60/120日收益、风险调整收益、有效波动、均线距离稳定性、收盘位置质量、流动性
- 横截面评分：按配置权重计算综合分，并支持过热惩罚
- 候选池构建：支持主题去重、资产类别约束、相关性去重
- 入场规则：强势 ETF 回踩 EMA20 后重新转强，结合 MA60、EMA20/MA60、收盘位置和阳线条件
- 出场规则：ATR 硬止损、EMA20 跌破、排名退化、轮动退出、移动止损、保本逻辑
- 仓位管理：单 ETF 最大金额、单笔最大亏损、总仓位上限、资产类别上限、最小交易金额、滑点、佣金、最小交易单位
- 多 ETF 回测：生成交易、净值曲线、候选池、信号等输出
- 每日计划：生成 Markdown 格式交易计划

### 2.2 ETF 数据存储与更新

已实现 DuckDB 版 ETF 数据仓库：

- 主库：`etf_strategy/data/processed/etf_strategy.duckdb`
- 表：`etf_daily`、`etf_meta`、`update_log`
- 写入方式：基于 `(date, symbol)` 幂等 upsert
- 支持全量和增量更新
- 支持从 CSV 导入 DuckDB
- 支持生成更新摘要

当前本地 ETF 库只读统计：

- 日线行数：401,705
- ETF 数量：1,514
- 数据区间：2021-06-22 至 2026-07-02
- 最大资产类别：A 股行业、A 股宽基、港股宽基、红利、港股科技

最近一次 ETF 更新摘要：

- 请求区间：2026-07-02 至 2026-07-02
- 实际数据区间：2026-07-02 至 2026-07-02
- 写入行数：1,514
- 失败数量：5
- 更新模式：`carry_forward`

### 2.3 多市场行情数据

已实现独立于 ETF 表的多市场 OHLCV 数据仓库：

- 主库：`etf_strategy/data/processed/market_data.duckdb`
- 表：`market_ohlcv`、`market_universe`、`market_update_log`
- 支持市场：A 股、港股、美股、Crypto
- 支持周期：`1mo`、`1w`、`1d`、`1h`、`30m`、`15m`、`5m`
- 支持数据源：
  - A 股：BaoStock、AKShare
  - 港股/美股：AKShare
  - Crypto：CCXT，且带 OKX/Binance REST fallback
- 支持行情标准化、幂等写入、增量更新、缺失周期过滤、覆盖范围验证
- 支持从 Binance bulk kline 导入
- 支持日线派生周线/月线
- 支持行业信息补充：A 股、港股、美股

当前本地 `market_data.duckdb` 只读统计：

- OHLCV 行数：39,115,093
- 标的数量：3,159
- 总体区间：2021-01-04 至 2026-06-30
- A 股：约 384 个标的，多周期覆盖较完整
- 港股：日线覆盖约 2,764 个标的，分钟/小时级目前主要是 1 个标的
- 美股：日线约 10 个标的，周/月线目前主要是 1 个标的
- Crypto：1 个标的，多周期覆盖

另有 `etf_strategy/data/processed/stock_market_data_2021.duckdb`：

- 日线行数：17,262,561
- 标的数量：15,237
- A 股：5,528 个
- 港股：2,769 个
- 美股：6,940 个
- 数据区间：2021-01-04 至 2026-06-25

### 2.4 Dashboard

已实现本地 HTTP Dashboard：

- 后端入口：`etf_strategy/run_dashboard.py`
- 前端目录：`etf_strategy/web/static/`
- 默认地址：`http://127.0.0.1:8765`

后端 API 当前包括：

- `/api/summary`
- `/api/leaderboard`
- `/api/etf_detail`
- `/api/timeseries`
- `/api/watchlist`
- `/api/theme_heatmap`
- `/api/stock_overview`
- `/api/rolling_plans`
- `POST /api/update`

前端当前包括：

- ETF 总览
- ETF 详情与价格趋势
- ETF 排名榜
- ETF Watchlist
- 主题热力图
- ETF 滚动持仓/换仓模拟
- 股票市场与行业分组概览
- ETF 和股票产品视图切换

### 2.5 测试覆盖

已有测试目录：`etf_strategy/tests/`

当前测试覆盖的主要模块：

- 指标计算
- 评分与候选池
- 入场/出场信号
- 仓位计算
- 回测行为
- ETF/多市场 DuckDB 幂等写入
- 多市场数据 provider 标准化
- 市场数据更新请求构建
- 数据质量校验
- 股票 universe 与缺失周期过滤

当前验证状态：

- 执行 `python -m pytest` 失败，退出码 139
- `PYTHONFAULTHANDLER=1 python -m pytest -q` 显示 Python segmentation fault
- 崩溃发生在 `/opt/miniconda3/lib/python3.13/site-packages/_pytest/capture.py` 的 pytest 启动阶段
- 这更像当前 Python 3.13/conda/pytest 环境问题，不是业务测试断言失败
- 建议使用 Python 3.11 或 3.12 新建虚拟环境后重新验证

## 3. 项目结构

```text
alphaLab/
  ETFStrategy.md                 根目录完整策略规格说明
  HANDOFF.md                     历史交接文档
  PROJECT_OVERVIEW.md            当前项目总览
  etf_strategy/
    README.md                    项目运行手册
    ETFStrategy.md               指向根目录规格的短说明
    requirements.txt             Python 依赖
    run_backtest.py              回测入口
    run_daily_plan.py            每日计划入口
    run_dashboard.py             Dashboard 后端和 API
    config/
      strategy_config.yaml       策略参数
      etf_universe.yaml          ETF 资产类别枚举
      market_data_universe.yaml  多市场行情 seed universe
    src/
      data_loader.py             CSV/Parquet/样例数据加载与标准化
      data_store.py              ETF DuckDB 存储
      market_data_store.py       多市场 DuckDB 存储
      market_data_providers.py   AKShare/BaoStock/CCXT/REST/Synthetic provider
      market_data_updater.py     多市场行情更新编排
      market_data_quality.py     数据质量校验与报告
      market_universe.py         A 股/港股/美股/Crypto universe 发现
      universe.py                ETF 池过滤、主题分类、去重
      indicators.py              技术指标
      scoring.py                 综合评分、候选池、收益矩阵
      signals.py                 入场/出场信号
      position_sizing.py         仓位计算
      portfolio.py               Portfolio/Position 和交易记录
      backtester.py              策略数据准备、回测、输出
      risk.py                    回撤等风险指标
      report.py                  绩效指标、年度/资产类别表现、每日计划
      utils.py                   配置加载和通用工具
    scripts/
      update_etf_data.py                 ETF 数据全量/增量更新
      import_csv_to_duckdb.py            ETF CSV 导入 DuckDB
      update_market_data.py              seed universe 多市场行情更新
      update_stock_data.py               股票 universe 构建与行情更新
      build_market_universe.py           构建市场 universe CSV
      download_universe_market_data.py   下载 universe 行情
      download_a_share_baostock.py       BaoStock A 股下载
      import_binance_bulk_klines.py      Binance bulk kline 导入
      derive_higher_timeframes.py        从日线派生周/月线
      validate_market_data.py            市场数据质量校验
      check_market_scope.py              市场覆盖范围检查
      update_a_share_industries.py       A 股行业更新
      update_hk_us_industries.py         港股/美股行业更新
    web/static/
      index.html                 Dashboard 页面
      app.js                     Dashboard 前端逻辑
      styles.css                 Dashboard 样式
    data/
      raw/                       ETF CSV、元数据、更新摘要
      processed/                 DuckDB、行业 CSV、验证报告
      cache/                     缓存目录
    outputs/
      trades/                    回测交易输出
      reports/                   每日计划、净值、信号、watchlist
      charts/                    图表输出目录
    launchers/
      launch_dashboard.sh
      start_dashboard_daemon.py
      ETFStrategyDashboardLauncher.c
      AppIcon.icns
    tests/                       pytest 测试套件
```

## 4. 常用命令

安装依赖：

```bash
cd /Users/zhoulin/Documents/alphaLab/etf_strategy
pip install -r requirements.txt
```

运行 Dashboard：

```bash
python run_dashboard.py \
  --db data/processed/etf_strategy.duckdb \
  --market-db data/processed/market_data.duckdb
```

运行回测：

```bash
python run_backtest.py \
  --config config/strategy_config.yaml \
  --data data/processed/etf_strategy.duckdb
```

生成每日计划：

```bash
python run_daily_plan.py \
  --config config/strategy_config.yaml \
  --date 2026-06-21
```

增量更新 ETF 数据：

```bash
python scripts/update_etf_data.py \
  --incremental \
  --end-date 2026-07-02 \
  --min-amount 30000000 \
  --min-fund-size 500000000 \
  --db data/processed/etf_strategy.duckdb \
  --no-csv
```

预览股票数据下载计划：

```bash
python scripts/update_stock_data.py \
  --refresh-universe \
  --markets a_share hk us \
  --dry-run
```

运行无网络 synthetic 示例：

```bash
python scripts/update_market_data.py \
  --provider synthetic \
  --years 1 \
  --replace-db \
  --db data/processed/market_data_example.duckdb
```

校验市场数据：

```bash
python scripts/validate_market_data.py \
  --db data/processed/market_data.duckdb
```

检查市场覆盖：

```bash
python scripts/check_market_scope.py \
  --db data/processed/market_data.duckdb
```

## 5. 当前注意事项

- 需要先补 `.gitignore`，避免 `.DS_Store`、DuckDB 大文件、CSV 原始数据和 generated outputs 被误提交
- 当前测试在 Python 3.13 conda 环境中崩溃，建议使用 Python 3.11/3.12 重新建环境验证
- `market_data.duckdb` 中 HK/US 分钟级覆盖有限，使用前需要按目标回测范围校验覆盖率
- 免费行情源存在网络、代理、限频、复权口径和历史分钟数据保留限制
- `market_scope_report.md` 和 `market_data_validation.md` 是已生成报告，使用前最好重新生成
- `launchers/` 是本地 Dashboard 启动便利工具，换机器前应重新验证 macOS 打包/启动行为

