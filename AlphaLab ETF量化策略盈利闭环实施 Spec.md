# AlphaLab ETF量化策略盈利闭环实施 Spec

**文档编号：** SPEC-ETF-PAPER-001  
**版本：** v0.1.0  
**状态：** 可执行基线  
**适用项目：** AlphaLab  
**核心策略：** ETF日线轮动  
**当前优先级：** P0  
**启动阶段：** 模拟交易与一致性验证

---

## 1. 文档目的

本 Spec 用于指导 AlphaLab 从“策略研究与历史回测”进入“每日模拟执行、持续验证及小资金实盘”的完整迭代过程。

本阶段不以扩大策略数量、增加复杂模型或建设完整交易平台为目标，而是围绕一条 ETF 日线轮动策略，建立一条可追踪、可复现、不可作弊的量化策略盈利证据链：

```text
策略定义
→ 历史回测
→ 样本外验证
→ 历史模拟回放
→ 每日模拟交易
→ 回测与模拟一致性验证
→ 小资金实盘
→ 收益归因
→ 策略继续迭代或淘汰
```

最终需要回答的不是“策略历史收益看起来是否不错”，而是：

> 在没有未来信息、考虑真实交易约束和成本后，这套策略是否能够稳定地产生可执行的净收益。

---

# 2. 背景与当前问题

## 2.1 当前已有基础

AlphaLab 已具备或计划具备以下能力：

- ETF候选池管理；
- ETF评分与排序；
- 日线策略回测；
- 组合滚动与模拟持仓；
- QMT交易环境；
- `macdkd_15min_v0` 策略；
- ETF轮动策略设计；
- Python本地研究和运行环境；
- 策略配置文件和自动化运行设想。

当前 ETF 轮动策略已有大致框架：

- 非杠杆、非主动型ETF；
- 设置上市时间、规模和流动性过滤；
- 使用60日收益、20日风险收益和有效移动等指标；
- 持有排名靠前的3至5只ETF；
- 使用趋势过滤、EMA回踩、ATR仓位或止损；
- 以日线或周度频率进行调整。

## 2.2 当前主要问题

当前最大的风险不是缺少策略想法，而是：

1. 回测、模拟交易和未来实盘可能存在三套不同代码；
2. 回测结果可能包含未来函数或不现实的成交假设；
3. 缺少长期保存的订单、成交、现金和持仓账本；
4. 缺少逐日对账机制；
5. 缺少回测与模拟回放的一致性验证；
6. 缺少每日自动生成的交易计划和归因报告；
7. 过早建设平台、UI、Agent和参数搜索，导致策略本身迟迟没有走完闭环；
8. 多条策略同时推进，无法形成一条完整且可信的盈利证据链。

---

# 3. 总体目标

## 3.1 北极星目标

围绕 ETF 日线轮动策略，建立一套可以从历史研究稳定迁移至模拟交易和小资金实盘的执行系统。

系统必须做到：

```text
相同市场数据
+ 相同策略配置
+ 相同成交规则
=
历史回测、历史回放、每日模拟三者逐日一致
```

在此基础上，再验证策略在真实交易环境中是否存在可持续的净收益。

## 3.2 阶段目标

### 阶段一：工程可信

证明系统本身没有重复下单、现金错账、持仓错账、未来数据和回测模拟不一致等问题。

### 阶段二：策略可信

证明 ETF 轮动策略在样本外、成本压力测试和参数扰动下仍有合理表现。

### 阶段三：执行可信

证明每日生成的信号、订单计划、模拟成交和真实市场行情可以闭环运行。

### 阶段四：实盘可信

通过小资金实盘确认真实滑点、手续费、成交率和人工执行行为不会吞噬回测收益。

---

# 4. 成功标准

## 4.1 工程成功标准

系统必须满足：

- 任意历史日期均可重复回放；
- 相同输入重复运行得到相同结果；
- 同一订单不会因程序重复运行而重复成交；
- 任意一天的总资产均可由现金和持仓解释；
- 任意一笔现金变化均可追溯到成交或调整记录；
- 任意一笔持仓变化均可追溯到成交记录；
- 所有运行均记录策略版本、配置哈希和代码版本；
- 数据日期严格限制在信号日期及以前；
- 回测和模拟回放在相同假设下逐日一致。

## 4.2 策略成功标准

进入实盘前，策略至少满足：

- 样本外扣除成本后收益为正；
- 相比简单的60日动量基线具有可解释的增量；
- 收益不是只来自单一年份或单一ETF；
- 交易成本翻倍后策略不立即失效；
- 信号延迟一个交易日后表现不发生断崖式下降；
- 40、60、90日等相邻参数结果方向相对一致；
- Top3、Top5、Top7持仓数量结果不存在完全相反的结论；
- 周度和双周调仓结果具有一致性；
- 最大回撤位于可接受范围；
- 收益改善不是依赖孤立的最优参数点。

## 4.3 模拟执行成功标准

连续模拟运行至少20个交易日，并满足：

- 无无法解释的现金差异；
- 无无法解释的持仓差异；
- 无重复订单和重复成交；
- 无负现金；
- 无超额卖出；
- 所有异常均有显式状态；
- 模拟滑点与回测假设位于同一量级；
- 每日均可生成交易计划、成交记录、持仓和归因报告。

---

# 5. 核心原则

## 5.1 单一主线原则

当前唯一盈利验证主线为：

> ETF日线轮动策略。

其他方向的定位如下：

| 方向 | 当前定位 |
|---|---|
| ETF日线轮动 | 唯一P0盈利主线 |
| `macdkd_15min_v0` | QMT执行链路测试 |
| Al Brooks / PA量化 | 后续研究储备 |
| 深度学习与复杂多因子 | 暂缓 |
| 大规模参数搜索 | 暂缓 |
| AlphaLab完整Web平台 | 暂缓 |
| Agent自动研究 | 暂缓 |

## 5.2 策略与执行分离

策略只输出：

- 信号；
- 目标持仓；
- 目标权重；
- 评分；
- 排名；
- 原因。

策略不得：

- 直接写数据库；
- 直接调用QMT；
- 直接修改现金；
- 直接模拟成交；
- 自行读取未来数据；
- 在内部隐式修改配置。

## 5.3 唯一策略核心

回测、模拟交易和未来实盘必须调用相同的策略核心。

禁止分别维护：

```text
backtest_strategy.py
paper_strategy.py
live_strategy.py
```

正确结构为：

```text
统一策略核心
├── 回测执行器
├── 模拟交易执行器
└── QMT实盘适配器
```

## 5.4 先可验证，再自动化

实现顺序必须是：

```text
手动运行正确
→ 历史回放正确
→ 每日手动模拟正确
→ 定时自动运行
→ QMT只读接入
→ QMT小资金实盘
```

不得一开始直接自动下单。

## 5.5 先账本，后界面

SQLite账本是系统状态的唯一事实来源。

- SQLite：账户、订单、成交和运行状态；
- Parquet：行情、因子和指标快照；
- YAML：策略和账户配置；
- Markdown/CSV：给人阅读的报告；
- Web UI：后续增强项，不作为第一阶段依赖。

---

# 6. 本阶段范围

## 6.1 本期必须实现

### 策略层

- ETF轮动策略纯函数；
- ETF候选池生成；
- 指标计算；
- ETF评分和排名；
- 目标组合生成；
- 信号原因输出；
- 策略版本与配置管理。

### 模拟执行层

- T日收盘后生成T+1交易计划；
- 按T+1开盘价模拟成交；
- 滑点和手续费；
- 先卖后买；
- 整手处理；
- 资金不足处理；
- 无行情和不可成交处理；
- 订单状态机；
- 模拟账户更新。

### 账本层

- 运行记录；
- 信号记录；
- 订单记录；
- 成交记录；
- 现金流水；
- 持仓快照；
- 每日净值；
- 异常记录。

### 验证层

- 单日历史回放；
- 连续日期历史回放；
- 回测与回放逐日比较；
- 现金恒等式检查；
- 资产恒等式检查；
- 重复订单检查；
- 数据日期检查；
- 配置和版本追溯。

### 报告层

- 每日账户摘要；
- 今日成交；
- 当前持仓；
- 下一交易日计划；
- 收益归因；
- 异常检查；
- 回测与模拟差异报告。

## 6.2 本期不实现

以下内容明确不属于P0范围：

- 完整Web交易平台；
- 实时盘口撮合；
- Tick级或分钟级模拟成交；
- 多账户管理；
- 多策略资金分配；
- 杠杆交易；
- 融资融券；
- 复杂部分成交模型；
- 自动参数寻优；
- 强化学习；
- 深度学习预测；
- Agent自动修改策略；
- QMT自动实盘下单；
- 多用户权限；
- 云端部署；
- 分布式任务调度。

---

# 7. 总体架构

```text
┌──────────────────────┐
│ 市场数据与ETF元数据   │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ 数据日期截断与质量检查 │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ ETF轮动统一策略核心    │
│ 候选池/因子/评分/排名   │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ 目标组合与信号         │
│ target portfolio      │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ 订单计划器             │
│ target → delta order  │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────────────┐
│ BrokerAdapter                │
├──────────────────────────────┤
│ BacktestBroker               │
│ PaperBroker                  │
│ QMTBroker（后续）             │
└──────────┬───────────────────┘
           │
           ▼
┌──────────────────────┐
│ 订单、成交、现金账本   │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ 持仓、净值、对账、归因 │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ Markdown/CSV日报       │
└──────────────────────┘
```

---

# 8. 建议目录结构

```text
alphalab/
├── config/
│   ├── etf_rotation_v0.yaml
│   └── paper_account.yaml
│
├── data/
│   ├── market/
│   ├── metadata/
│   └── snapshots/
│
├── strategies/
│   ├── base.py
│   └── etf_rotation_v0.py
│
├── portfolio/
│   ├── target_portfolio.py
│   ├── order_planner.py
│   └── risk_checks.py
│
├── brokers/
│   ├── base.py
│   ├── backtest_broker.py
│   ├── paper_broker.py
│   └── qmt_broker.py
│
├── paper/
│   ├── cli.py
│   ├── pipeline.py
│   ├── prepare.py
│   ├── execute.py
│   ├── reconcile.py
│   ├── replay.py
│   └── report.py
│
├── storage/
│   ├── database.py
│   ├── models.py
│   └── paper_trading.db
│
├── validation/
│   ├── account_checks.py
│   ├── data_checks.py
│   ├── parity_checks.py
│   └── anomaly_checks.py
│
├── reports/
│   └── paper/
│
└── tests/
    ├── unit/
    ├── integration/
    ├── replay/
    └── fixtures/
```

第一阶段不要求一次性建立全部目录，可以先实现最小结构：

```text
strategies/etf_rotation_v0.py
paper/prepare.py
paper/execute.py
paper/replay.py
storage/database.py
paper/report.py
```

---

# 9. 策略核心接口

## 9.1 输入

统一策略函数接收：

```text
signal_date
截至signal_date的市场数据
ETF元数据
当前持仓
账户净值
策略配置
```

## 9.2 输出

统一返回：

```text
信号列表
目标持仓列表
诊断信息
```

建议接口：

```python
from dataclasses import dataclass
from datetime import date
from typing import Sequence


@dataclass(frozen=True)
class TargetPosition:
    symbol: str
    target_weight: float
    score: float
    rank: int
    signal: str
    reason: str


@dataclass(frozen=True)
class StrategyResult:
    signal_date: date
    targets: Sequence[TargetPosition]
    diagnostics: dict


def generate_target_portfolio(
    signal_date: date,
    market_data,
    etf_metadata,
    current_positions,
    total_equity: float,
    config: dict,
) -> StrategyResult:
    ...
```

## 9.3 强制约束

策略函数必须满足：

- 无数据库写入；
- 无全局可变状态；
- 无当前时间隐式依赖；
- 无网络请求；
- 无QMT调用；
- 输入相同，输出必须相同；
- 市场数据必须在调用前截断到 `signal_date`；
- 输出必须包含人类可读原因；
- 输出权重之和不得超过100%；
- 不在目标组合中的标的默认目标权重为0。

---

# 10. 策略分层设计

ETF轮动策略拆分为三层，每一层必须可独立开关。

## 10.1 核心Alpha层

用于验证横截面趋势和动量是否存在收益。

默认逻辑：

- 流动性和上市时间过滤；
- 使用60日收益或综合评分；
- 每周固定时间调仓；
- 选择Top5；
- 等权持有；
- 不满足绝对趋势时持有现金。

## 10.2 风险控制层

可选模块：

- MA60绝对趋势过滤；
- 最大单标的权重；
- ATR风险仓位；
- 组合现金储备；
- 组合最大回撤保护；
- ETF类别集中度约束。

## 10.3 交易优化层

可选模块：

- EMA20回踩入场；
- 排名缓冲；
- 最小调仓阈值；
- 延迟买入；
- 分批建仓；
- ATR止损。

## 10.4 实验顺序

固定运行以下六组：

| 编号 | 策略定义 | 验证目的 |
|---|---|---|
| B0 | 宽基ETF买入持有 | 市场基准 |
| B1 | 60日动量Top5、等权、周度调仓 | 简单策略基线 |
| S0 | 综合评分Top5 | 验证综合评分增量 |
| S1 | S0 + 绝对趋势过滤 | 验证趋势风控 |
| S2 | S1 + EMA20回踩 | 验证入场优化 |
| S3 | S2 + ATR仓位与止损 | 验证风险预算 |

禁止在第一轮实验中大规模搜索参数。

---

# 11. 默认配置

```yaml
strategy:
  id: etf_rotation_v0
  version: 0.1.0
  rebalance_frequency: weekly
  rebalance_weekday: 4
  max_positions: 5

universe:
  min_listing_days: 180
  min_aum_cny: 500000000
  min_avg_turnover_20d_cny: 30000000
  exclude_leveraged: true
  exclude_inverse: true
  exclude_active: true

alpha:
  ranking_method: composite
  momentum_window: 60
  short_window: 20
  top_n: 5

trend_filter:
  enabled: true
  moving_average_window: 60

entry:
  ema_pullback_enabled: false
  ema_window: 20

portfolio:
  weighting_method: equal_weight
  max_single_weight: 0.20
  reserve_cash_pct: 0.05
  rebalance_threshold_pct: 0.00

risk:
  atr_position_sizing_enabled: false
  atr_stop_enabled: false
  allow_negative_cash: false

account:
  initial_cash: 100000

execution:
  signal_time: close
  execution_time: next_open
  price_type: open
  slippage_bps: 5
  commission_bps: 3
  min_commission_cny: 0
  lot_size: 100
  allow_partial_fill: false
  reject_if_price_missing: true
  sell_before_buy: true

validation:
  asset_balance_tolerance_cny: 0.01
  cash_balance_tolerance_cny: 0.01
  reject_future_data: true
```

所有配置变更均必须提升策略版本或形成新的运行配置哈希。

---

# 12. 每日运行流程

日线模拟交易拆成两个主要时点。

## 12.1 T日收盘后：生成交易计划

执行命令：

```bash
python -m alphalab.paper prepare --date 2026-08-24
```

程序依次完成：

1. 检查交易日；
2. 更新截至T日的行情数据；
3. 校验数据完整性；
4. 将市场数据截断至T日；
5. 读取当前模拟持仓；
6. 计算ETF候选池；
7. 计算因子、评分和排名；
8. 生成目标持仓；
9. 比较当前持仓和目标持仓；
10. 生成T+1订单计划；
11. 保存信号和目标组合快照；
12. 保存运行版本、配置哈希和代码版本；
13. 输出下一交易日计划报告。

T日生成的订单不得使用T日收盘价作为已成交价格。

## 12.2 T+1日：模拟成交

执行命令：

```bash
python -m alphalab.paper execute --date 2026-08-25
```

程序依次完成：

1. 读取执行日对应的待执行订单；
2. 获取T+1真实开盘价；
3. 检查是否有行情；
4. 检查是否可成交；
5. 先执行卖单；
6. 更新卖出现金；
7. 根据剩余现金计算买单；
8. 按交易单位向下取整；
9. 计算滑点；
10. 计算手续费；
11. 生成成交记录；
12. 更新现金流水；
13. 更新持仓；
14. 保存日终持仓快照；
15. 计算日终净值；
16. 执行自动对账。

## 12.3 T+1日收盘后：对账和报告

执行命令：

```bash
python -m alphalab.paper reconcile --date 2026-08-25
python -m alphalab.paper report --date 2026-08-25
```

输出：

- 账户摘要；
- 今日订单；
- 今日成交；
- 当前持仓；
- 现金流水；
- 收益与成本；
- 下一交易日计划；
- 异常和警告。

---

# 13. 目标持仓转订单规则

## 13.1 计算目标市值

```python
target_value = total_equity * target_weight
```

## 13.2 计算当前市值

```python
current_value = current_quantity * reference_price
```

## 13.3 计算调仓差额

```python
delta_value = target_value - current_value
```

## 13.4 买入数量

```python
raw_quantity = delta_value / reference_price
buy_quantity = floor_to_lot(raw_quantity, lot_size=100)
```

## 13.5 卖出数量

```python
raw_quantity = abs(delta_value) / reference_price
sell_quantity = min(
    available_quantity,
    floor_to_lot(raw_quantity, lot_size=100),
)
```

## 13.6 订单生成约束

- 先生成卖单，后生成买单；
- 卖出数量不得超过可卖数量；
- 买入不得使现金为负；
- 不足一手不生成订单；
- 目标权重变化为0时不生成订单；
- 无行情时订单状态为 `REJECTED`；
- 禁止静默跳过；
- 资金不足时允许按优先级或同比缩减；
- 缩减逻辑必须固定、可复现。

---

# 14. 模拟成交模型

## 14.1 默认成交价格

买入：

```python
fill_price = open_price * (1 + slippage_bps / 10000)
```

卖出：

```python
fill_price = open_price * (1 - slippage_bps / 10000)
```

## 14.2 V0成交假设

- 使用下一交易日开盘价；
- 有行情且满足基本交易条件时全部成交；
- 不模拟盘口深度；
- 不允许部分成交；
- 无开盘价则拒绝；
- 不使用收盘价自动替代开盘价；
- 不允许执行日以前的价格；
- 所有滑点和费用均记录在成交表中。

## 14.3 后续可扩展能力

P1以后可增加：

- 开盘后VWAP；
- 成交额容量约束；
- 部分成交；
- 涨跌停检查；
- 买卖价差；
- ETF品种差异化成本；
- QMT真实成交回报。

---

# 15. Broker接口

```python
from abc import ABC, abstractmethod


class BrokerAdapter(ABC):

    @abstractmethod
    def get_cash(self, trade_date):
        ...

    @abstractmethod
    def get_positions(self, trade_date):
        ...

    @abstractmethod
    def submit_orders(self, orders):
        ...

    @abstractmethod
    def get_orders(self, trade_date):
        ...

    @abstractmethod
    def get_fills(self, trade_date):
        ...
```

第一阶段实现：

```python
PaperBrokerAdapter
BacktestBrokerAdapter
```

后续实现：

```python
QMTBrokerAdapter
```

策略代码禁止直接调用QMT的 `passorder` 等接口。

---

# 16. SQLite账本设计

## 16.1 `runs`

用于保存每次运行的信息。

```text
run_id
run_type
as_of_date
strategy_id
strategy_version
config_hash
code_commit
data_snapshot_id
started_at
finished_at
status
error_message
```

`run_type`：

```text
PREPARE
EXECUTE
RECONCILE
REPORT
REPLAY
BACKTEST
```

`status`：

```text
RUNNING
SUCCESS
FAILED
CANCELLED
```

## 16.2 `signals`

```text
signal_id
run_id
signal_date
symbol
signal_type
score
rank
target_weight
reason
created_at
```

## 16.3 `orders`

```text
order_id
run_id
strategy_id
signal_date
execution_date
symbol
side
planned_quantity
reference_price
planned_value
target_weight
order_status
reason
created_at
updated_at
```

订单状态：

```text
PLANNED
SUBMITTED
FILLED
PARTIALLY_FILLED
REJECTED
CANCELLED
```

## 16.4 `fills`

```text
fill_id
order_id
trade_date
symbol
side
quantity
market_price
fill_price
gross_amount
slippage_amount
commission
net_cash_effect
created_at
```

## 16.5 `cash_ledger`

```text
entry_id
trade_date
entry_type
related_order_id
related_fill_id
amount
cash_before
cash_after
description
created_at
```

现金流水类型：

```text
INITIAL_CAPITAL
BUY
SELL
COMMISSION
DIVIDEND
ADJUSTMENT
```

## 16.6 `positions`

```text
trade_date
symbol
quantity
available_quantity
average_cost
close_price
market_value
unrealized_pnl
realized_pnl
actual_weight
target_weight
created_at
```

## 16.7 `daily_nav`

```text
trade_date
cash
market_value
total_equity
daily_pnl
daily_return
cumulative_return
turnover
commission
slippage
created_at
```

## 16.8 `anomalies`

```text
anomaly_id
run_id
trade_date
severity
anomaly_type
symbol
message
status
created_at
resolved_at
```

严重程度：

```text
INFO
WARN
ERROR
FATAL
```

---

# 17. 幂等性设计

## 17.1 基本要求

同一天重复执行：

```bash
python -m alphalab.paper execute --date 2026-08-25
```

不得产生重复成交。

## 17.2 唯一键

订单建议设置唯一约束：

```text
strategy_id
+ signal_date
+ execution_date
+ symbol
+ side
+ order_generation_version
```

## 17.3 重跑规则

- 已成功执行的日期，默认拒绝再次执行；
- 使用 `--force` 时，不得直接覆盖原账本；
- 强制重跑应创建新的 `run_id`；
- 历史账本不得被静默修改；
- 如需冲正，必须新增 `ADJUSTMENT` 流水；
- 不允许直接删除成交记录以修复账户。

---

# 18. 数据安全与未来函数防护

## 18.1 数据截断

策略调用前必须执行：

```python
market_data = market_data[
    market_data["trade_date"] <= signal_date
]
```

## 18.2 强制检查

如果策略访问的数据中存在：

```text
trade_date > signal_date
```

运行立即失败。

## 18.3 ETF池历史一致性

候选池原则上应使用当时可获得的信息：

- 当时是否上市；
- 当时是否停牌或退市；
- 当时成交额；
- 当时流动性；
- 当时规模。

若历史规模数据暂时不可获得：

- 不得使用今天的规模直接回填全部历史；
- 必须在报告中标记这一数据限制；
- 可先使用上市时间和历史成交额作为主要过滤；
- 待取得历史规模数据后重新验证。

## 18.4 数据快照

每次运行记录：

```text
data_snapshot_id
latest_market_date
source
row_count
checksum
```

确保历史结果可复现。

---

# 19. 对账规则

## 19.1 资产恒等式

每日必须满足：

```text
总资产 = 现金 + Σ（持仓数量 × 收盘价）
```

## 19.2 现金恒等式

每日必须满足：

```text
期末现金
=
期初现金
+ 卖出净收入
- 买入支出
- 手续费
+ 其他现金流
```

## 19.3 持仓恒等式

每个标的必须满足：

```text
期末数量
=
期初数量
+ 买入成交数量
- 卖出成交数量
```

## 19.4 强制检查项

每日运行结束必须检查：

- 现金是否为负；
- 持仓是否为负；
- 卖出是否超过可卖数量；
- 订单是否重复；
- 成交是否重复；
- 订单和成交是否能关联；
- 现金流水和成交是否一致；
- 净值是否连续；
- 总资产是否守恒；
- 是否存在未来日期数据；
- 是否存在无法解释的手工调整。

超过容差时：

```text
运行状态 = FAILED
异常级别 = FATAL
```

---

# 20. 每日报告

每日生成：

```text
reports/paper/YYYY-MM-DD.md
```

## 20.1 账户摘要

```text
交易日期：
策略版本：
配置哈希：
总资产：
现金：
持仓市值：
当日收益：
累计收益：
当日换手：
手续费：
滑点：
```

## 20.2 今日成交

| 标的 | 方向 | 数量 | 市场价 | 模拟成交价 | 滑点 | 手续费 | 状态 |
|---|---|---:|---:|---:|---:|---:|---|

## 20.3 当前持仓

| 标的 | 数量 | 成本价 | 收盘价 | 市值 | 浮动盈亏 | 实际权重 | 目标权重 |
|---|---:|---:|---:|---:|---:|---:|---:|

## 20.4 下一交易日计划

| 标的 | 动作 | 计划数量 | 目标权重 | 评分 | 排名 | 原因 |
|---|---|---:|---:|---:|---:|---|

## 20.5 收益归因

至少拆分：

```text
市场整体收益
持仓选择收益
调仓收益
现金仓位影响
交易手续费
模拟滑点
```

## 20.6 异常检查

示例：

```text
[PASS] 账户总资产恒等式成立
[PASS] 现金未出现负值
[PASS] 无重复订单
[PASS] 无超额卖出
[WARN] 513100.SH 缺少开盘价，订单已拒绝
[PASS] 策略数据未超过信号日期
```

---

# 21. CLI命令设计

## 21.1 生成计划

```bash
python -m alphalab.paper prepare \
  --date 2026-08-24 \
  --strategy etf_rotation_v0 \
  --config config/etf_rotation_v0.yaml
```

## 21.2 执行模拟成交

```bash
python -m alphalab.paper execute \
  --date 2026-08-25
```

## 21.3 对账

```bash
python -m alphalab.paper reconcile \
  --date 2026-08-25
```

## 21.4 生成报告

```bash
python -m alphalab.paper report \
  --date 2026-08-25
```

## 21.5 历史回放

```bash
python -m alphalab.paper replay \
  --start 2026-07-01 \
  --end 2026-07-31 \
  --reset-account
```

## 21.6 回测与回放一致性比较

```bash
python -m alphalab.paper compare \
  --backtest-run-id <run_id> \
  --replay-run-id <run_id>
```

## 21.7 查看账户状态

```bash
python -m alphalab.paper status \
  --date 2026-08-25
```

---

# 22. 历史回放设计

## 22.1 单日回放

目的：

- 校验候选池；
- 校验因子；
- 校验排名；
- 校验目标权重；
- 校验订单数量；
- 校验下一日成交；
- 校验现金和持仓。

## 22.2 连续5日回放

目的：

- 校验持仓跨日继承；
- 校验先卖后买；
- 校验卖出资金释放；
- 校验重复运行；
- 校验净值连续性。

## 22.3 连续20日回放

目的：

- 比较回测和模拟回放；
- 检查逐日净值差异；
- 检查逐笔成交差异；
- 检查持仓差异；
- 检查交易成本差异。

## 22.4 全样本回放

在前述验证通过后，运行完整历史区间。

要求输出：

- 年度收益；
- 最大回撤；
- Calmar；
- 月度收益；
- 换手；
- 手续费；
- 滑点；
- ETF贡献；
- 不同市场阶段表现；
- 参数邻域稳定性；
- 与B0、B1基线对比。

---

# 23. 回测与模拟一致性标准

在相同条件下：

- 相同信号日期；
- 相同成交日期；
- 相同开盘价格；
- 相同手续费；
- 相同滑点；
- 相同整手规则；
- 相同先卖后买逻辑；
- 相同资金约束。

应满足：

```text
每日现金差异 <= 0.01元
每日持仓数量完全一致
每日成交数量完全一致
每日净值差异 <= 0.01元
最终累计收益一致
```

如不一致，系统必须输出逐日差异：

| 日期 | 类型 | 标的 | 回测值 | 回放值 | 差异 | 可能原因 |
|---|---|---|---:|---:|---:|---|

在一致性通过前，不进入实时模拟阶段。

---

# 24. 测试要求

## 24.1 单元测试

必须覆盖：

- 向下取整至100股；
- 买卖滑点计算；
- 手续费计算；
- 目标权重转订单；
- 资金不足；
- 无行情；
- 空仓；
- 全部卖出；
- 同日重复运行；
- 权重和超过100%；
- 数据日期超过信号日；
- 持仓数量小于0；
- 现金小于0。

## 24.2 集成测试

至少覆盖：

- `prepare → execute → reconcile → report`；
- 卖出后买入；
- 多ETF同时调仓；
- 无成交日；
- 某个ETF缺失行情；
- 程序执行中断后恢复；
- 重复执行保护；
- 账户从初始资金连续运行5日。

## 24.3 黄金样本测试

选择一个固定的历史日期区间，保存：

- 输入数据快照；
- 预期信号；
- 预期订单；
- 预期成交；
- 预期持仓；
- 预期现金；
- 预期净值。

任何代码修改后自动运行黄金样本，防止策略行为被无意改变。

---

# 25. 迭代计划

## M0：策略与规则冻结

### 目标

形成唯一可执行的 ETF 轮动 V0 定义。

### 交付物

- `etf_rotation_v0.yaml`；
- ETF候选池规则；
- 因子公式；
- 排名规则；
- 调仓时间；
- 目标持仓规则；
- 成交规则；
- 手续费和滑点；
- 样本内外划分；
- B0、B1、S0至S3实验定义。

### 验收标准

- 所有参数均显式配置；
- 不存在隐藏参数；
- 策略版本固定为 `0.1.0`；
- 样本外结果产生前不修改策略规则。

---

## M1：抽取统一策略核心

### 目标

将现有回测中的策略逻辑抽取为纯函数。

### 交付物

- `strategies/etf_rotation_v0.py`；
- `generate_target_portfolio()`；
- 信号原因输出；
- 策略单元测试；
- 固定输入下的预期输出样本。

### 验收标准

- 无数据库依赖；
- 无QMT依赖；
- 无当前时间依赖；
- 无未来数据；
- 相同输入返回相同输出；
- 回测可调用统一策略核心。

---

## M2：账户和SQLite账本

### 目标

建立模拟账户的唯一事实来源。

### 交付物

- SQLite数据库；
- `runs`；
- `signals`；
- `orders`；
- `fills`；
- `cash_ledger`；
- `positions`；
- `daily_nav`；
- `anomalies`；
- 初始化账户命令。

### 验收标准

- 可以初始化10万元模拟账户；
- 所有现金变化有流水；
- 所有持仓变化有成交；
- 支持查询任意日期账户状态；
- 重复初始化被拒绝。

---

## M3：实现每日模拟闭环

### 目标

打通 `prepare → execute → reconcile → report`。

### 交付物

- `prepare`命令；
- `execute`命令；
- `reconcile`命令；
- `report`命令；
- PaperBrokerAdapter；
- Markdown日报。

### 验收标准

- 可指定历史日期生成计划；
- 可按下一日开盘模拟成交；
- 可更新现金和持仓；
- 可生成每日净值；
- 可生成日报；
- 同一天重复执行不会重复成交。

---

## M4：历史回放与一致性验证

### 目标

证明回测、历史回放和模拟执行逻辑一致。

### 交付物

- `replay`命令；
- `compare`命令；
- 单日回放结果；
- 连续5日回放结果；
- 连续20日回放结果；
- 差异报告。

### 验收标准

在相同成交假设下：

- 持仓数量逐日一致；
- 成交数量逐笔一致；
- 每日现金差异不超过0.01元；
- 每日净值差异不超过0.01元；
- 无未来数据；
- 无无法解释的差异。

---

## M5：策略基线与样本外验证

### 目标

判断策略是否真正优于简单基线。

### 交付物

- B0结果；
- B1结果；
- S0至S3结果；
- 样本内报告；
- 样本外报告；
- 参数邻域分析；
- 成本翻倍压力测试；
- 延迟一天执行测试；
- ETF收益贡献分析。

### 验收标准

明确输出：

```text
保留哪些模块
删除哪些模块
哪些模块没有证据
下一轮只允许修改哪个变量
```

复杂策略若不能稳定战胜B1，应回退至更简单版本。

---

## M6：当前日期开始每日模拟

### 目标

验证真实每日运行的稳定性。

### 运行周期

至少20个交易日，推荐60个交易日。

### 每日流程

```text
更新数据
→ prepare
→ 人工检查计划
→ 次日execute
→ reconcile
→ report
→ 记录异常
```

### 验收标准

连续20个交易日：

- 无重复订单；
- 无账本错误；
- 无无法解释的持仓差异；
- 无负现金；
- 无超额卖出；
- 所有异常有记录；
- 每日计划均可执行；
- 回测和模拟假设未发生漂移。

---

## M7：QMT只读对接

### 目标

使用QMT读取真实账户数据，但不自动下单。

### 交付物

- `QMTBrokerAdapter.get_cash()`；
- `get_positions()`；
- `get_orders()`；
- `get_fills()`；
- QMT账户与本地账本对比报告。

### 验收标准

- 可读取QMT现金；
- 可读取QMT持仓；
- 可读取委托和成交；
- 不具备自动提交订单权限；
- 本地模拟账户不被QMT数据覆盖。

---

## M8：小资金实盘

### 前置条件

必须同时通过：

- 工程正确性闸门；
- Alpha闸门；
- 稳健性闸门；
- 模拟执行闸门。

### 初始资金

建议：

```text
总可投资资金的5%至10%
约10万至20万元
```

### 执行方式

第一阶段采用：

```text
系统生成订单计划
→ 人工审核
→ 人工在QMT执行
→ 系统读取真实成交
→ 自动对账
```

暂不自动提交订单。

### 验收标准

- 实盘和模拟成交差异可解释；
- 实际手续费和滑点可追踪；
- 实盘账户每日对账一致；
- 不因短期盈亏临时修改参数；
- 不因连续盈利立即扩大资金。

---

## M9：自动化与平台化

仅在M8运行稳定后考虑：

- 自动定时运行；
- QMT半自动下单；
- 风险闸门；
- 消息推送；
- Web Dashboard；
- 多策略管理；
- 组合级资金分配；
- Agent辅助异常分析。

---

# 26. 优先级清单

## P0：立即实现

- 冻结ETF轮动V0；
- 抽取统一策略函数；
- SQLite账本；
- `prepare`；
- `execute`；
- `reconcile`；
- `report`；
- 单日和5日历史回放；
- 回测与回放一致性；
- 幂等性；
- 配置哈希；
- 数据日期检查；
- 现金和资产守恒检查。

## P1：P0稳定后实现

- 20日历史回放；
- B0至S3实验；
- 样本外验证；
- 成本压力测试；
- 参数稳定性；
- 每日模拟运行；
- 收益归因；
- QMT只读适配器。

## P2：小资金实盘后实现

- 自动调度；
- 实盘订单导出；
- QMT半自动下单；
- Dashboard；
- 异常消息提醒；
- 多账户；
- 多策略组合；
- 自动研究和Agent能力。

---

# 27. 研发约束

Codex在实现时必须遵循：

1. 中文沟通和中文说明为主，必要专有名词保留英文；
2. 不重写现有AlphaLab无关模块；
3. 优先复用现有行情读取和回测能力；
4. 不新增Web前端；
5. 不直接接入QMT自动下单；
6. 每个阶段先写测试，再实现功能；
7. 每个里程碑独立提交；
8. 数据库迁移必须可追踪；
9. 所有运行结果必须可复现；
10. 禁止使用系统当前时间替代显式交易日期；
11. 禁止静默修复数据；
12. 禁止跳过异常后继续标记成功；
13. 禁止直接修改历史账本；
14. 策略逻辑变更必须提升策略版本；
15. 回测和模拟必须共享策略核心；
16. 每个订单和成交必须有唯一ID；
17. 所有金额计算明确精度和舍入规则；
18. 任何失败必须留下运行记录和错误原因。

---

# 28. Definition of Done

本阶段只有在以下条件全部满足时才视为完成：

- ETF轮动策略配置已经冻结；
- 策略已抽取为纯函数；
- 回测已切换至统一策略核心；
- SQLite账本可正常运行；
- 支持生成下一交易日计划；
- 支持按下一日开盘价模拟成交；
- 支持更新现金、持仓和净值；
- 支持每日自动对账；
- 支持生成Markdown日报；
- 支持单日、5日和20日历史回放；
- 回测和回放逐日一致；
- 同一天重复运行不会重复成交；
- 策略版本、配置哈希和代码版本可追踪；
- 所有关键异常均有测试；
- 当前日期开始的每日模拟交易可以稳定运行。

---

# 29. 最终决策原则

每一轮策略迭代只能修改一个主要变量。

标准流程为：

```text
提出一个明确假设
→ 修改一个策略模块
→ 运行固定样本内实验
→ 运行封存样本外实验
→ 运行成本和参数扰动测试
→ 与B1简单基线比较
→ 决定保留、回退或淘汰
```

禁止采用以下方式：

```text
同时修改多个因子
+ 重新调整权重
+ 更换持仓数
+ 更换调仓频率
+ 更换止损参数
→ 只保留结果最好的组合
```

系统最终追求的不是找到历史收益最高的策略，而是找到：

> 规则简单、逻辑可解释、样本外有效、交易成本可承受、执行行为稳定，并能够长期持续迭代的策略。