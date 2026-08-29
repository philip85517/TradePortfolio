---
title: 确定多日期研究与证据标签的统计边界
label: wayfinder:grilling
status: open
parent: ../map.md
blocked_by:
  - 04-data-history-import.md
  - 05-portfolio-backtest-semantics.md
  - 06-reproducible-run-artifacts.md
---

## Question

多日期研究是否固定使用同一因子版本、参数、Portfolio 规则、成本和基准，并按最长观察窗口去除重叠样本；需要输出哪些均值/中位数/胜率/分位数/配对超额收益置信区间，至少多少个有效独立窗口才允许正向证据标签，point-in-time 覆盖和数据质量不通过时如何强制降级为 `DESCRIPTIVE_ONLY`？
