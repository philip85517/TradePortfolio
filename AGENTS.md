# 用户确认的 Portfolio 入口

在本项目和本任务中，用户提到 portfolio 时，默认指股票组合研究向导：
http://127.0.0.1:8787/wizard?draft=bbb045486b4e4d9cbe353b95454355db

服务启动方式、持久化数据目录和暂不使用的错误匹配目录见 [入口约定](docs/parking/portfolio-entry.md)。先读取该约定，再启动或切换服务。

除非用户明确点名，不使用 ce1b 工作目录的 Portfolio Workspace（18774）、ETF Portfolio Lab 或 Quant Workbench 来替换这个入口。它们已按用户要求暂存为非默认匹配；保留原目录与数据，不自动整合或删除。
