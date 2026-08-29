"""目标组合权重生成：等权 + 现金储备 + 单标的权重上限。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WeightedSymbol:
    symbol: str
    weight: float


def build_equal_weights(
    symbols: list[str],
    config: dict,
) -> list[WeightedSymbol]:
    """等权目标权重。

    规则（固定、可复现）：
    1. 先扣除 reserve_cash_pct 现金储备；
    2. 其余资金按 top_n 等分；
    3. 若等分超过 max_single_weight，则每只标的封顶到 max_single_weight，
       剩余部分留在现金（不重新分配给其他标的）。
    """
    portfolio_cfg = config.get("portfolio", {})
    reserve = float(portfolio_cfg.get("reserve_cash_pct", 0.05))
    max_single = float(portfolio_cfg.get("max_single_weight", 0.20))
    n = len(symbols)
    if n == 0:
        return []
    equal = (1.0 - reserve) / n
    weights = [min(equal, max_single) for _ in symbols]
    return [WeightedSymbol(sym, w) for sym, w in zip(symbols, weights)]

