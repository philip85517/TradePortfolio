---
title: 选择 point-in-time 数据源与正式研究覆盖范围
label: wayfinder:grilling
status: closed
assignee: codex
parent: ../map.md
blocked_by:
  - 03-data-contract.md
---

## Question

正式 point-in-time 股票池、行业分类和复权日线具体采用哪一个可审计来源、快照文件格式、历史覆盖起止日期、更新责任和验收覆盖率？推荐先接受用户提供或供应商导出的 CSV/Parquet 快照，通过 `source + snapshot_id` 固化身份，不在研究运行时联网；覆盖范围至少要覆盖所有计划的历史截面、最长回看窗口和最长前瞻窗口，缺口必须报告而不能补造。

## Resolution

已确认 V1 采用冻结文件作为正式研究数据输入，不在研究运行时绑定或调用在线 provider：

- 股票池、行业分类和状态数据由用户或供应商导出为 CSV/Parquet，记录 `source`、`snapshot_id`、来源记录时间和文件身份；股票池区间使用 `[effective_from, effective_to)`。
- A 股日线使用单一、明确且全实验兼容的复权口径，V1 优先采用同一来源的前复权数据；未知、混合或无法证明兼容的口径不得进入正式研究。
- 文件覆盖范围必须覆盖所有计划信号日之前的最大因子回看窗口、信号日后的最大观察窗口和下一可交易日建仓日；覆盖缺口要报告为不可评估，不能用在线下载、当前快照倒推或合成数据补造。
- 研究运行只读已落盘快照；数据导入、schema/区间/重复/覆盖校验由独立任务完成。具体文件、实际日期覆盖、责任人和 snapshot_id 在导入任务完成后登记。

## Amendment (2026-08-29)

用户明确否决 V1 的手动 CSV/Parquet 导入流程，改为自动绑定已有开源数据。原决策中的“来源身份、统一复权、覆盖缺口不可补造、正式研究不得使用当前快照冒充历史”继续有效，但数据获取方式改为：

- 优先自动发现本机已有、只读打开的 `market_data.duckdb` / `stock_market_data_2021.duckdb`，兼容 `market_ohlcv` 和 `market_universe`；默认优先覆盖所需日期更完整且日线为 qfq 的库。
- 现有本地 `alphaLab/etf_strategy` 已提供可复用的 provider/updater：A 股 universe discovery + BaoStock 日线，港股/美股 AKShare，其他市场沿用其既有路由。研究入口在缓存缺失或覆盖不足时自动调用补数流程，用户不需要准备或导入文件。
- 自动写入的项目缓存必须经过统一 schema、重复、OHLCV、日期覆盖和复权校验；运行 manifest 记录实际 DB 路径、provider、source/source_dataset_id、查询窗口、行数、更新时间和数据指纹，保证结果可追溯。
- 现有本地库的 `market_universe` 只有一次 `discovered_at` 当前快照，没有 `[effective_from, effective_to)` 历史；因此它可以支持行情自动绑定和探索回测，但不能单独满足正式 PIT universe。不能用当前快照倒推历史，也不能用合成数据填洞。
- `04-data-history-import.md` 改为自动数据绑定/PIT 可用性任务：先从本地开源数据链路获取并校验；若开源链路无法提供 PIT 历史，正式模式必须报告不可评估或失败，探索模式必须显式标记。
