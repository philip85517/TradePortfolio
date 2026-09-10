# Delisting Replacement Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 退市决定公开后退出并按历史可见排名替补，输出可审阅的收益与交易记录。
**Architecture:** 新增独立事件及逐日执行模块，向导冻结事件和全量评分数据，engine 分派新策略，现有静态持仓路径不变。
**Tech Stack:** Python/pandas/DuckDB，原生 JS，pytest/node test。
**Spec:** docs/superpowers/specs/2026-09-11-delisting-replacement.md（会话已确认）

## Global Constraints
- 只使用 8787 向导，旧运行保持不变。
- 事件日期不能用摘牌日倒推，替补排名不能读取未来行情。
- 不改变原本金；补仓仅用卖出净回款，无成交不补仓。

### Task 1: 事件与交易记账
Files: new alphalab/research/delisting_events.py, replacement_policy.py; tests/integration/test_delisting_replacement.py.
- [x] 写日期证据/停牌顺延/真实价格/净回款与整手/无候选现金测试，运行看到失败。
- [x] 实现 load_events(path)、event_available(event, session)、evaluate_replacement(portfolio, data, sessions, spec, rank, raw_open)，事件来源冻结在 metadata。
- [x] rank(day, excluded) 仅消费 <= day 的数据并返回当时排名；raw_open(symbol, day) 只给实际未复权开盘价；缺证据抛出有股票日期的错误。
- [x] 测试重复事件、尾日卖出无替补、替补再次退市、未来数据篡改不影响早期成交。

### Task 2: 向导和引擎集成
Files: wizard_backend.py, engine.py, static/wizard.html, static/wizard.js, review.py.
- [x] 端到端测试从 portfolio policy 配置到 manifest 完整保存交易事件与收益。
- [x] 冻结完整研究截面供动态选股使用，按事件日历史身份筛选；不使用初始 FrozenRulePlugin 排名替补。
- [x] 新增可选处理策略，默认旧策略兼容已保存草稿；用户确认后用新配置验证原草稿。
- [x] 审阅显示退出与替补清单及费用，不伪造原始股数或已结算收益。

### Task 3: 真实数据验收
- [x] 核实并保存 002336 正式公告（巨潮 2025-039）；首个复牌日 2025-06-13 需真实交易状态/价格。
- [x] 运行回归、恢复 8787 服务，通过按钮启用新策略并运行，检查净值终点和 manifest 对账。
- [x] 记录实际成交、替补失败原因、数据限制，提交修复代码。
