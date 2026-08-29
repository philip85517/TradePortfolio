---
title: 确定历史股票池与价格复权的数据可信度门槛
label: wayfinder:grilling
status: closed
assignee: codex
parent: ../map.md
blocked_by:
  - 01-mainline-scope.md
---

## Question

正式研究是否必须使用带 `[effective_from, effective_to)`、状态、行业分类、来源和 snapshot_id 的 point-in-time universe，并要求同一实验价格序列复权口径一致、重复日线/非法 OHLC/非正价格/缺失历史等质量问题 fail-closed；当前数据不足时只允许标记为 `observed-history` 探索结果，不能输出正式正向证据？需要选择哪一个可审计数据源和覆盖范围来满足这个门槛？

## Resolution

已确认正式研究必须使用 point-in-time universe 和严格的数据质量门槛：

- 股票池采用半开生效区间 `[effective_from, effective_to)`，并记录状态、行业分类、来源和 `snapshot_id`；不能把当前 `market_universe` 快照倒推成历史事实。
- 同一实验的价格序列必须使用兼容且明确的复权口径。重复日线、非法 OHLC、非正价格、缺失必需字段、历史长度不足、未知或混合复权等问题不得静默修复进入正式结果。
- 无法满足上述条件时，运行只能标记为 `observed-history` 探索结果或明确失败，不得输出正式正向证据。
- 具体供应商、数据文件、覆盖年限和更新责任尚未确定，不在本票中假定；将作为独立票据继续决策。正式研究只读取已落盘、带来源身份的快照，运行时不联网下载。
