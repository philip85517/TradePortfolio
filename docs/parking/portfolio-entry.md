# Portfolio 入口约定（用户于 2026-09-10 明确）

本任务后续提到 portfolio，默认且唯一匹配为股票组合研究向导：
http://127.0.0.1:8787/wizard?draft=bbb045486b4e4d9cbe353b95454355db

代码目录：/Users/zhoulin/.codex/worktrees/0ec3/TradePortfolio
分支：codex/data-repair-design
草稿与缓存：/Users/zhoulin/.codex/worktrees/1deb/TradePortfolio/alphalab/reports/workbench
研究产物：/Users/zhoulin/.codex/worktrees/1deb/TradePortfolio/alphalab/reports/research

启动：
```sh
/opt/miniconda3/bin/python -u -m alphalab research workbench --host 127.0.0.1 --port 8787 --workspace-dir /Users/zhoulin/.codex/worktrees/1deb/TradePortfolio/alphalab/reports/workbench --runs-dir /Users/zhoulin/.codex/worktrees/1deb/TradePortfolio/alphalab/reports/research
```

## 暂存、排除默认匹配的入口

以下目录和数据保留原处，暂不使用；除非用户明确点名，不再因 portfolio 关键词启动、打开或整合：

- /Users/zhoulin/.codex/worktrees/ce1b/TradePortfolio：codex/portfolio-workspace-validation；alphalab/portfolio_workspace；18774 独立 Portfolio Workspace（Small/Large Portfolio）。
- 当前仓库 etf_strategy/run_dashboard.py 与 etf_strategy/web/static：ETF Portfolio Lab / Quant Workbench，非用户指定入口。
- /tmp/tradeportfolio-full-workbench.py：误匹配时生成的临时启动脚本，已停用。

独立历史截面审阅页只作为向导结果详情，不替代用户要求的向导首页。不要以其他分支中的 Portfolio UI 替换本服务。
