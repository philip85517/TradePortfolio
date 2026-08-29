# ETF 轮动策略规格说明：ETFStrategy

> 目标：让 Codex 基于本文档自动实现一个可回测、可扩展、可实盘迁移的 ETF 轮动策略框架。  
> 策略核心：先构建可交易 ETF 池，再基于中期动量、趋势质量、风险调整收益、流动性和过热惩罚进行排序；只在强势 ETF 回踩 EMA20 后重新转强时买入；通过 ATR 止损、风险预算仓位和轮动退出控制回撤。

---

## 1. 策略名称

```text
ETFStrategy
```

建议策略 ID：

```text
etf_rotation_ema20_v1
```

---

## 2. 策略目标

本策略不是日内交易策略，而是日线级别的中短期 ETF 轮动策略。

核心目标：

1. 在 A 股、美股、港股相关 ETF，以及黄金、石油、债券等 ETF 中，筛选强势且趋势质量较好的标的；
2. 避免杠杆 ETF、反向 ETF、主动管理 ETF、个股型 ETF、流动性差 ETF；
3. 使用趋势评分选出候选 ETF；
4. 使用 EMA20 回踩确认作为买入触发；
5. 使用 ATR 止损和单笔风险预算控制亏损；
6. 使用排名退化、趋势破坏和硬止损作为退出条件；
7. 支持后续迁移到 QMT 或其他实盘交易环境。

---

## 3. 策略设计原则

### 3.1 不预测，只跟随

本策略不预测宏观、不预测市场方向，只做趋势跟随和相对强弱轮动。

### 3.2 先选标的，再等买点

不要在全市场随机寻找 K 线形态。  
先通过 ETF 综合评分选出候选池，再只在候选池中等待 EMA20 回踩买点。

### 3.3 仓位由风险决定，而不是固定金额决定

每只 ETF 单笔买入金额不是固定 10 万，而是由止损距离和单笔最大亏损决定：

```text
实际买入金额 = min(单标的最大买入金额, 单笔最大亏损金额 / 止损距离百分比)
```

### 3.4 避免重复配置同一风险因子

ETF 之间可能高度相关，例如：

- 沪深300 ETF、A50 ETF、上证50 ETF；
- 纳指100 ETF、标普500 ETF、科技 ETF；
- 恒生科技 ETF、港股互联网 ETF、中概互联网 ETF。

因此需要做 ETF 分类和相关性去重。

### 3.5 所有信号必须避免未来函数

任何指标、排名、信号，只能使用交易日收盘时点已经可见的数据。  
如果在 T 日收盘生成信号，则最早只能在 T+1 日开盘或指定价格成交。

---

## 4. 适用市场与标的范围

支持以下 ETF：

1. A 股上市 ETF；
2. 港股相关 ETF；
3. 美股相关 ETF；
4. 黄金 ETF；
5. 原油 / 石油 ETF；
6. 债券 ETF；
7. 红利、低波、价值、成长等被动策略指数 ETF；
8. 行业 ETF。

默认剔除以下 ETF：

1. 杠杆 ETF；
2. 反向 ETF；
3. 主动管理 ETF；
4. 个股型 ETF；
5. 主题过窄、持仓过度集中 ETF；
6. 上市时间过短 ETF；
7. 流动性不足 ETF；
8. 数据质量异常 ETF。

---

## 5. 推荐项目目录结构

Codex 应按照如下目录结构实现：

```text
etf_strategy/
  README.md
  ETFStrategy.md
  requirements.txt
  config/
    strategy_config.yaml
    etf_universe.yaml
  data/
    raw/
    processed/
    cache/
  src/
    __init__.py
    data_loader.py
    universe.py
    indicators.py
    scoring.py
    signals.py
    position_sizing.py
    portfolio.py
    backtester.py
    risk.py
    report.py
    utils.py
  tests/
    test_indicators.py
    test_scoring.py
    test_signals.py
    test_position_sizing.py
    test_backtester.py
  outputs/
    trades/
    reports/
    charts/
  run_backtest.py
  run_daily_plan.py
```

---

## 6. 配置文件设计

Codex 需要实现 `config/strategy_config.yaml`。

推荐配置如下：

```yaml
strategy:
  strategy_id: "etf_rotation_ema20_v1"
  benchmark: "000300.SH"
  initial_cash: 1000000
  start_date: "2018-01-01"
  end_date: null
  rebalance_frequency: "daily"

universe_filter:
  min_listing_days: 180
  min_avg_amount_20d: 30000000
  min_fund_size: 500000000
  exclude_leverage: true
  exclude_inverse: true
  exclude_active: true
  exclude_single_stock: true
  max_premium_abs: 0.03

scoring:
  lookback_short: 20
  lookback_mid: 60
  lookback_long: 120
  atr_window: 20
  ema_window: 20
  ma_mid_window: 60
  effective_move_window: 20
  gap_stability_window: 20
  close_position_window: 10

  weights:
    return_20d: 0.15
    return_60d: 0.25
    return_120d: 0.15
    return_atr_20d: 0.15
    effective_move_20d: 0.15
    ma20_gap_stability: 0.05
    close_position_quality: 0.05
    liquidity: 0.05

  overheat:
    ma20_gap_penalty_threshold: 0.12
    ma60_gap_penalty_threshold: 0.25
    rsi_penalty_threshold: 75
    penalty_score: 20

candidate:
  top_n_watchlist: 10
  top_n_holdings: 5
  max_per_asset_class: 2
  correlation_lookback: 60
  correlation_threshold: 0.8

entry:
  require_close_above_ma60: true
  require_ma20_above_ma60: true
  ema_pullback_atr_multiple: 0.5
  max_consecutive_close_below_ema20: 2
  close_position_threshold: 0.75
  require_bullish_candle: true

exit:
  atr_stop_multiple: 1.5
  consecutive_close_below_ema20_exit: 2
  rank_exit_threshold: 30
  enable_rotation_exit: true
  enable_trailing_stop: true
  profit_to_break_even_r: 2
  trailing_stop_atr_multiple: 2.0

position:
  max_position_value_per_etf: 100000
  max_loss_per_trade: 5000
  max_total_position_pct: 0.8
  max_single_asset_class_pct: 0.4
  min_trade_value: 2000

execution:
  signal_price: "close"
  execution_price: "next_open"
  commission_rate: 0.0003
  slippage_rate: 0.0005
  min_lot_size: 100
```

---

## 7. 数据字段要求

每个 ETF 至少需要以下字段：

```text
date
symbol
name
open
high
low
close
volume
amount
fund_size
premium_discount_rate
asset_class
is_leverage
is_inverse
is_active
is_single_stock
listing_date
```

字段说明：

| 字段 | 含义 |
|---|---|
| date | 交易日期 |
| symbol | ETF 代码 |
| name | ETF 名称 |
| open | 开盘价 |
| high | 最高价 |
| low | 最低价 |
| close | 收盘价 |
| volume | 成交量 |
| amount | 成交额 |
| fund_size | 基金规模 |
| premium_discount_rate | 溢价率 / 折价率 |
| asset_class | ETF 分类 |
| listing_date | 上市日期 |

`asset_class` 建议使用以下枚举：

```text
A_SHARE_BROAD
A_SHARE_INDUSTRY
HK_BROAD
HK_TECH
US_BROAD
US_TECH
COMMODITY_GOLD
COMMODITY_OIL
COMMODITY_METAL
BOND
DIVIDEND
LOW_VOL
OTHER
```

---

## 8. ETF 池过滤逻辑

### 8.1 基础过滤

Codex 需要实现函数：

```python
filter_universe(etf_meta, market_data, config) -> pd.DataFrame
```

过滤条件：

```text
上市交易天数 >= 180
过去20日平均成交额 >= 3000万
基金规模 >= 5亿
不是杠杆ETF
不是反向ETF
不是主动管理ETF
不是个股型ETF
溢价率绝对值 <= 3%
价格数据无严重缺失
```

### 8.2 数据完整性要求

如果某 ETF 在过去 120 个交易日中缺失超过 10% 的交易日，则剔除。

```text
missing_ratio_120d <= 10%
```

### 8.3 相关性去重

Codex 需要实现：

```python
deduplicate_by_correlation(scored_etfs, returns_matrix, threshold=0.8) -> pd.DataFrame
```

逻辑：

1. 按综合得分从高到低排序；
2. 依次加入候选池；
3. 如果新 ETF 与已选 ETF 在过去 60 日收益相关性大于 0.8，则跳过；
4. 如果新 ETF 与已选 ETF 属于同一主题方向，则跳过，保留组内得分更高者；
5. 每个资产类别最多保留 `max_per_asset_class` 只；
6. 最终得到候选观察池。

---

## 9. 指标计算

Codex 需要实现 `src/indicators.py`。

### 9.1 收益率

```text
return_20d = close / close.shift(20) - 1
return_60d = close / close.shift(60) - 1
return_120d = close / close.shift(120) - 1
```

### 9.2 EMA20

```text
ema20 = close.ewm(span=20, adjust=False).mean()
```

### 9.3 MA60

```text
ma60 = close.rolling(60).mean()
```

### 9.4 ATR20

True Range：

```text
tr = max(
  high - low,
  abs(high - previous_close),
  abs(low - previous_close)
)
```

ATR：

```text
atr20 = rolling_mean(tr, 20)
atr20_pct = atr20 / close
```

### 9.5 风险调整收益

```text
return_atr_20d = return_20d / atr20_pct
```

注意：

```text
如果 atr20_pct <= 0 或为空，则该指标为空。
```

### 9.6 Effective Move

用于衡量价格移动是否有效。

```text
effective_move_20d = abs(close - close.shift(20)) / sum(abs(close.diff()), 20)
```

解释：

- 越接近 1，说明价格单向移动更强；
- 越接近 0，说明来回震荡多。

### 9.7 MA20 GAP 稳定性

```text
ma20_gap = close / ema20 - 1
ma20_gap_stability = -rolling_std(ma20_gap, 20)
```

标准差越低越稳定，因此取负数，使得越大越好。

### 9.8 收盘位置质量

```text
daily_close_position = (close - low) / (high - low)
close_position_quality = rolling_mean(daily_close_position, 10)
```

如果 `high == low`，则当日 `daily_close_position = 0.5`。

### 9.9 RSI14

用于过热惩罚，不作为主要趋势指标。

```text
rsi14 = standard_rsi(close, window=14)
```

---

## 10. 综合评分模型

Codex 需要实现 `src/scoring.py`。

函数：

```python
calculate_score(indicator_df, config) -> pd.DataFrame
```

### 10.1 横截面排名

每天对所有可交易 ETF 做横截面排名。

建议使用百分位排名：

```python
rank_pct = series.rank(pct=True)
```

分数范围：

```text
0 ~ 100
```

转换：

```python
score = rank_pct * 100
```

### 10.2 综合分数

```text
total_score =
0.15 × rank(return_20d)
+ 0.25 × rank(return_60d)
+ 0.15 × rank(return_120d)
+ 0.15 × rank(return_atr_20d)
+ 0.15 × rank(effective_move_20d)
+ 0.05 × rank(ma20_gap_stability)
+ 0.05 × rank(close_position_quality)
+ 0.05 × rank(liquidity)
- overheat_penalty
```

### 10.3 流动性分数

```text
liquidity = log(avg_amount_20d)
```

然后做横截面排名。

### 10.4 过热惩罚

满足任意条件则扣分：

```text
close / ema20 - 1 > 12%
close / ma60 - 1 > 25%
rsi14 > 75
```

每触发一个条件，扣 `penalty_score`。

默认：

```text
penalty_score = 20
```

### 10.5 无效数据处理

如果某 ETF 某天关键指标为空，则当日不可进入候选池。

关键指标包括：

```text
return_20d
return_60d
return_120d
atr20_pct
effective_move_20d
ema20
ma60
avg_amount_20d
```

---

## 11. 候选池生成

Codex 需要实现：

```python
build_watchlist(scored_df, date, config) -> pd.DataFrame
```

流程：

1. 取当日所有 ETF 的评分；
2. 剔除无效分数 ETF；
3. 按 `total_score` 从高到低排序；
4. 做资产类别数量约束；
5. 做相关性去重；
6. 输出 Top10 观察池。

默认：

```text
观察池数量 = 10
实际持仓最多 = 5
单一资产类别最多 = 2
相关性阈值 = 0.8
```

---

## 12. 入场信号

Codex 需要实现 `src/signals.py`。

函数：

```python
generate_entry_signal(symbol, history_df, config) -> bool
```

### 12.1 入场前置条件

ETF 必须在当日观察池内。

### 12.2 趋势条件

满足：

```text
close > ma60
ema20 > ma60
```

如果配置中关闭 `require_close_above_ma60` 或 `require_ma20_above_ma60`，可跳过对应条件。

### 12.3 回踩条件

过去 5 个交易日内，至少有一天满足：

```text
low <= ema20 + 0.5 × atr20
```

含义：价格曾经回踩到 EMA20 附近。

### 12.4 不能明显跌破趋势

过去 5 个交易日内，不能出现连续 3 日收盘低于 EMA20。

默认规则：

```text
连续收盘低于 EMA20 的天数 <= 2
```

### 12.5 重新转强确认

当日满足：

```text
close > ema20
close > open
(close - low) / (high - low) >= 0.75
```

如果 `high == low`，则该条件视为不通过。

### 12.6 入场信号生成

全部满足时，生成买入信号：

```text
entry_signal = true
```

信号发生在 T 日收盘后。  
成交发生在 T+1 日开盘价，或配置中的 `execution_price` 指定价格。

---

## 13. 初始止损

Codex 需要实现：

```python
calculate_initial_stop(entry_price, atr20, ema20, recent_low, config) -> float
```

推荐初版使用简单规则：

```text
stop_price = entry_price - 1.5 × atr20
```

可选增强规则：

```text
stop_price = min(
  entry_price - 1.5 × atr20,
  ema20 - 0.5 × atr20,
  recent_low
)
```

初版建议使用：

```text
entry_price - 1.5 × atr20
```

因为更容易回测，也更清晰。

---

## 14. 仓位计算

Codex 需要实现 `src/position_sizing.py`。

函数：

```python
calculate_position_size(entry_price, stop_price, cash, config) -> dict
```

### 14.1 单笔风险预算

默认：

```text
单笔最大亏损 = 5000
```

### 14.2 止损距离

```text
stop_loss_pct = (entry_price - stop_price) / entry_price
```

如果 `stop_loss_pct <= 0`，不允许买入。

### 14.3 理论买入金额

```text
theoretical_position_value = max_loss_per_trade / stop_loss_pct
```

### 14.4 实际买入金额

```text
position_value = min(
  theoretical_position_value,
  max_position_value_per_etf,
  available_cash
)
```

默认：

```text
单 ETF 最大买入金额 = 100000
```

### 14.5 最小交易金额

如果：

```text
position_value < min_trade_value
```

则不交易。

默认：

```text
min_trade_value = 2000
```

### 14.6 手数约束

A 股 ETF 默认最小交易单位为 100 份。

```text
shares = floor(position_value / entry_price / 100) * 100
```

如果 `shares <= 0`，不交易。

---

## 15. 退出规则

Codex 需要实现：

```python
generate_exit_signal(position, history_df, scored_df, config) -> dict
```

退出优先级如下：

### 15.1 硬止损退出

如果当日最低价触及止损：

```text
low <= stop_price
```

则按照止损价或下一根开盘价模拟成交。

保守回测建议：

```text
exit_price = min(stop_price, next_open)
```

如果没有 next_open，则使用 stop_price。

### 15.2 趋势破坏退出

如果连续 2 日收盘低于 EMA20：

```text
close < ema20 连续 2 日
```

则 T 日收盘生成卖出信号，T+1 日开盘卖出。

### 15.3 排名退化退出

如果持仓 ETF 当日综合排名跌出 Top30，则生成退出信号。

默认：

```text
rank_exit_threshold = 30
```

### 15.4 轮动替换退出

如果：

```text
当前持仓排名跌出 Top30
且观察池中出现更高分 ETF
且组合持仓数量已达到上限
```

则卖出弱势 ETF，保留现金或买入新的候选 ETF。

### 15.5 保本止损

如果单笔交易浮盈达到 2R：

```text
unrealized_profit >= 2 × initial_risk_amount
```

则止损上移到成本价：

```text
stop_price = max(stop_price, entry_price)
```

### 15.6 ATR 跟踪止盈

如果启用跟踪止损：

```text
trailing_stop = close - 2 × atr20
stop_price = max(current_stop_price, trailing_stop)
```

止损只能上移，不能下移。

---

## 16. 调仓逻辑

### 16.1 每日流程

每个交易日收盘后执行：

```text
1. 更新行情数据
2. 过滤 ETF 池
3. 计算指标
4. 计算综合分数
5. 生成观察池
6. 检查已有持仓退出信号
7. 检查观察池入场信号
8. 生成下一交易日交易计划
9. 输出日报
```

### 16.2 交易优先级

优先处理卖出，再处理买入：

```text
先退出弱势或触发止损的持仓
再用可用现金买入新信号
```

### 16.3 买入排序

如果多个 ETF 同时触发买入：

```text
按 total_score 从高到低买入
```

直到满足任一条件：

```text
持仓数量达到上限
现金不足
组合仓位达到上限
单一资产类别仓位达到上限
```

---

## 17. 组合约束

默认组合约束：

```text
最多持有 5 只 ETF
单只 ETF 最大买入 100000
组合最大仓位 80%
单一资产类别最大仓位 40%
单笔最大亏损 5000
```

如果出现多个同类 ETF，需按资产类别限制：

```text
同一主题方向最多持有 1 只
同一 asset_class 最多持有 2 只
```

如果相关性过高，保留得分更高者。

---

## 18. 回测要求

Codex 需要实现 `src/backtester.py`。

### 18.1 回测撮合规则

默认：

```text
T 日收盘生成信号
T+1 日开盘成交
```

### 18.2 成交成本

默认：

```text
commission_rate = 0.0003
slippage_rate = 0.0005
```

买入成本：

```text
buy_price = next_open × (1 + slippage_rate)
```

卖出价格：

```text
sell_price = next_open × (1 - slippage_rate)
```

### 18.3 不允许未来函数

以下数据不能在 T 日信号中使用：

```text
T+1 日开盘价
T+1 日最高价
T+1 日最低价
T+1 日收盘价
未来排名
未来基金规模
未来成交额
```

### 18.4 回测输出

需要输出：

```text
每日净值
每日持仓
每日观察池
每日信号
交易明细
策略绩效指标
年度绩效
月度绩效
最大回撤区间
```

---

## 19. 绩效指标

Codex 需要实现 `src/report.py`。

至少输出：

```text
累计收益率
年化收益率
年化波动率
夏普比率
最大回撤
Calmar Ratio
胜率
盈亏比
平均持仓天数
交易次数
年换手率
最大单笔亏损
最大单笔盈利
收益回撤比
相对基准超额收益
信息比率
```

### 19.1 分年度统计

输出每年：

```text
年度收益
年度最大回撤
年度交易次数
年度胜率
年度换手率
```

### 19.2 分资产类别统计

输出每个资产类别：

```text
交易次数
胜率
累计收益
平均收益
最大亏损
平均持仓天数
```

---

## 20. 日度交易计划输出

Codex 需要实现 `run_daily_plan.py`。

每日输出 Markdown 文件：

```text
outputs/reports/daily_plan_YYYYMMDD.md
```

内容包括：

```text
日期
当前持仓
今日观察池 Top10
今日买入信号
今日卖出信号
明日计划买入
明日计划卖出
每笔计划买入金额
每笔止损价
每笔最大亏损
组合当前仓位
组合风险暴露
```

示例：

```markdown
# ETFStrategy 日度交易计划：2026-06-21

## 当前持仓

| ETF | 名称 | 持仓金额 | 成本价 | 最新价 | 止损价 | 浮盈亏 | 当前排名 |
|---|---|---:|---:|---:|---:|---:|---:|

## 今日观察池 Top10

| 排名 | ETF | 名称 | 资产类别 | 综合分 | 20日收益 | 60日收益 | Effective Move | ATR% |
|---:|---|---|---|---:|---:|---:|---:|---:|

## 明日买入计划

| ETF | 名称 | 买入价格假设 | 止损价 | 买入金额 | 最大亏损 | 入场理由 |
|---|---|---:|---:|---:|---:|---|

## 明日卖出计划

| ETF | 名称 | 卖出原因 | 卖出价格假设 |
|---|---|---|---:|
```

---

## 21. 命令行入口

Codex 需要实现两个入口。

### 21.1 回测入口

```bash
python run_backtest.py --config config/strategy_config.yaml
```

支持参数：

```bash
python run_backtest.py   --config config/strategy_config.yaml   --start-date 2018-01-01   --end-date 2026-06-21   --initial-cash 1000000
```

### 21.2 每日计划入口

```bash
python run_daily_plan.py --config config/strategy_config.yaml --date 2026-06-21
```

---

## 22. 测试要求

Codex 需要为以下模块写单元测试：

```text
test_indicators.py
test_scoring.py
test_signals.py
test_position_sizing.py
test_backtester.py
```

### 22.1 指标测试

测试：

```text
EMA20 是否正确
ATR20 是否正确
Effective Move 是否正确
收盘位置质量是否正确
```

### 22.2 打分测试

测试：

```text
横截面 rank 是否正确
过热惩罚是否正确
缺失指标是否剔除
```

### 22.3 信号测试

测试：

```text
回踩 EMA20 后重新站上是否触发买入
连续 3 日收盘低于 EMA20 不触发买入
收盘位置低于 0.75 不触发买入
```

### 22.4 仓位测试

测试：

```text
止损距离 5% 时，5000 风险预算对应 100000 买入金额
止损距离 8% 时，5000 风险预算对应 62500 买入金额
低于最小交易金额时不交易
交易份额按 100 份取整
```

### 22.5 回测测试

测试：

```text
T 日信号只能在 T+1 日成交
不能使用未来价格
止损触发逻辑正确
交易成本计算正确
```

---

## 23. Codex 实现任务清单

请 Codex 按以下顺序实现：

### 阶段一：基础框架

1. 创建项目目录；
2. 创建配置文件；
3. 实现数据读取模块；
4. 实现 ETF 池过滤模块；
5. 实现指标计算模块。

### 阶段二：策略信号

1. 实现综合评分；
2. 实现观察池生成；
3. 实现相关性去重；
4. 实现入场信号；
5. 实现退出信号；
6. 实现仓位计算。

### 阶段三：回测系统

1. 实现组合对象；
2. 实现撮合逻辑；
3. 实现每日调仓；
4. 实现交易记录；
5. 实现净值曲线；
6. 实现风险指标。

### 阶段四：报告系统

1. 输出回测报告；
2. 输出日度交易计划；
3. 输出持仓明细；
4. 输出观察池明细；
5. 输出交易明细 CSV。

### 阶段五：测试和验收

1. 编写单元测试；
2. 使用模拟数据跑通；
3. 使用真实 ETF 历史数据跑通；
4. 检查无未来函数；
5. 检查信号、成交、仓位、止损是否符合本文档。

---

## 24. 初版验收标准

初版实现完成后，至少满足：

```text
1. 能读取 ETF 日线数据
2. 能过滤 ETF 池
3. 能计算所有指标
4. 能生成每日综合评分
5. 能生成每日观察池
6. 能生成买入信号
7. 能生成卖出信号
8. 能根据风险预算计算仓位
9. 能完成多 ETF 组合回测
10. 能输出交易明细和绩效报告
11. 单元测试通过
12. 不存在明显未来函数
```

---

## 25. 后续可增强方向

初版完成后，再考虑以下增强：

1. 加入大类资产择时；
2. 加入市场风险开关；
3. 加入指数宽度指标；
4. 加入波动率状态识别；
5. 加入宏观利率、美元指数、商品价格等外部变量；
6. 加入 Walk-forward 参数验证；
7. 加入不同市场交易日历；
8. 加入 QMT 实盘交易接口；
9. 加入自动日报和复盘报告；
10. 加入因子有效性归因。

---

## 26. 重要风险提示

本策略仅用于研究和模拟交易，不保证收益。  
ETF 轮动策略可能在震荡市、快速反转、流动性异常、溢价率异常、跨市场节假日错位时出现明显回撤。  
实盘前必须进行：

```text
样本外回测
参数稳定性测试
交易成本敏感性测试
极端行情压力测试
小资金模拟盘验证
```

---

## 27. 推荐最小可行版本

如果 Codex 需要先实现 MVP，请优先实现下面这个版本：

```text
ETF 池过滤：
非杠杆、非主动、日均成交额 > 3000万、规模 > 5亿、上市 > 180天

排序：
60日收益
20日收益 / ATR20%
Effective Move 20日

观察池：
Top10

入场：
回踩 EMA20 附近
重新收盘站上 EMA20
阳线
收盘位置 >= 75%

仓位：
每笔最多亏 5000
单只 ETF 最多买 100000

退出：
跌破 1.5ATR 止损
连续2日收盘低于 EMA20
排名跌出 Top30
```

MVP 跑通后，再逐步加入：

```text
相关性去重
资产类别约束
过热惩罚
跟踪止损
保本止损
分市场交易日历
真实交易成本
```
