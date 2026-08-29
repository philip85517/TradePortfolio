"""统一策略核心的内存回测引擎（与模拟共用成交规则）。"""

from .engine import run_backtest

__all__ = ["run_backtest"]

