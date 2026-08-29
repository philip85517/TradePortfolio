"""模拟交易撮合引擎（PaperBrokerAdapter）。

成交模型（SPEC 第 14 节）：
- T+1 开盘价成交；买入加滑点、卖出减滑点；
- 佣金 = max(成交额 × 佣金率, 最低佣金)；
- 不允许部分成交；无开盘价拒绝；资金不足拒绝；
- 先卖后买；卖出不得超过可卖数量。

核心撮合逻辑为纯函数 ``simulate_fills``，回测引擎与模拟交易共用，
从结构上保证回测与模拟一致（SPEC 5.3 唯一策略核心 + 相同成交规则）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from ..storage import PaperDatabase
from ..utils import money_round
from .base import BrokerAdapter


@dataclass(frozen=True)
class FillResult:
    order_key: str
    order_id: int
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
    cash_before: float
    cash_after: float


@dataclass(frozen=True)
class RejectedOrder:
    order_key: str
    symbol: str
    side: str
    reason: str


def compute_fill_price(market_price: float, side: str, slippage_bps: float) -> float:
    """买卖滑点：买入 = 市价×(1+滑点)，卖出 = 市价×(1-滑点)。"""
    slip = float(slippage_bps) / 10000.0
    if side == "BUY":
        return money_round(market_price * (1.0 + slip), 4)
    return money_round(market_price * (1.0 - slip), 4)


def compute_commission(gross_amount: float, commission_bps: float, min_commission_cny: float) -> float:
    return money_round(max(gross_amount * float(commission_bps) / 10000.0, float(min_commission_cny)))


def _fill(order, trade_date: str, market_price: float, cash: float, config: dict) -> FillResult:
    exec_cfg = config.get("execution", {})
    slippage_bps = float(exec_cfg.get("slippage_bps", 5))
    commission_bps = float(exec_cfg.get("commission_bps", 3))
    min_commission = float(exec_cfg.get("min_commission_cny", 0))
    fill_price = compute_fill_price(market_price, order["side"], slippage_bps)
    gross = money_round(int(order["planned_quantity"]) * fill_price)
    commission = compute_commission(gross, commission_bps, min_commission)
    net_cash_effect = -(gross + commission) if order["side"] == "BUY" else gross - commission
    return FillResult(
        order_key=order["order_key"],
        order_id=int(order.get("order_id", 0)),
        trade_date=trade_date,
        symbol=order["symbol"],
        side=order["side"],
        quantity=int(order["planned_quantity"]),
        market_price=money_round(market_price, 4),
        fill_price=fill_price,
        gross_amount=gross,
        slippage_amount=money_round(abs(fill_price - market_price) * int(order["planned_quantity"])),
        commission=commission,
        net_cash_effect=money_round(net_cash_effect),
        cash_before=money_round(cash),
        cash_after=money_round(cash + net_cash_effect),
    )


def _order_dict(order) -> dict:
    """兼容 sqlite3.Row 与 dataclass（PlannedOrder）两种订单表示。"""
    if hasattr(order, "keys"):
        return dict(order)
    return vars(order)


def simulate_fills(
    orders: list[Any],
    positions: dict[str, dict],
    cash: float,
    open_prices: dict[str, float],
    config: dict,
    trade_date: str | None = None,
) -> dict[str, Any]:
    """纯撮合：给定订单、持仓、现金与开盘价，返回成交/拒绝/新状态。

    这是回测与模拟交易的唯一成交规则实现（先卖后买、整手、滑点、佣金、
    资金约束、可卖数量约束）。
    """
    allow_negative = bool(config.get("risk", {}).get("allow_negative_cash", False))
    td = trade_date or ""
    fills: list[FillResult] = []
    rejected: list[RejectedOrder] = []
    orders = [_order_dict(o) for o in orders]
    state = {sym: dict(pos) for sym, pos in positions.items()}
    cash_now = float(cash)

    def update_position(sym: str, side: str, qty: int, fill_price: float, commission: float) -> None:
        pos = state.setdefault(
            sym,
            {
                "quantity": 0,
                "available_quantity": 0,
                "average_cost": 0.0,
                "realized_pnl": 0.0,
            },
        )
        if side == "BUY":
            old_qty = int(pos["quantity"])
            old_cost = float(pos["average_cost"]) * old_qty
            pos["quantity"] = old_qty + qty
            pos["available_quantity"] = int(pos["available_quantity"]) + qty
            pos["average_cost"] = money_round((old_cost + qty * fill_price + commission) / pos["quantity"], 4)
        else:
            realized = (fill_price - float(pos["average_cost"])) * qty - commission
            pos["quantity"] = int(pos["quantity"]) - qty
            pos["available_quantity"] = int(pos["available_quantity"]) - qty
            pos["realized_pnl"] = money_round(float(pos["realized_pnl"]) + realized)
            if pos["quantity"] == 0:
                pos["average_cost"] = 0.0

    # 第一轮：卖单
    for order in [o for o in orders if o["side"] == "SELL"]:
        sym = order["symbol"]
        if sym not in open_prices:
            rejected.append(RejectedOrder(order["order_key"], sym, "SELL", "NO_PRICE 无开盘价"))
            continue
        available = int(state.get(sym, {}).get("available_quantity", 0))
        if int(order["planned_quantity"]) > available:
            rejected.append(RejectedOrder(order["order_key"], sym, "SELL", "OVERSELL 超过可卖数量"))
            continue
        fill = _fill(order, td, open_prices[sym], cash_now, config)
        update_position(sym, "SELL", fill.quantity, fill.fill_price, fill.commission)
        cash_now = fill.cash_after
        fills.append(fill)

    # 第二轮：买单（目标权重高优先，其次 order_key，固定可复现）
    buy_orders = sorted(
        [o for o in orders if o["side"] == "BUY"],
        key=lambda o: (float(o.get("target_weight", 0.0)), o["order_key"]),
        reverse=True,
    )
    for order in buy_orders:
        sym = order["symbol"]
        if sym not in open_prices:
            rejected.append(RejectedOrder(order["order_key"], sym, "BUY", "NO_PRICE 无开盘价"))
            continue
        fill = _fill(order, td, open_prices[sym], cash_now, config)
        cost = fill.gross_amount + fill.commission
        if cash_now + 1e-9 < cost and not allow_negative:
            rejected.append(RejectedOrder(order["order_key"], sym, "BUY", "INSUFFICIENT_CASH 资金不足"))
            continue
        update_position(sym, "BUY", fill.quantity, fill.fill_price, fill.commission)
        cash_now = fill.cash_after
        fills.append(fill)

    return {
        "fills": fills,
        "rejected": rejected,
        "cash": money_round(cash_now),
        "positions": state,
    }


class PaperBrokerAdapter(BrokerAdapter):
    """基于 SQLite 账本的模拟券商适配器。"""

    def __init__(self, db: PaperDatabase, config: dict):
        self.db = db
        self.config = config

    # ---- BrokerAdapter 接口 ----
    def get_cash(self, trade_date):
        return self.db.get_cash(str(trade_date))

    def get_positions(self, trade_date):
        return self.db.get_positions(str(trade_date))

    def submit_orders(self, orders):
        return self.db.insert_orders("", orders)

    def get_orders(self, trade_date):
        return self.db.get_orders(execution_date=str(trade_date))

    def get_fills(self, trade_date):
        return self.db.get_fills(str(trade_date))

    # ---- 撮合并持久化 ----
    def execute_pending(
        self,
        execution_date: date | str,
        market_data: pd.DataFrame,
        run_id: str,
    ) -> dict[str, Any]:
        """执行 execution_date 的全部 PLANNED 订单：撮合 + 账本写入。"""
        d = str(execution_date)
        orders = self.db.get_orders(execution_date=d, status="PLANNED")
        if not orders:
            cash = self.db.get_cash(d)
            positions = {row["symbol"]: dict(row) for row in self.db.get_positions(d)}
            return {"fills": [], "rejected": [], "cash": cash, "positions": positions}

        md = market_data[pd.to_datetime(market_data["date"]) == pd.Timestamp(d)]
        open_prices: dict[str, float] = {}
        for sym in {o["symbol"] for o in orders}:
            part = md[md["symbol"] == sym]
            if not part.empty and pd.notna(part.iloc[0]["open"]):
                open_prices[sym] = float(part.iloc[0]["open"])

        cash = self.db.get_cash(d)
        positions: dict[str, dict] = {}
        for row in self.db.get_positions(d):
            positions[row["symbol"]] = {
                "quantity": int(row["quantity"]),
                "available_quantity": int(row["available_quantity"]),
                "average_cost": float(row["average_cost"]),
                "realized_pnl": float(row["realized_pnl"]),
            }

        result = simulate_fills(orders, positions, cash, open_prices, self.config, trade_date=d)

        # 持久化成交、现金流水、订单状态
        for fill in result["fills"]:
            fill_id = self.db.insert_fill(
                order_id=fill.order_id,
                order_key=fill.order_key,
                trade_date=fill.trade_date,
                symbol=fill.symbol,
                side=fill.side,
                quantity=fill.quantity,
                market_price=fill.market_price,
                fill_price=fill.fill_price,
                gross_amount=fill.gross_amount,
                slippage_amount=fill.slippage_amount,
                commission=fill.commission,
                net_cash_effect=fill.net_cash_effect,
            )
            entry_type = "SELL" if fill.side == "SELL" else "BUY"
            self.db.insert_cash_ledger(
                trade_date=d,
                entry_type=entry_type,
                amount=fill.net_cash_effect,
                cash_before=fill.cash_before,
                cash_after=fill.cash_after,
                description=f"{'卖出' if fill.side == 'SELL' else '买入'} {fill.symbol} {fill.quantity} 股 @ {fill.fill_price}",
                related_order_key=fill.order_key,
                related_fill_id=fill_id,
            )
            self.db.update_order_status(fill.order_key, "FILLED")

        for rej in result["rejected"]:
            self.db.update_order_status(rej.order_key, "REJECTED", rej.reason)
            severity = "FATAL" if rej.reason.startswith("OVERSELL") else "WARN"
            self.db.insert_anomaly(
                run_id=run_id,
                trade_date=d,
                severity=severity,
                anomaly_type=rej.reason.split(" ")[0],
                symbol=rej.symbol,
                message=f"{rej.symbol} {rej.side} 订单拒绝: {rej.reason}",
            )

        return result
