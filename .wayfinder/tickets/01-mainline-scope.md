---
title: 确定因子研究闭环的主线与 V1 验收边界
label: wayfinder:grilling
status: closed
assignee: codex
parent: ../map.md
blocked_by: []
---

## Question

当前仓库同时有 `alphalab/research` 股票历史截面研究线和 `etf_strategy` ETF 轮动线。是否将前者定为“筛选因子定义 → Portfolio → 回测”闭环的唯一 V1 主线，后者只作为行情数据基础设施/既有策略参考，并将验收范围固定为单市场 A 股日线、下一交易日开盘、固定买入并持有观察窗口？

## Resolution

用户已确认：以 `alphalab/research` 作为“筛选因子定义 → Portfolio → 回测”闭环的唯一 V1 主线；`etf_strategy` 仅作为行情数据基础设施和既有 ETF 策略参考。V1 范围固定为单市场 A 股日线、下一可交易日开盘、固定买入并持有观察窗口，不包含实盘交易。
