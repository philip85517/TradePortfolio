## Problem Statement

用户在股票组合研究向导的“5. 运行与结果”中，无法迅速回答：这次研究赚亏多少、过程承担了多大风险、何时买卖、为什么调仓、没有替补成功究竟是系统故障还是策略结果。

当前页面只突出任务完成、三个数值和长交易记录，历史净值与回撤需要进入另一张以候选池为主的审阅页。初始持仓、后续交易与期末状态分离；技术原因码、缺失指标和冗长数值增加理解成本。“运行成功”也容易被误认为“全部清算且结果完整”。

本次真实运行已经保存 707 个日终权益点、收益汇总和退市执行事件，却没有将它们组织成可用的组合复盘界面。用户要求在原入口内完成专业、清晰的结果展示，借鉴 TradingView 的图表与交易记录交互，而非另建一个名称相近的 Portfolio 产品。

## Solution

将原向导第五步升级为“组合复盘”，让用户按三个层级理解结果：

1. **先读结论**：固定展示研究身份、收益、期末权益、风险及结算状态，并用事实型短句解释关键交易。
2. **再看过程**：大幅历史净值主图、同步回撤副图和交易事件标记；支持十字线、缩放、范围选择及与事件详情双向定位。
3. **最后查原因**：按“触发 → 执行条件 → 实际成交 → 替补选择 → 最终结果”展示调仓过程；明细、口径及来源渐进展开。

首屏结构：运行标题与研究区间；一行关键指标；事实摘要；图表工具栏；净值主图与回撤副图；下方“交易与调仓 / 初始与期末持仓 / 研究设置与证据”。桌面上选中事件可展开右侧详情，窄屏改为图表下方详情。首屏不以 JSON、全量日志或另一页跳转作为主要内容。

用户无需重跑研究即可查看新界面。原有独立审阅页继续提供个股分析，但不是查看组合收益的前置步骤。

## User Stories

1. As a portfolio researcher, I want the completed run to open a portfolio review in the original wizard, so that I do not have to find another product or page.
2. As a portfolio researcher, I want to see the portfolio name, research interval and saved run identity, so that I know exactly which experiment I am reading.
3. As a portfolio researcher, I want total return and profit or loss together, so that percentages and money have a clear relationship.
4. As a portfolio researcher, I want initial capital and ending equity displayed together, so that I can reconcile the result.
5. As a portfolio researcher, I want maximum drawdown near the return, so that I can assess the risk of the path.
6. As a portfolio researcher, I want annualized return with its calculation context, so that I do not confuse it with the total return.
7. As a portfolio researcher, I want execution completion and liquidation status distinguished, so that a successful task does not imply all assets were sold.
8. As a portfolio researcher, I want a short factual summary of consequential events, so that I understand the result before reading every trade.
9. As a portfolio researcher, I want the complete historical NAV chart directly on the result page, so that I can see how the portfolio evolved.
10. As a portfolio researcher, I want to switch between unit NAV, equity and cumulative return, so that I can examine the same path in useful units.
11. As a portfolio researcher, I want synchronized NAV and drawdown charts, so that a difficult period is easy to locate.
12. As a portfolio researcher, I want a crosshair tooltip with date and exact values, so that I can inspect a point without guessing from the axis.
13. As a portfolio researcher, I want zoom, pan, range shortcuts and reset, so that I can investigate a period and return to the full run.
14. As a portfolio researcher, I want chart-range changes clearly separated from the full-run metrics, so that zooming does not silently change the reported return.
15. As a portfolio researcher, I want weekly and monthly candles aggregated from daily closing NAV, so that I can use a candlestick-style view without fabricated intraday prices.
16. As a portfolio researcher, I want chart labels to explain the candle construction, so that I know what its high and low actually mean.
17. As a portfolio researcher, I want buy and sell markers on the portfolio path, so that I can relate actual transactions to portfolio changes.
18. As a portfolio researcher, I want decisions and unsuccessful attempts marked differently from fills, so that a selected replacement is not mistaken for a purchase.
19. As a portfolio researcher, I want multiple events on one date grouped and expandable, so that markers remain readable without losing information.
20. As a portfolio researcher, I want selecting a chart marker to open its trade or decision, so that I can immediately understand what happened.
21. As a portfolio researcher, I want selecting an event row to locate the same date on the chart, so that the table and chart tell one story.
22. As a portfolio researcher, I want the trigger, execution constraints and outcome of each rebalance explained, so that I can distinguish strategy intent from actual execution.
23. As a portfolio researcher, I want the announcement date, first executable date and actual sale date shown separately, so that delays are understandable.
24. As a portfolio researcher, I want replacement rank cutoff and selection evidence visible, so that I can verify that future information was not used.
25. As a portfolio researcher, I want net sale proceeds, costs and the replacement budget explained together, so that I can understand an unaffordable replacement.
26. As a portfolio researcher, I want no-candidate, suspension, one-price and insufficient-lot outcomes explained in Chinese, so that I know why no trade occurred.
27. As a portfolio researcher, I want ordinary end-of-study exits distinguished from strategy rebalances, so that I do not overcount strategy activity.
28. As a portfolio researcher, I want initial fills and initial non-fills included in the review, so that the starting exposure is understandable.
29. As a portfolio researcher, I want to filter events by stock, action, reason and date, so that I can investigate a specific trade without scrolling through unrelated events.
30. As a portfolio researcher, I want stock names, codes, quantities, prices and fees displayed consistently, so that I can read and compare trades quickly.
31. As a portfolio researcher, I want initial holdings and ending holdings labeled separately, so that historical purchases are not presented as current positions.
32. As a portfolio researcher, I want cash, remaining market value and unsettled shares distinguished, so that incomplete valuation is not presented as a complete return.
33. As a portfolio researcher, I want price basis and simulation-cost assumptions available near trade details, so that I can interpret a research execution price correctly.
34. As a portfolio researcher, I want missing metrics to include a reason rather than zero or unexplained dashes, so that missing evidence is not confused with a poor strategy.
35. As a portfolio researcher, I want incomplete or stale valuation segments identified on the chart, so that apparent continuity does not hide missing information.
36. As a portfolio researcher, I want saved runs to remain unchanged when I open or filter them, so that the review is reproducible.
37. As a portfolio researcher, I want older runs to show everything their saved evidence supports, so that a UI upgrade does not force a costly backtest rerun.
38. As a portfolio researcher, I want loading, failure, cancellation and retry states to be clear, so that a chart-loading problem is not mistaken for a failed backtest.
39. As a portfolio researcher, I want an offline fallback and an accessible data table, so that I can still review my results without the external chart library.
40. As a portfolio researcher, I want keyboard access and readable narrow-screen layouts, so that chart interactions are not limited to precise mouse use.
41. As a portfolio researcher, I want visible benchmark comparisons only when frozen comparable data exists, so that an invented benchmark does not mislead me.
42. As a portfolio researcher, I want downloadable NAV and event records with the run identity, so that I can independently reconcile the displayed results.
43. As a portfolio researcher, I want exports and summaries to state their scope, so that filtered events are not mistaken for all activity.
44. As a portfolio researcher, I want refreshing or revisiting the original draft to restore its saved result, so that I can resume the review without resubmitting a run.

## Implementation Decisions

### 1. 入口、信息架构与视觉层级

- 改造现有研究向导第五步及其只读结果展示能力；保留既有草稿、运行身份和路由。默认 Portfolio 始终指用户指定的 8787 股票组合研究向导。
- 完成态标题采用“组合复盘”；运行中仍显示阶段、耗时与连接状态，任务结束后收为紧凑状态条。结果内容不能被完成日志挤出首屏。
- 桌面首屏指标优先级：总收益率、盈亏金额、期末权益、最大回撤；次级展示初始本金、年化收益、佣金、滑点、现金与未平仓状态。金额与百分比有清晰单位。更多统计置于可展开区域。
- 显示研究请求区间与实际建仓/结束日期；观察周期使用“持有交易日”，不把 706 日理解为自然日。多组合或多观察周期运行使用显式选择器，所有区域共享同一个选择。
- 默认明亮、低噪声研究界面；用中性色区分层级，涨跌色配合正负号，买卖和决策用文字与形状共同识别；不依赖颜色单独表达状态。
- 1440×900 桌面首屏应看到标题、核心指标和主要图表，无水平页面滚动；主图建议至少 360px 高，回撤副图约 120px。窄屏指标换行，详情堆叠，宽表仅在自身容器滚动。
- 原技术 JSON、完整执行参数和来源明细放入“研究设置与证据”，保留诊断价值，不作为默认阅读材料。

### 2. 净值、回撤与 K 线的真实口径

- 复用冻结的组合日终权益与已有指标。单位净值 = 日终权益 / 本组合初始本金；累计收益 = 单位净值 − 1。默认展示单位净值，可切权益（元）和累计收益（%）。不可用首个日终点归一化，否则会丢失建仓首日收益与成本。
- 初始本金基准显示为 1.0 / 初始本金 / 0% 参考线或独立标记，不虚构额外交易日，不改变原始净值点数。
- 回撤展示冻结序列与冻结汇总的定义。图表不得套用外部平台的盘中回撤算法重新解释现有日终回撤；如果旧汇总与重建的路径定义不同，标明口径而不是静默改值。
- 默认 1D 日终净值线/面积图。1W、1M 可切“日终净值聚合 K 线”：开 = 周/月首个实际交易日的日终净值，收 = 最后一个日终净值，高/低 = 该周期内日终净值的最大/最小值。周期按市场日历周/月分组，末尾不完整周期明确标注。
- 上述 K 线并非真实盘中 OHLC，也不是成分股最高/最低价的简单加总。图表标题与提示持续说明该口径。没有真实组合日内数据时，日 K 选项不可用并说明原因；禁止使用前日收盘、当日收盘凑出假日 K。
- 权益与单位净值的周/月 K 可按相同口径聚合；累计收益视图保持折线，避免不同量纲下产生模糊的 K 线含义。
- 横轴使用真实交易日期。缺失值不能当作零、跨缺口连成完整收益、用后续价格插值，或把停牌估值当作真实成交。已确认的陈旧估值显示其证据和陈旧时长；未结算边界后不显示伪完整总收益。
- 聚合中存在应有日缺口时，该周期不生成看似完整的蜡烛，显示缺失提示；周末与市场休市不算缺口。
- 图表范围缩放不改变顶部全研究汇总。范围控件提供全部、近 1/3/6 个月、近 1 年及自定义日期（均相对研究结束日）；超出研究范围时裁切并提示。周期切换、重置和事件定位不触发回测或行情补数。
- 净值与回撤共用时间范围和十字线；悬停显示日期、单位净值/权益、累计收益、当日收益、回撤及当日事件数。缺失字段逐项显示原因，不阻断已有曲线。
- 不默认新增基准。只有当前运行已冻结同组合、同区间、可比较口径的基准时才显示开关；否则明确“本次运行未保存基准”。不使用今日股票池补算基准。

### 3. TradingView 风格图表能力

- 沿用已引入的 TradingView Lightweight Charts 能力，扩展到组合曲线、聚合蜡烛、回撤和事件标记。当前代码锁定 4.2.3；实现必须使用与锁定版本兼容的 API，不直接混用官网最新主版本示例。
- 采用独立、可释放的图表展示组件，处理窗口尺寸变化、隐藏面板重显、组合切换及销毁；重复渲染不得累积图表、订阅或相互递归的时间轴同步。
- 图表资源优先从应用本地提供并保留版本与必要署名。即使资源加载失败，仍显示可读曲线/表格、核心指标和事件列表，不能出现空白结果页。
- 样式借鉴专业图表工具的层级、十字线和主副图联动，不嵌入一个无关证券的 TradingView Widget 来冒充本地组合，不增加账户登录要求。

### 4. 买卖节点与调仓原因

- 同一结果展示模型提供稳定事件 ID 和可选调仓链 ID。保留动作、日期精度、代码/名称、数量、研究成交价格、净回款/总成本、佣金、滑点、价格口径、原因、触发证据、排名截止、预算、结果状态与来源身份。缺失信息有可读的 unavailable reason。
- 已成交买/卖、初始未成交、顺延、选择替补、取消与保留现金是不同事件类型。交易笔数只计真实模拟成交，不计公告、选择、顺延或现金事件；期末退出与策略触发卖出可区分统计。没有完整平仓配对时，不新造胜率、盈亏比或每笔贡献。
- 买卖标记定位在组合净值的对应日期，纵向锚定组合值，不把个股价格放到组合净值轴。点击后在详情显示个股成交价格与其口径。公告/决策日期若没有净值点，在独立事件轨道显示真实日期，不挪成一个虚假成交日。
- 同一天多事件显示计数和事件选择列表；切周/月时聚合标记仍可展开真实逐日时间，保持稳定 ID 与可定位性。
- 初始买入由冻结成交记录或冻结可执行持仓与成本契约支持；旧运行缺少显式事件时可生成“由冻结建仓记录还原”的派生展示事件，不修改旧产物。理想目标权重或候选入选本身不是已买入证据。无法可靠还原时明确没有成交明细。
- 以结构化证据和确定性中文模板生成事实摘要，不调用模型编造因果或重新解释策略。推荐链路文案：“正式退市决定公开 → 公告明确复牌前等待 → 首个可成交日退出 → 按当日收盘可见数据排名 → 下一交易日因预算不足一手而保留现金”。
- 调仓详情区分“为什么触发”“为什么当天能/不能成交”“为什么选择该替补”“结果与资金去向”。展示公告可见日期、真实生效区间、排名截止、候选得分/排名（有冻结证据时）及净回款预算。未保存的候选价格或一手成本不能从最新行情补写。
- 每笔交易优先展示股票名称与代码、日期、动作、股数、模拟价格、净现金变化、费用和一句话原因。价格最多显示 4 位小数，金额 2 位，百分比 2 位；内部对账保留原精度。未知原因保留可诊断代码并说明“暂无中文解释”，不显示空白成功。
- 连续顺延默认收为一条日期区间摘要，可展开原事件；原始日期与事件数不能丢失。卖出、选择、保留现金组成一条调仓过程，但各自的成交/决策状态保留。
- 图表事件、列表与详情双向联动：点击列表定位并突出图表日期；点击标记定位对应行；筛选后不能让详情停留在另一运行/组合的旧事件。支持键盘聚焦、Escape 收起和清晰焦点样式。

### 5. 汇总、状态与数据真实性

- 本次研究使用模拟成交模型：卖出按建仓原始价锚定的后复权总回报序列，替补买入按已验证未复权开盘价并计费用。图表、成交表、说明统一表达口径；“研究成交价格”不冒充真实逐笔成交。
- 结果状态至少区分运行中、运行失败、完成且清算、完成但仍有可估值未平仓、存在未结算股份导致完整收益不可确定、空组合与证据不足。前端不把任务 SUCCEEDED 直接映射为全部清算。
- 完整收益不可确定时，以已知现金、已知资产、未结算股票及原因作为主要信息；已知资产不称为完整期末权益。可估值未平仓时注明收益含未实现部分。
- 初始建仓和研究期末持仓并列呈现，不混为“当前持仓”。期末空仓应显示“已全部清算”；现金不是缺失数据。不要求本期新增每日成分持仓回放。
- 费用沿用研究模型已有的佣金和滑点，不新增未计入的税费并暗示已计入。Sharpe、胜率、个股贡献等未生成指标移出主指标卡，展开后说明原因。
- 展示价差、百分比和数据修正限制，不把来源错误修正或 PIT 证据限制作为交易触发原因；原模式及相关限制随运行保存并在设置区可见。

### 6. 数据接口与兼容性

- 以既有“按运行读取冻结组合详情”的只读接口为唯一主要数据边界，按 run_id、portfolio_id 和观察周期读取汇总、净值、初始持仓、期末状态及执行事件。扩展该结果投影，不另建独立结果数据库或第二套收益计算引擎。
- 仅增加有版本的展示元数据、能力标志、事件规范化和可解释缺失原因；保持旧客户端及旧运行可读取。周/月聚合由统一投影规则产出或统一纯展示转换完成，前后端不得分别维护不同定义。
- 查看结果绝不发起策略重算、行情网络补数、账本交易或旧 manifest 写入。展示派生事件与周/月聚合不覆盖冻结事实，派生来源可追溯。
- 图表加载失败与运行失败分开处理：可独立重试结果读取；已加载汇总与列表继续可用；重新运行策略始终是显式独立动作。
- NAV 和事件导出带运行、组合、观察周期、口径和是否应用筛选；数值保留足够对账精度。复用已有下载能力，缺失导出仅在同一只读数据边界补充。CSV 的自由文本需防公式注入，网页字段按文本转义。
- 对外链接仅作为证据阅读入口；无敏感账户数据或本机路径进入面向用户的主视图。

## Testing Decisions

- **主要验收接缝：原向导的已保存运行 → 只读组合详情 → 可见复盘行为。** 优先复用研究审阅 HTTP 集成测试、向导状态恢复测试、现有页面行为测试与人工浏览器验收。仅为聚合数学及事件规范化增加少量确定性边界用例，不逐个 mock 图表内部函数。
- 已向用户提出上述验收边界核对。按“原入口全流程、冻结数据对账、异常状态、离线与旧运行兼容”作为规格的默认验收范围；后续反馈可直接补充到本 Issue。
- 好测试验证外部可见行为和数据不变量：显示正确结果、点击后正确联动、无伪成交/伪价格、网络失败可恢复、旧文件哈希不变。不能仅断言函数被调用、模板包含某字符串或插件对象存在。
- 使用小型冻结夹具覆盖：全部清算、未平仓估值、退市未结算、初始不成交、卖出后成功替补、买不起一手、无候选、连续停牌顺延、同日多事件、没有事件的旧运行、缺少 NAV、部分 NAV 缺口，以及多个组合与多个观察周期。
- 数学验收：初始资金不等于首个收盘权益时，单位净值仍按初始资金计算；周/月 OHLC 等于该周期日终序列的首/末/最大/最小；不完整周期与中间缺口可区别；图表末点与冻结汇总对账，不因缩放或过滤改变全期收益。
- 事件验收：真实成交计数排除 SELECT/DEFER/CASH；初始未成交不产生买入标记；聚合标记可恢复准确日期；列表与图表互相定位；旧运行的派生初始事件标明来源；不存在的预算、成交价格和因果说明不能伪造。
- UI 验收：默认首屏突出核心指标和组合主图，原向导刷新恢复结果，桌面与 390px 窄屏可读；同日事件不会重叠到无法点击；键盘可选择事件；切换组合/周期/范围后不残留旧状态；连续切换不累积图表或监听器。
- 离线/错误验收：禁用图表资源仍可读结果、导出数据并理解降级状态；结果接口失败不丢已显示汇总、不触发重跑；缺失值不变成零，不跨未知区间连线；未知原因码和不可信字符串安全显示。
- 真实运行回归采用研究 `research-20260910T182300883182Z-f5a894775d`：本金 100,000 元，707 个日终点，期末权益 62,158.38008203717 元，总收益 −37.84161991796283%，最大回撤按冻结值显示约 −52.43%。首次日终权益 102,748.94841765001 元，对应单位净值约 1.027489484，而非 1。
- 该运行必须清楚展示：002336 在 2025-06-09 至 06-12 顺延，06-13 卖出 500 股净回款 304.75604575 元；06-13 选择 300204，06-16 因不足一手保留现金；此链不存在替补买入成交。保留 9 只初始实际持仓及 002186 初始未成交说明；期末已全部清算。真实验收只读既有结果，不要求重复耗时回测。
- 性能目标：本地运行的 707 点、15 条事件结果在接口响应后 1 秒内可交互；运行于既有桌面环境时，初次完整结果加载目标 3 秒内。额外用约 5,000 日终点与 1,000 事件的固定夹具验证：不渲染全部详情、不持续全图重建，交互后无明显长时间冻结；记录环境与实测值，不把主观“流畅”作为唯一标准。

## Out of Scope

- 不修改选股、退市卖出时点、替补排名、资金预算、成本或复权策略；不为改善收益而重跑、删除或改写历史研究。
- 不新增真实组合日内 OHLC、分钟回测、盘中最大回撤或将成分股日 K 拼成虚假组合 K 线。
- 不接实盘账户、模拟账户账本、实时行情、Pine Script、策略编辑器或 TradingView 商业终端。
- 不新增不存在的基准、每日成分持仓回放、自动归因、胜率/盈亏比/个股贡献计算或机器生成投资建议。
- 不更换为其他 Portfolio Workspace、ETF Portfolio Lab 或 Quant Workbench，不重新设计选股向导前四步。
- 不全面仿制 TradingView 页面，不在本期升级整个前端技术栈，不要求用户购买图表服务。
- 本次交付是可实施规格和 Issue；实际界面改造由该 Issue 执行，不把规格发布宣称为功能已上线。

## Further Notes

- 优先级：P0 为原结果页关键汇总、日终净值/回撤、交易链说明及正确的状态；P1 为本规格同时要求的周/月聚合 K 线、范围与事件联动、导出和响应式完善。P0/P1 表示实现顺序，全部属于本 Issue 验收范围。
- 已核对现有研究决策：独立组合本金、冻结运行只读、实际成交/现金残留、不同周期隔离、信号日与事后评估分离。旧决策中的全股票池基准要求以当前向导“无冻结基准则不展示/不补算”的既有行为为准，本规格不恢复已放弃的基准计算。
- 展示上的“复盘”指已完成研究的事后查看，不能让原选股审阅模式提前读取信号日后的数据。
- 参考 TradingView Strategy Report 的指标总览、权益图、悬停信息与交易清单组织；使用适合当前组合日终研究模型的口径，而不照搬其每笔交易统计或盘中回撤定义：[Strategy Report — How to start](https://www.tradingview.com/support/solutions/43000764138-tradingview-strategy-report-how-to-start/)。
- 图表方案参考官方事件标记能力：[Lightweight Charts — Series markers](https://tradingview.github.io/lightweight-charts/tutorials/how_to/series-markers)。该教程包含最新版本接口，实施以当前锁定版本为准：[Lightweight Charts 4.2](https://tradingview.github.io/lightweight-charts/docs/4.2)。
- TradingView 风格不等于取得商业 Advanced Charts 使用权；本期使用现有开源 Lightweight Charts，并保留该版本要求的署名与链接。
