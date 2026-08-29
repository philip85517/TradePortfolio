---
title: AlphaLab 历史截面因子到 Portfolio 回测闭环
label: wayfinder:map
status: open
---

# AlphaLab 历史截面因子到 Portfolio 回测闭环

## Destination

在 `alphalab/research` 主线上，形成一条可复现、不可使用未来信息的 A 股日线研究闭环：用户定义并版本化筛选因子 → 生成指定历史截面的候选排名与解释 → 构建一个或多个有明确约束和成本口径、各自独立资金基数的 Portfolio → 下一可交易日开盘建仓并按固定前瞻窗口回测 → 保存可审阅、可比较、可复现的研究产物。完成标准是单日期闭环可信、多日期研究能诚实表达证据强弱；不包含实盘交易。

## Notes

- 领域：历史截面选股因子研究、Portfolio 构建、固定持有期前瞻回测。
- 已确认的主线：`alphalab/research` 是因子研究与回测主线；`etf_strategy` 提供行情仓库和既有策略参考，不再并行扩展第二套因子闭环。
- 已确认的因子边界：采用显式注册的本地 Python 插件；插件只负责历史截面过滤/评分，研究引擎负责聚合、排名、Portfolio 和前瞻评估。
- 已确认的数据门槛：正式研究使用带生效区间和来源身份的 point-in-time 快照；复权和行情质量不合格时 fail-closed，当前快照只能支持探索模式。
- 已确认的数据来源：V1 不要求用户手动导入文件。研究入口自动发现已有 DuckDB；覆盖不足时自动复用 `etf_strategy` 的 BaoStock/AKShare 等 provider/updater 写入项目缓存，再由研究适配器读取标准化日线。每次运行仍必须记录 provider、数据集身份、实际覆盖和复权口径。
- 已确认的 PIT 限制：本机现有 `market_universe` 只有当前快照，没有历史生效区间；不得用当前快照倒推历史。正式研究若无法自动获得 PIT universe，必须 fail-closed 或明确标记为探索结果。
- 已确认的 Portfolio/回测语义：整数股/整手、保留现金、次日开盘成交、固定持有期、终点卖出成本和同 universe 等权基准；研究与模拟交易只共享纯成交成本内核。
- 已确认的运行记录语义：一个实验可包含多个 Portfolio，每个 Portfolio 独立定义本金；绝对盈亏、收益率、回撤和常见研究指标按 `portfolio_id + horizon` 分开保存，并通过不可变运行历史查询比较。
- 必须继续参考的规范：`AlphaLab 历史截面因子研究与组合回测 Spec.md`、`docs/superpowers/plans/2026-08-29-point-in-time-universe.md`。
- 研究与模拟交易账本保持分离；V1 先做单市场、日线、买入并持有固定观察窗口。
- 当前证据：完整测试 `111 passed`，`git diff --check`、`compileall` 和前端 JS 语法检查通过；真实 A 股 `research run --universe-mode point-in-time` 已自动读取本机开源 DuckDB、调用 BaoStock 生成 5,549 条上市区间 sidecar，并完成多 Portfolio 的 Top N/整手成交/现金残留/绝对盈亏/百分比收益/回撤/成本指标落盘。运行 manifest 已包含行情路径、qfq/provider 统计、数据指纹、PIT sidecar/snapshot、行业缺失诊断；只读审阅页与 `ResearchRunStore` 已支持按 `portfolio_id` 查询、比较持仓和指标。该证据仍不等于完整行业 PIT；当前开源链路没有历史行业生效区间，因此多日期结果保持 `DESCRIPTIVE_ONLY`。

## Decisions so far

- [确定因子研究闭环的主线与 V1 验收边界](tickets/01-mainline-scope.md) — V1 统一走 `alphalab/research`，范围锁定为 A 股日线、次日开盘、固定持有期；ETF 线不再并行扩展。
- [锁定可迭代筛选因子插件与评分聚合契约](tickets/02-factor-contract.md) — 采用显式注册 Python 插件；插件只返回历史截面因子结果，聚合和安全校验由引擎统一负责。
- [确定历史股票池与价格复权的数据可信度门槛](tickets/03-data-contract.md) — 正式研究必须使用 point-in-time 生效区间和明确复权口径；数据不足时降级为探索结果或失败，不发布正式证据。
- [统一 Portfolio 构建、成交成本与前瞻回测语义](tickets/05-portfolio-backtest-semantics.md) — 采用整数股/整手和现金残留，次日开盘建仓、固定持有期终点退出；同 universe 等权为 V1 基准，共享纯成本内核而不共享账本。
- [定义实验运行的可复现产物与失败语义](tickets/06-reproducible-run-artifacts.md) — 一个运行可包含多个独立本金 Portfolio；按 Portfolio/周期保存绝对与相对指标、净值和回撤，并以不可变 run 历史查询比较。
- [选择 point-in-time 数据源与正式研究覆盖范围](tickets/09-pit-data-source.md) — V1 改为自动绑定现有 DuckDB/开源 provider；统一复权与来源身份仍需写入运行产物，PIT universe 缺失不得被当前快照替代。

## Not yet specified

- 自动发现已有 DuckDB、覆盖检查、缺覆盖时调用既有 updater/provider 补数、来源指纹写入 manifest、BaoStock 上市区间 sidecar 和多 Portfolio 独立本金/绝对指标产物已落地。现有开源链路仍只有上市状态 PIT；行业历史分类缺失时运行会标记 `listing-only`，多日期证据不得据此标成正向正式证据。
- 首个自定义因子的具体假设、参数默认值和对应验收样本尚未确定；网页因子编辑、无代码 DSL 和自动挖掘已明确不在范围内。
- 首个可执行 Portfolio/回测内核已能完成固定 `fixed_v0` 单日期闭环，实际股数按整手向下取整并保留现金残留；严格质量模式、失败留痕和外部指数基准边界已明确，外部指数基准暂不属于 V1。
- 多 Portfolio 配置、按组合/周期的本金、绝对盈亏、收益率、回撤、波动率、Sharpe、成本、历史列表/比较和审阅页切换均已落地；后续只需继续扩充历史行业数据源，不再用当前行业快照倒推历史。
- 多日期研究已经输出有效样本、重叠窗口诊断和证据门槛；在完整行业 PIT 数据源接入前，结果继续保持 `DESCRIPTIVE_ONLY`。
- `point-in-time + strict` 已对缺少历史行业生效区间 fail-closed，并保存 `FAILED` 运行诊断；探索模式仍允许跑通闭环，但不升级证据等级。

## Out of scope

- 实盘交易、Broker/QMT 对接、账户同步、下单与自动调度。
- 盘中/分钟级因子、跨市场组合、汇率和跨时区结算。
- 网页因子源码编辑、无代码规则搭建、自动因子挖掘、网格搜索和 AutoML。
- 观察窗口内动态再平衡、止损止盈和复杂成交撮合。
- V1 外部指数/市场基准标的接入。
