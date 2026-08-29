"""组合级风险/约束检查。"""

from __future__ import annotations

from .target_portfolio import TargetPosition


def check_weights_sum(targets: list[TargetPosition], tolerance: float = 1e-9) -> None:
    total = sum(t.target_weight for t in targets)
    if total > 1.0 + tolerance:
        raise ValueError(f"目标权重之和超过 100%: {total:.4%}")


def check_single_weight(targets: list[TargetPosition], max_weight: float) -> None:
    for t in targets:
        if t.target_weight > max_weight + 1e-9:
            raise ValueError(f"{t.symbol} 目标权重 {t.target_weight:.4%} 超过上限 {max_weight:.4%}")

