# 股票组合创建向导：先准备数据，再配置并运行历史模拟

## Problem Statement

用户希望从首页新建一个股票 Portfolio，并选择 2021～2025 年内任意有效起止区间进行历史模拟。当前界面把股票行业浏览、ETF 预置模拟和只读历史审阅分散成不同入口，缺少可完成该目标的创建流程。用户既不知道下一步在哪里，也无法提前判断数据是否足够。

即使界面补上创建表单，如果在用户填写组合、权重和资金后才发现历史行情、指标预热数据或股票池信息不齐，之前的操作仍可能白做。当前“已读取”“暂无结果”等反馈也不能区分数据未准备、规则无候选、约束不可行和运行失败。

核心问题是业务前置依赖没有被明确表达和提前解决，而不仅是缺少日期控件或需要增加几条提示。

## Solution

首页提供明确的主操作“新建股票组合”，进入可保存、可恢复的分步向导。按依赖顺序组织录入：先让系统获得确定数据需求的最少信息，再完成数据准备，最后配置组合资金并运行。复用已就绪数据，不要求每次重新下载，也不要求先下载整个市场 2021～2025 年的所有数据。

| 步骤 | 用户操作 | 系统完成条件 | 下一步 |
|---|---|---|---|
| 1. 选择研究范围 | 选择 A 股、开始/结束日期、手选股票或已有规则；手选录入代码，规则方式选择规则及影响数据需求的参数 | 日期有效、标的身份可解析、数据需求明确；展示实际交易日和所需预热范围 | 检查与准备数据 |
| 2. 准备历史数据 | 查看已覆盖/待补齐内容，点击“补齐所需数据”；有明确缺口时修复范围或重试 | 所选数据质量模式要求满足；行情、预热、股票池及必要元数据通过检查 | 配置组合 |
| 3. 配置组合 | 填写名称、本金、权重、成本与约束；查看候选及可执行持仓预览 | 权重和约束有效，整手成交及现金预览可行；配置对应的数据准备状态仍有效 | 确认并运行 |
| 4. 确认并运行 | 检查股票、日期、资金、成本、数据质量和持有方式摘要，启动模拟 | 后端原子校验数据准备凭据与草稿版本，创建唯一运行任务 | 运行进度 |
| 5. 查看结果 | 查看净值、收益、回撤、现金、交易解释；重新打开或复制配置 | 不可变结果已保存，可从首页运行记录再次打开 | 审阅 / 基于配置创建新运行 |

V1 持有方式固定为“买入并持有至结束日”，界面明确展示，不提供不可用的调仓选项。规则选择使用当前已注册的 fixed_v0；手选股票与规则生成候选是同一研究引擎的两种输入方式。首页同时展示“继续未完成创建”和“最近运行”，不再让用户猜测应该进入哪个 Lab。

示例：选择 2021-03-01～2025-06-30 和股票范围后，先看到“所需范围包含策略预热；18 只就绪、2 只缺少指定日期行情”，可以补齐或修改范围。数据通过后才进入本金和权重配置。刷新页面、补数失败或返回上一步均不清空已输入信息。

## User Stories

1. As a researcher, I want one clearly named stock portfolio creation entry on the home page, so that I know where to start.
2. As a researcher, I want the wizard to show the current step and remaining steps, so that I understand the work ahead.
3. As a researcher, I want to select an explicit start and end date within 2021–2025, so that I can test the historical interval I intended.
4. As a researcher, I want invalid or reversed dates identified immediately, so that I do not discover them after configuring a portfolio.
5. As a researcher, I want requested dates and effective trading dates shown together, so that calendar adjustments are transparent.
6. As a researcher, I want to choose manual stocks or an existing selection rule before data preparation, so that the application can determine the required dataset.
7. As a researcher, I want stock codes resolved with names and listing information, so that invalid and duplicate selections are identified early.
8. As a researcher, I want unsupported markets and selection methods clearly marked, so that I do not follow an unusable path.
9. As a researcher, I want existing local historical data discovered automatically, so that I do not need to understand database paths.
10. As a researcher, I want sufficient existing data reused without downloading it again, so that subsequent experiments are quick to start.
11. As a researcher, I want missing data listed by stock and date range, so that I understand what blocks progress.
12. As a researcher, I want indicator warm-up data included in the preparation check, so that factor calculation does not fail later.
13. As a researcher, I want listing history, adjustment quality and required industry metadata checked early, so that historical results have an honest interpretation.
14. As a researcher, I want one action to fetch the missing historical data from supported sources, so that I do not need to import files manually.
15. As a researcher, I want preparation progress and the current stage displayed, so that I can distinguish slow work from a stalled task.
16. As a researcher, I want partial successes preserved when a provider fails, so that a retry does not repeat completed work.
17. As a researcher, I want unavailable data explained with concrete alternatives, so that I can change scope or retry without guessing.
18. As a researcher, I want later steps locked with a reason while prerequisites are unmet, so that I do not spend time configuring an unusable run.
19. As a researcher, I want exploratory data limitations explicitly acknowledged and retained in results, so that exploratory output cannot be mistaken for strict historical evidence.
20. As a researcher, I want my draft restored after refresh or navigation, so that interruptions do not erase my work.
21. As a researcher, I want to return to an earlier step without losing later inputs, so that correcting a date is inexpensive.
22. As a researcher, I want only affected validations invalidated after editing an earlier choice, so that the wizard avoids unnecessary rework.
23. As a researcher, I want a portfolio name and independent starting capital, so that saved experiments are easy to identify and compare.
24. As a researcher, I want supported weighting choices with immediate validation, so that my intended allocation is represented accurately.
25. As a researcher, I want stock and industry constraints checked against available candidates, so that impossible portfolios are explained before submission.
26. As a researcher, I want lot-size, transaction costs and residual cash included in a holdings preview, so that theoretical weights are not confused with executable holdings.
27. As a researcher, I want no-candidate and unaffordable-holding states explained separately, so that I know whether to change selection criteria or capital.
28. As a researcher, I want a final summary of dates, stocks, capital, costs and data quality, so that I can verify the exact run being submitted.
29. As a researcher, I want repeated clicks or retries to refer to the same submitted run, so that accidental duplicate runs are avoided.
30. As a researcher, I want to leave and reopen a running task, so that I do not need to keep the wizard open.
31. As a researcher, I want actionable run failure diagnostics with my configuration preserved, so that recovery starts from the failed step.
32. As a researcher, I want immutable saved results with their configuration and data identity, so that I can reproduce and review the experiment.
33. As a researcher, I want equity, profit and loss, drawdown, trades and cash displayed for my portfolio, so that I can understand the simulation outcome.
34. As a researcher, I want to copy a previous configuration into a new draft, so that iteration does not overwrite earlier evidence.
35. As a researcher, I want selection review to hide future information even after request failures, so that mode changes cannot mislead my review.
36. As a keyboard user, I want labeled controls, sensible focus movement and announced errors, so that the entire creation flow is operable without a mouse.
37. As a researcher, I want data preparation for stocks independent of ETF freshness, so that an up-to-date ETF database does not block stock research.
38. As a researcher, I want an honest estimated scope and elapsed time when a source cannot provide an ETA, so that progress indicators do not create false confidence.
39. As a researcher, I want terminal or paused task states to survive a server restart, so that the application never leaves a task falsely marked as running forever.
40. As a researcher, I want data changed since validation detected before execution, so that a successful preflight cannot silently authorize a different dataset.

## Implementation Decisions

1. **统一主线。** 扩展现有 HistoricalResearchLab、ResearchSpec、PortfolioSpec、ResearchDataBinding、ResearchRunStore 及研究审阅能力。ETF 模块继续提供数据仓库、provider/updater 和已有纯计算能力，不新建第二套股票回测引擎。首页创建入口导航到统一研究向导；现有冻结审阅页保持只读。
2. **创建草稿与运行分离。** 草稿可编辑、自动持久化；已提交运行和结果不可覆盖。草稿包含 draft_id、revision、研究范围、选股方式、Portfolio 配置、数据质量模式、步骤完成状态及最近验证结果。运行引用冻结配置和数据身份；编辑历史配置须生成新草稿。
3. **范围先于详细配置。** 步骤 1 收集市场、日期、手选股票清单或规则 ID/版本及数据相关参数。手选代码是范围声明，名称/上市状态可在解析后补充；需要元数据才能解析失败时，在此处提供获取元数据动作。规则模式在补行情前确定所需历史 universe，禁止以当前全量股票名单冒充历史可投资域。
4. **V1 选择和权重。** 手选支持等权或显式目标权重；显式权重非负、无重复标的，总和不大于 100%，余额为目标现金，不自动归一化改变用户意图。规则模式支持既有等权/评分权重及约束。评分算法不可在网页编辑。每次向导创建一个独立本金 Portfolio，底层既有多 Portfolio 产物继续兼容。
5. **日期契约。** 新向导的开始日期表示希望开始持有的日期，实际建仓日为该日或之后第一个市场交易日；结束日期映射为该日或之前最近市场交易日收盘。规则信号日为建仓前一个完整市场交易日，因子只读截至该信号日的数据。建仓日与结束日间须至少有一个完整后续交易日，以匹配现有正整数 horizon 契约；否则就地说明无法运行。映射使用可信市场交易日历，不能因单只股票缺行情而误判休市。非交易日调整在步骤 1 和最终摘要中明确显示，不静默调整。
6. **保持既有日期兼容。** 保留原先 CLI 的 as-of 信号日和 horizon 语义；新增区间输入适配，由实际建仓日和结束日计算现有观察周期，输出仍记录请求日期、信号日、建仓日和退出日。不能简单以自然日差充当交易日 horizon，也不能把此次新增契约偷偷改成旧 CLI 行为。
7. **数据需求计划。** 基于范围和规则必需字段/最小历史窗口生成 requirement_id 与确定性需求指纹。计划包含交易日期、预热日期、候选域、价格复权口径、历史上市状态及所选模式所需行业信息。不把所有市场、所有年份下载完作为默认前提。后续约束需要的元数据在此提前声明；纯资金或权重变化不触发行情重下载。
8. **数据就绪必须分层验证。** 复用现有自动绑定与补数能力，但不能仅凭数据库 min/max 日期包围研究区间就认定 READY。检查按标的覆盖、预热窗口、交易日缺口、重复行、非法 OHLC、复权一致性及历史身份。区分休市、上市前、退市后、停牌与未知缺失；未知缺失保持阻塞。正式研究维持既有 PIT/严格质量门禁。
9. **交易不可用情形。** V1 不实现复杂停复牌和退市清算。手选标的在统一建仓日不可交易、期间退市或缺乏可靠估值时提前阻塞并要求用户调整；规则候选中不可执行项须明确列出，并依照冻结规则和最低持仓约束验证，不能静默补入其他股票或悄悄移动个股建仓日。不能用前值伪造可成交行情。
10. **明确探索模式。** 默认正式研究门禁。来源无法提供历史行业/PIT 等严格所需信息时，展示不可补齐原因；用户可显式切换到既有探索模式并接受具体限制后重新校验。模式标记进入草稿、确认页、manifest 和结果。探索也不能绕过必要价格、预热或成交数据缺失。用户手选历史股票清单须声明选择偏差，不宣称代表无偏历史股票池。
11. **补数是后台任务。** 任务有 task_id、目标范围、阶段、已完成/总量（可得时）、已用时间、最近进展、可重试失败列表和状态。状态至少含 QUEUED、RUNNING、SUCCEEDED、PARTIAL、FAILED、CANCELLED、INTERRUPTED。展示来源及阶段名称而非数据库异常堆栈；没有可靠 ETA 时不虚构百分比或剩余时间。
12. **可恢复和幂等补数。** 已完成数据分片保留，失败仅重试缺口；同一需求可复用正在运行或已完成任务。关闭浏览器不取消任务；提供协作式取消，保留已写入有效缓存。服务重启后将未能恢复执行的任务标为 INTERRUPTED，提供继续，不无限显示 RUNNING。防止并发 updater 对同一缓存造成冲突，完整现有行情库只读复用，补数写项目缓存。
13. **就绪凭据。** 数据检查成功返回 readiness_id，绑定需求指纹、数据版本/快照、模式及检查结果。只有明确满足要求的 READY 状态可以解锁组合配置；PARTIAL 不是完成。缓存凭据可复用但执行前必须验证仍有效，不能只信任浏览器的步骤状态。
14. **依赖失效。** 修改市场、日期、股票范围、规则版本或影响数据需求的参数，令数据检查和下游预览失效，保留所有录入值并标出原因，只补新增缺口。修改名称不影响数据检查；修改本金/权重/成本只重算组合预览；涉及新元数据或字段的约束变化只补对应检查。异步响应须匹配草稿 revision 与需求指纹，旧响应不得重新解锁步骤。
15. **资金与可执行预览。** 复用既有整手、现金、佣金/滑点纯计算内核，在提交前展示预期股数、残余现金、买不起一手的标的和不可行约束。此处是回测建仓预览，可使用已确定建仓日开盘价进行成交模拟，但规则评分只能使用信号日前信息。错误如 INFEASIBLE_CONSTRAINTS、INSUFFICIENT_HOLDINGS 映射成就地中文说明和修正动作，不静默放宽约束。
16. **最小工作流服务边界。** 提供统一研究创建工作流接口，覆盖草稿创建/读写、范围解析与预检、补数任务启动/查询/重试/取消、组合预览、运行提交及结果查询。请求变更带 revision 防止过期覆盖；状态冲突返回结构化错误、受影响步骤和恢复动作。运行提交带幂等键，相同提交不生成重复运行，配置变化必须使用新提交身份。
17. **提交与失败语义。** 后端原子校验当前草稿版本、有效 readiness 和组合预览后冻结运行。数据版本不符时回到“重新检查数据”，保留输入；运行中数据快照须固定或通过一致性机制保障不变。运行失败保留配置、失败阶段和诊断；重试显式生成可追溯的新运行，不覆盖失败记录。
18. **首页和步骤反馈。** 首页主按钮、草稿卡片和最近运行均用中文任务名，不把创建功能藏在行业评分页。禁用“下一步”时紧邻显示阻塞原因和解决按钮；步骤内容中分别显示未开始、检查中、可继续、需处理。高级成本参数可折叠，默认值明确，已完成步骤可以返回编辑。自动保存失败须立即提示并提供重试，不能声称已保存。
19. **错误与可访问性。** 字段有 label；验证错误关联字段，进入步骤或错误后移动焦点至相关内容；进度与错误可被辅助技术感知；支持键盘完成流程。小屏幕按单列展示，不以横向大表作为创建必经入口。页面主流程不展示数据库路径和内部异常，诊断详情可展开。
20. **结果与盲审安全。** 结果按独立 Portfolio 展示本金、绝对盈亏、净值、回撤、成本、持仓、交易及数据质量。从运行记录能打开原有审阅。切回选股模式时立即隐藏未来收益与图表，失败保持安全空态；响应必须匹配当前股票/组合/模式，修复已有状态错位，避免创建闭环最终又落到误导性审阅状态。

## Testing Decisions

- 主要验收边界选择 **首页创建向导到保存并重开结果的端到端用户流程**。通过真实工作流 HTTP 接口连接真实研究引擎和临时持久化存储，只在已有行情 provider/updater 注入边界替换确定性数据源。不逐层 mock 草稿、预览和运行服务。
- 复用已有研究流水线、数据自动绑定、HTTP 审阅、运行存储以及纯成交计算测试的结构。只对日期适配和现有纯计算新增少量必要边界测试，不再创建一套平行回测测试架构。
- 好测试验证用户能否继续、输入是否保留、缺口是否准确、输出是否对应指定股票与日期、是否生成唯一且可重开的结果；不验证内部函数调用次数、CSS 类名、组件拆分或实现私有状态。
- 用户已明确确认“符合，按完整向导流程验收”。主要验收边界包含补数失败、刷新续填和修改日期后的重新校验；同时覆盖“数据已就绪直接复用”的快捷路径。

| 用例 | 验收标准 |
|---|---|
| 空库首次创建 | 从首页选择历史范围，显示缺口并阻止进入资金配置；点击补数后完成预检、建仓预览、运行与保存 |
| 全量已就绪 | 不要求额外下载动作；能快速完成同一向导，显示复用的数据范围和质量 |
| 2021～2025 自选区间 | 至少覆盖完整跨度、中间年份区间、同年短区间、周末端点、倒置区间和过短区间；输出实际日期与约定契约一致 |
| 预热不足或标的缺口 | 即使数据库总体最早/最晚日期满足范围，也不能错误解锁；显示缺少的股票与区间 |
| 手选与规则选股 | 两种范围输入均能建立可执行 Portfolio；规则因子不读取信号日后数据，权重/现金/成本可由确定性样本核算 |
| PIT 或行业来源不可得 | 正式模式阻塞并解释；仅在显式切换探索后按探索门槛验证，最终产物保留限制；必要行情缺口仍阻塞 |
| 补数部分失败、取消、恢复 | 已完成部分保存，失败可定位并重试；页面刷新后任务与草稿可恢复；服务重启不会留下假运行状态 |
| 修改上游输入 | 输入保留，旧 readiness 失效，下游不能提交；只补增量需求；改本金不使行情准备重复执行 |
| 旧响应与并发点击 | 旧草稿响应不能覆盖新范围；连续点击启动模拟、提交超时后重试，只关联一个冻结运行 |
| 数据变更后提交 | 拒绝过期 readiness，定位到检查步骤并保留配置；运行使用的实际数据身份与冻结记录一致 |
| 无候选、资金不足、约束冲突 | 在预览阶段分别给出可操作的解释，不创建假成功运行或静默改变策略 |
| 运行失败与历史恢复 | 失败诊断与草稿保留；恢复后创建可追溯运行；成功结果可从首页重开，复制不覆盖旧结果 |
| 审阅隔离 | 正常/失败/乱序模式切换均不在选股视图展示未来收益或过期股票详情 |
| 键盘和窄屏 | 完整向导可通过键盘操作，错误可定位，主要按钮和状态不被裁切 |

网络自动化测试使用确定性历史样本覆盖目标年月；发布前另做一次真实来源的有限股票范围验收，验证实际数据获取链路。真实来源不可用时明确记录阻塞，不能把合成样本通过报告为真实 2021～2025 数据已补齐。

## Out of Scope

- 实盘、Broker/QMT 接入、账户资金变动、下单及自动交易。
- V1 任意调仓策略、动态止损止盈、复杂停复牌撮合与退市清算；本向导先完成明确的区间买入持有。
- 港股、美股、跨市场组合、分钟级行情、汇率结算；首版 A 股日线。
- 网页因子源码编辑、无代码因子 DSL、自动因子挖掘和参数网格优化。
- 新增付费数据源、承诺补齐来源本身不提供的历史数据，或默认全市场全历史下载。
- 全站视觉重做、ETF 策略功能扩展和通用工作流平台。
- 新建多个 Portfolio 的批量向导及新的历史比较分析系统；保留现有研究产物的兼容性。

## Further Notes

- 规格来自本次完整操作评审、用户“新建股票组合并选择 2021～2025 历史区间”的失败场景，以及“先补数据、Wizard 按依赖顺序录入”的最新要求，不依赖再次需求访谈。
- 沿用已有领域决策：股票研究主线、独立本金 Portfolio、不可变运行、正式 PIT 门禁、探索证据限制、次日开盘/整手/成本语义及研究账本分离。本规格新增网页创建工作流、手选股票输入与显式起止区间适配，未将 V1 扩展为持续调仓系统。
- 优先实现顺序是数据需求与就绪契约、可恢复后台补数、创建向导及区间适配、运行结果衔接；可以分阶段开发，但不能把只完成表单展示当成用户目标完成。
- 不保证在数据源不可用时每次模拟都成功；保证先告知可验证的前置阻塞，保留用户投入，提供明确恢复路径，且不以伪造数据或静默降级制造成功。
- 本次交付是规格与 Issue，尚未实施数据补齐或界面改造。
