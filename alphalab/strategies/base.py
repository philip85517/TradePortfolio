"""策略核心公共数据类与异常。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Sequence


class StrategyError(Exception):
    """策略执行错误。"""


class FutureDataError(StrategyError):
    """检测到未来数据（trade_date > signal_date）。"""


class WeightConstraintError(StrategyError):
    """目标权重约束被违反（如权重和超过 100%）。"""


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
    diagnostics: dict[str, Any] = field(default_factory=dict)


def current_positions_to_dict(rows: Sequence[dict]) -> dict[str, dict]:
    """把持仓行序列（含 symbol/quantity/available_quantity/average_cost）转为字典。"""
    out: dict[str, dict] = {}
    for row in rows:
        available = row["available_quantity"] if "available_quantity" in row.keys() else row["quantity"]
        out[str(row["symbol"])] = {
            "quantity": int(row["quantity"]),
            "available_quantity": int(available),
            "average_cost": float(row["average_cost"] or 0.0) if "average_cost" in row.keys() else 0.0,
        }
    return out
