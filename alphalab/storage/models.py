"""账本行数据类（供查询结果使用）。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OrderRow:
    order_id: int
    order_key: str
    run_id: str
    strategy_id: str
    signal_date: str
    execution_date: str
    symbol: str
    side: str
    planned_quantity: int
    reference_price: float
    planned_value: float
    target_weight: float
    order_status: str
    reason: str


@dataclass(frozen=True)
class FillRow:
    fill_id: int
    order_id: int
    order_key: str
    trade_date: str
    symbol: str
    side: str
    quantity: int
    market_price: float
    fill_price: float
    gross_amount: float
    slippage_amount: float
    commission: float
    net_cash_effect: float


@dataclass(frozen=True)
class PositionRow:
    trade_date: str
    symbol: str
    quantity: int
    available_quantity: int
    average_cost: float
    close_price: float
    market_value: float
    unrealized_pnl: float
    realized_pnl: float
    actual_weight: float
    target_weight: float

