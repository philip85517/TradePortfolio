# AlphaLab ETF 轮动模拟交易闭环

对应文档：[AlphaLab ETF量化策略盈利闭环实施 Spec](../AlphaLab%20ETF量化策略盈利闭环实施%20Spec.md)

这是 SPEC-ETF-PAPER-001 的本地可执行实现，覆盖 P0 最小闭环：

```text
统一策略核心（纯函数）
→ 目标组合与订单计划
→ 模拟撮合（T+1 开盘 + 滑点/佣金/整手/先卖后买）
→ SQLite 账本（订单/成交/现金/持仓/净值/异常）
→ 对账与 Markdown 日报
→ 历史回放 / 回测 / 一致性对比
```

## 快速开始

### 1. 环境

```bash
# 本项目使用项目虚拟环境（复用 conda 的 pandas/numpy）
/opt/miniconda3/bin/python3.13 -m venv --system-site-packages .venv
./.venv/bin/pip install -r alphalab/requirements.txt
```

### 2. 一键端到端验证（合成行情，不依赖任何数据）

```bash
./.venv/bin/python -m alphalab demo --synthetic --start 2026-06-01 --end 2026-07-03
```

`demo` 使用临时账本，自动播种默认标的池，跑完打印成交笔数、异常数与期末资产。

### 3. 运行测试

```bash
./.venv/bin/python -m pytest
```

包含单元测试、集成测试（prepare→execute→reconcile→report）、回测-回放一致性测试与黄金样本测试。

> 说明：本机 conda Python 3.13 的 readline 在 macOS 上会导致 pytest 段错误，
> `pytest.ini` 已禁用 capture/debugging 插件规避；其他环境可去掉 `addopts`。

## 历史截面因子研究（固定 V0 原型）

当前原型把选股规则固化为一个可复现的 V0 实验：在指定历史日期使用当时可见的
A 股日线，过滤 20 日日均成交额与 MA60 条件，按动量/成交额综合分数选 Top 10，
在下一交易日开盘建仓，并观察 21/42 个交易日的收益和最大回撤。研究过程只读行情，
不会写入模拟交易账本。

```bash
./.venv/bin/python -m alphalab research run \
  --as-of 2025-07-01 \
  --runs-dir alphalab/reports/research
```

默认自动发现本机已有的开源行情 DuckDB；覆盖不足时会调用现有 BaoStock updater 补到当前项目缓存，
不要求手动导入 CSV/Parquet。使用 `--universe-mode point-in-time` 时会自动生成上市/退市历史 sidecar；
当前开源链路没有历史行业分类时，运行会明确标记为 `listing-only`。
若同时指定 `--data-quality-mode strict`，缺少历史行业生效区间会直接失败并保存 `FAILED` 诊断；不使用手工文件倒推历史。

运行时会优先复用已绑定行情库中的 `market_universe` 行业字段；缺少时，CLI 自动调用既有
`update_a_share_industries.py` 的 BaoStock provider，把当前行业快照写入本项目的
`market_industry_snapshot.duckdb` sidecar。该快照只用于当前展示和筛选，不会被冒充为历史 PIT 行业；
只有 `market_universe_history` 同时具备有效区间和完整行业层级时，manifest 才会标记
`point_in_time_industry=true`。

每次运行都会创建一个新的不可覆盖目录，包含候选筛选、组合持仓、逐日净值、
`portfolio_metrics.csv`（按组合/周期的本金、绝对盈亏、收益率、回撤、波动、Sharpe 和成本）以及
`manifest.json`（规则契约、参数、数据质量与数据快照）。请求日期不是交易日时，自动回退到之前最近的有效信号日。

使用运行 ID 启动只读候选审阅页：

```bash
./.venv/bin/python -m alphalab research review \
  --run-id <RUN_ID> \
  --runs-dir alphalab/reports/research \
  --db auto
```

页面默认处于“选股审阅”模式，后端不会返回信号日后的行情；点击“事后评估”后才会显示
未来蜡烛、建仓点、21/42 日结果和组合上下文。评估模式下可按组合切换器查看每个 Portfolio 的独立持仓、净值、绝对盈亏、收益率、回撤与成本；按 `↑/↓` 可在当前候选列表中切换股票。
左侧目录可折叠以扩大主区。股票技术图使用开源 Lightweight Charts；行情仍以日线为唯一源，
在浏览器内确定性聚合为 `1D / 1W / 1M`，并支持成交量、EMA5、EMA20、EMA60 开关以及缩放/拖动。

研究运行支持自定义观察周期、成本、整手和严格质量门禁；当前规则插件为 `fixed_v0`：

```bash
./.venv/bin/python -m alphalab research run \
  --as-of 2025-07-01 \
  --horizons 21,42,63 \
  --commission-rate 0.0003 \
  --slippage-rate 0.0005 \
  --data-quality-mode strict
```

一个实验可以定义多个独立本金的 Portfolio；重复 `--portfolio` 即可：

```bash
./.venv/bin/python -m alphalab research run \
  --as-of 2025-07-01 \
  --portfolio small=50000 \
  --portfolio large=200000
```

显式注册的自定义因子需要声明版本、支持市场、必需字段、最小历史窗口、分数方向和参数 schema；
通过 `factor_params`/`score_with_params` 接口接收参数，排名与组合构建仍由研究引擎统一完成。

可以通过命令行管理和比较冻结运行：

```bash
./.venv/bin/python -m alphalab research list
./.venv/bin/python -m alphalab research show --run-id <RUN_ID>
./.venv/bin/python -m alphalab research compare --left <BASELINE_RUN_ID> --right <NEW_RUN_ID>
./.venv/bin/python -m alphalab research study \
  --as-of 2024-01-05,2024-04-05,2024-07-05 \
  --horizons 21,42
```

## 使用真实行情

本地已存在 `etf_strategy/data/processed/etf_strategy.duckdb`（约 2021-06 至 2026-07 的 ETF 日线）。
加载器会自动发现该 DuckDB，其次读取 `etf_strategy/data/raw/etf_daily_5y.csv`。

```bash
# 播种默认目标标的（10 只常见 ETF，标记为真实行情）
./.venv/bin/python -m alphalab universe seed
./.venv/bin/python -m alphalab universe list

# 回测与回放一致性对比（真实行情）
./.venv/bin/python -m alphalab compare --start 2026-06-01 --end 2026-07-02
```

## 添加目标标的

```bash
# 添加标的（自动识别交易所后缀；无本地真实行情时标记为合成数据）
./.venv/bin/python -m alphalab universe add 510300.SH 159915.SZ 518880.SH

# 强制使用合成行情
./.venv/bin/python -m alphalab universe add 510300.SH --synthetic

# 停用 / 启用 / 移除
./.venv/bin/python -m alphalab universe disable 510300.SH
./.venv/bin/python -m alphalab universe enable 510300.SH
./.venv/bin/python -m alphalab universe remove 510300.SH
```

标的池持久化在 `alphalab/config/paper_universe.yaml`。`synthetic: true` 的标的
运行时强制使用确定性合成行情（seed 固定，可复现）；`false` 优先使用本地真实行情，
缺失时才回退合成并写入数据快照。

## 每日模拟运行（Spec 第 12 节）

```bash
export ALPHALAB_DB=/path/to/paper_trading.db   # 默认 alphalab/storage/paper_trading.db

# 0) 初始化模拟账户（默认 10 万元；重复初始化会被拒绝）
./.venv/bin/python -m alphalab.paper init-account

# 1) T 日收盘后生成 T+1 交易计划
./.venv/bin/python -m alphalab.paper prepare --date 2026-06-26

# 2) T+1 日按开盘价模拟成交
./.venv/bin/python -m alphalab.paper execute --date 2026-06-29

# 3) 对账（资产/现金/持仓恒等式，异常写入账本）
./.venv/bin/python -m alphalab.paper reconcile --date 2026-06-29

# 4) Markdown 日报（同时输出到终端）
./.venv/bin/python -m alphalab.paper report --date 2026-06-29

# 查看账户状态
./.venv/bin/python -m alphalab.paper status --date 2026-06-29
```

同一天重复执行 `prepare`/`execute` 会被拒绝（`--force` 可重跑，产生新的 run_id，
历史账本不会被静默修改）。订单唯一键 =
`strategy_id + signal_date + execution_date + symbol + side + generation_version`。

## 历史回放 / 回测 / 一致性

```bash
# 历史回放（自动跳过非交易日，逐日 prepare → execute → reconcile）
./.venv/bin/python -m alphalab replay --start 2026-06-01 --end 2026-07-02 --reset-account

# 内存回测（与模拟共用同一策略函数和撮合纯函数）
./.venv/bin/python -m alphalab backtest --start 2026-06-01 --end 2026-07-02

# 回测 vs 回放逐日对比（总资产/现金差异 <= 0.01 元，持仓数量完全一致）
./.venv/bin/python -m alphalab compare --start 2026-06-01 --end 2026-07-02
```

## 设计要点

- **统一策略核心**：`strategies/etf_rotation_v0.py` 为纯函数，回测/模拟/未来实盘共用；
  禁止回填未来数据（数据日期 > signal_date 立即失败）。
- **撮合规则唯一**：`brokers/paper_broker.py::simulate_fills` 是回测与模拟共用的成交
  规则（T+1 开盘、滑点、佣金、整手向下取整、先卖后买、资金约束）。
- **账本即事实**：SQLite 记录 runs/signals/orders/fills/cash_ledger/positions/daily_nav/anomalies；
  每次运行记录策略版本、配置哈希、代码版本与数据快照 ID。
- **幂等**：订单唯一键 + “同日已成功执行”双重保护；重跑必须显式 `--force` 且产生新 run_id。
- **节假日安全**：执行日按“信号日之后第一个有行情的日期”确定，而非简单工作日推算。
- **对账**：资产恒等式、现金流水连续性、持仓守恒、重复订单/成交、订单-成交关联、
  净值连续性，超过容差记为 FATAL 并将运行标记为 FAILED。

## 目录结构

```text
alphalab/
├── config/                  # 策略/账户/标的池 YAML
├── data/                    # 行情加载（DuckDB/CSV/合成）、标的池管理
├── strategies/              # 统一策略核心（纯函数）
├── portfolio/               # 目标组合权重、订单规划、风险检查
├── brokers/                 # Broker 接口 + PaperBroker 撮合
├── storage/                 # SQLite 账本
├── validation/              # 数据/账户/一致性校验
├── paper/                   # prepare/execute/reconcile/report/replay + CLI
├── backtest/                # 内存回测引擎
├── reports/paper/           # Markdown 日报输出
└── tests/                   # 单元 / 集成 / 回放一致性 / 黄金样本
```

## 已知限制（与 Spec 一致）

- 交易日历按行情数据实际日期推断；法定节假日/调休不单独维护。
- 成交模型为 V0：开盘价全额成交，无盘口深度、无部分成交、无涨跌停检查。
- 现金流水把佣金并入 BUY/SELL 净额（可通过 fills 表追溯滑点与佣金明细）。
- 规模/流动性过滤使用当时成交额与元数据；历史规模数据缺失时以最新元数据近似并记录快照。
- QMT 实盘、Web UI、自动调度等属于 P1+，不在本实现范围。

## 股票组合创建向导（数据准备优先）

从首页点击 **新建股票组合**。也可以独立启动，无需提前生成研究运行：

```bash
python -m pip install -r alphalab/requirements.txt
python -m alphalab research workbench --port 8787
```

打开 `http://127.0.0.1:8787/wizard`。已有 ETF 仪表盘也在同一个端口提供 `/wizard`。

1. 选择 A 股、开始/结束日期，以及手选股票或 `fixed_v0` 规则。
2. 检查历史数据，查看缺少的标的、日期和质量要求，再点击补齐。数据通过后才进入组合配置。
3. 设置名称、本金、权重和成本，检查整手持仓及剩余现金预览。
4. 确认区间和数据质量，运行买入持有模拟。
5. 从运行记录打开保存的净值、收益、回撤和只读股票审阅结果。

开始日期表示期望建仓日期，系统展示实际交易日、前一交易日信号和结束日映射；原先 CLI 的 `--as-of` 信号日含义不变。
向导默认正式研究质量门禁。数据源不能提供历史 PIT 信息时会阻塞并说明原因；用户可显式选择探索模式，但探索也不能跳过必要的价格缺口，产物会保留限制。

草稿与任务保存在 `alphalab/reports/workbench`，冻结结果仍在 `alphalab/reports/research`。刷新页面可以续填；修改日期只重新验证相关数据依赖。已下载的有效缓存保留，取消或补数失败后可以重试。

可选参数：`--db /path/to/market.duckdb` 指定已有行情库；`--workspace-dir /path/to/workbench` 改变草稿和缓存位置；`--runs-dir /path/to/runs` 改变冻结结果目录。

首版支持 A 股日线、单 Portfolio 和区间买入持有；不包含动态调仓、实盘或跨市场交易。数据源暂时不可用时，页面会保留输入并显示阻塞，不能保证任意历史区间均可从开源来源补齐。


## 数据检查失败后的处理

研究向导第二步按“可自动处理 / 需核实 / 需要你处理”展示问题，支持按股票、类型、阶段筛选，逐股覆盖每页 25 只，并可导出原始诊断。按钮由服务端修复计划决定：有真实缺口才下载，零成交量但状态不明时先核实交易状态。已确认停牌、持有期退市和当前来源无法补齐的历史行业证据不会被当作普通下载失败反复重取。

准备任务保留已验证缓存和动作记录，网络暂时失败最多尝试三次。重试只处理当前仍可执行的动作；相同无效响应不会在任务内反复循环。取消、页面刷新或连接暂时中断后，可重新获取状态并继续。修改范围会解除旧任务摘要和旧预览，已保存历史记录不受影响。

探索模式必须显式选择，仅接受列出的 PIT 证据限制；行情缺失及停牌、退市等当前执行能力限制仍会阻止继续。当前版本不提供停牌估值和退市清算，不会自动删除相关股票或改变日期来让研究通过。

修复只写工作台缓存，价格分片先校验再原子发布；新响应缺少量额时保留同股票同日期已有的有效量额，零值不会被当作空值填充。`cache/repair_attempts/` 保存不可覆盖的请求与结果证据。新版恢复流程兼容旧草稿，执行前仍重新校验范围版本和数据身份。
