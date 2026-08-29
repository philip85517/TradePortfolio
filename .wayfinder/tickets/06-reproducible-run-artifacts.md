---
title: 定义实验运行的可复现产物与失败语义
label: wayfinder:grilling
status: closed
assignee: codex
parent: ../map.md
blocked_by:
  - 02-factor-contract.md
  - 03-data-contract.md
  - 05-portfolio-backtest-semantics.md
---

## Question

一次实验运行必须保存哪些不可变产物和身份信息，才能从请求日期复现到候选排名、Portfolio、净值、基准和诊断：因子 ID/版本/源码哈希、参数/配置哈希、数据 snapshot/checksum、代码版本、数据区间、完整候选漏斗、运行状态和内容哈希是否全部纳入 manifest；异常、空候选池、前瞻不足和部分数据缺失分别是失败、成功但不可投资还是按周期不可评估？

## Resolution

实验运行采用不可变的 run aggregate，一个运行可以包含多个 Portfolio；每个 Portfolio 独立定义资金基数和配置，策略 Portfolio、基准 Portfolio 或不同权重方案不能共用一个隐含本金。

- 每个 Portfolio 具有稳定的 `portfolio_id`、名称、Portfolio 配置和 `initial_cash`。所有绝对金额指标以该 Portfolio 自己的本金为基数，百分比指标不得混用其他 Portfolio 的本金；跨 Portfolio 比较同时展示金额和百分比，但默认以百分比比较表现。
- 每个 Portfolio、每个观察周期都保存：初始资金、期末可实现资产、绝对盈亏、总收益率、gross 收益率、最大回撤金额、最大回撤比例、日收益波动率、年化收益/波动率（可计算时）、Sharpe（零无风险利率且样本足够时）、胜率、换手、佣金、滑点、基准超额收益及计算状态。净值路径、每日收益/回撤、持仓收益和个股贡献同时落盘。
- 研究历史以运行目录和 `manifest.json` 为事实来源，运行 ID 唯一且只追加；重复实验不得覆盖旧目录。`ResearchRunStore`/CLI 负责按运行 ID 列出、查看、筛选和比较历史运行，比较结果包含 Portfolio 身份、各自本金、绝对盈亏、收益率、回撤和差异。
- manifest 必须记录请求日期、有效信号日、完整 ExperimentSpec、每个 Portfolio 的配置与本金、因子 ID/版本/源码哈希、参数和配置哈希、数据 source/snapshot/checksum、数据区间、代码版本、候选漏斗、运行状态、诊断和所有产物清单/内容哈希。
- 产物至少分为候选结果、Portfolio 定义/持仓、逐 Portfolio 净值、逐 Portfolio/周期指标、个股收益贡献、基准结果和诊断；每张表带 `run_id`、`portfolio_id`，避免不同 Portfolio 的记录混淆。
- 因子/数据契约或系统异常生成 `FAILED` 运行并保存可诊断信息；空候选池是成功但不可投资的 `EMPTY_PORTFOLIO`；前瞻不足按 Portfolio/周期标记 `INSUFFICIENT_FORWARD_DATA`；部分数据缺失不得静默剔除后冒充完整结果，严格模式下该 Portfolio/周期不可评估并记录原因。
