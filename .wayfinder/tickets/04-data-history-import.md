---
title: 自动绑定开源行情并确认 point-in-time 股票池可用性
label: wayfinder:task
status: open
assignee: codex
parent: ../map.md
blocked_by: []
---

## Question

V1 不要求用户手动准备 CSV/Parquet。实现自动数据绑定任务：优先发现并只读复用当前项目、同机 `alphaLab/etf_strategy` 已有的 DuckDB（当前实际可发现 `/Users/zhoulin/Documents/alphaLab/etf_strategy/data/processed/market_data.duckdb` 和 `stock_market_data_2021.duckdb`）；`alphalab_harness_exp` 的 fixture 只用于测试，不作为正式研究数据。对缺失日期自动调用 BaoStock/AKShare 等既有 updater 补齐到项目缓存；统一标准化 `market_ohlcv`、记录 provider/source_dataset_id、校验复权和覆盖范围，并把数据快照指纹写入研究运行 manifest。

同时检查开源数据链路是否能提供带 `[effective_from, effective_to)` 的历史 universe/行业/状态。如果只能得到当前 `market_universe` 快照，就登记为“无法满足正式 PIT”，禁止倒推历史；正式模式失败或降级探索模式时必须显式记录原因。

## Progress (2026-08-29)

- 已完成：新增 `auto_bind_research_db`，默认自动发现当前项目和同机 `alphaLab/etf_strategy` 的已有 DuckDB；兼容旧 `market_ohlcv` schema，按覆盖完整度、A 股 qfq 比例、最新日期和行数选择候选库。
- 已完成：`research run/study/review` 默认 `--db auto`；自动绑定结果写入运行 manifest 的 `diagnostics.data_source`，包括路径、行数、标的数、日期范围、复权/provider 统计、数据指纹和 PIT 标记。
- 已完成：`research run/study` 会根据回看和前瞻窗口调用 `ensure_research_data`；本地覆盖不足时自动调用当前项目已有 `etf_strategy/scripts/update_stock_data.py`，写入当前项目缓存，不修改外部旧库；provider/updater 失败会保留错误原因。
- 已完成：point-in-time 模式在行情库覆盖完整时自动调用 BaoStock `query_stock_basic`，生成当前项目忽略的 `market_universe_history.duckdb` sidecar，以 `[effective_from, effective_to)` 上市区间供研究 adapter 按信号日读取；真实 `research run --universe-mode point-in-time` 已跑通。
- 已完成：运行 manifest 记录 sidecar 路径、`baostock`、snapshot_id、provisioning、数据指纹和 PIT 质量；PIT 运行诊断按股票数统计行业缺失，并将仅有上市区间明确标记为 `listing-only`。
- 已确认限制：BaoStock `query_stock_basic` 只提供上市/退市窗口，`query_stock_industry` 仅给当前分类且没有可回溯的行业生效区间；当前本机开源链路仍不能提供完整历史行业 PIT，不能倒推或回填历史行业。

## Verification (2026-08-29)

- 真实命令：`python -m alphalab research run --as-of 2025-07-01 --horizons 3 --top-n 5 --universe-mode point-in-time --portfolio small=50000 --portfolio large=200000`。
- 结果：自动绑定 `/Users/zhoulin/Documents/alphaLab/etf_strategy/data/processed/market_data.duckdb`，行情覆盖 `2021-01-04 → 2026-06-24`、508,304 条 A 股 qfq 日线；自动生成 `market_universe_history.duckdb`，384 个研究标的上市区间可按信号日读取。
- 结果状态：研究、Portfolio、前瞻回测和产物写入全部成功；PIT 质量为 `listing-only`，因为 384 个标的均没有历史行业分类，正式正向证据仍须保持 `DESCRIPTIVE_ONLY`。
