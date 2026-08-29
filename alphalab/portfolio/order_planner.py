"""目标组合 → 订单计划（SPEC 第 13 节）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from ..utils import floor_to_lot, money_round


@dataclass(frozen=True)
class PlannedOrder:
    order_key: str
    strategy_id: str
    signal_date: date
    execution_date: date
    symbol: str
    side: str  # BUY / SELL
    planned_quantity: int
    reference_price: float
    planned_value: float
    target_weight: float
    order_status: str = "PLANNED"
    reason: str = ""
    priority: int = 0


def _reference_prices(market_data: pd.DataFrame, symbols: list[str], signal_date: date) -> dict[str, float]:
    md = market_data[pd.to_datetime(market_data["date"]) <= pd.Timestamp(signal_date)]
    out: dict[str, float] = {}
    for sym in symbols:
        part = md[md["symbol"] == sym]
        if part.empty:
            raise KeyError(f"{sym} 在 {signal_date} 无参考价")
        out[sym] = float(part.sort_values("date").iloc[-1]["close"])
    return out


def next_data_trading_day(market_data: pd.DataFrame, signal_date: date) -> date | None:
    """取 signal_date 之后第一个有行情数据的交易日（尊重真实交易日历）。"""
    dates = sorted({pd.to_datetime(d).date() for d in pd.to_datetime(market_data["date"])})
    for d in dates:
        if d > signal_date:
            return d
    return None


def plan_orders(
    targets: list,
    current_positions: dict[str, dict],
    cash: float,
    total_equity: float,
    market_data: pd.DataFrame,
    signal_date: date | str,
    config: dict,
) -> tuple[list[PlannedOrder], dict]:
    """按目标组合与当前持仓计算调仓订单。

    规则（固定、可复现）：
    - 先卖后买；
    - 卖出数量 = min(可卖数量, 按参考价差额向下取整到整手)；
    - 买入数量向下取整到整手，不足一手不生成订单；
    - 权重变化低于 rebalance_threshold_pct 不生成订单；
    - 买入预算 = 现金 + 卖出参考回笼；按 (排名, 符号) 优先级依次满足，
      预算不足时缩减数量，缩减后不足一手则放弃该买单（记录在诊断中）。
    """
    strategy_cfg = config.get("strategy", {})
    exec_cfg = config.get("execution", {})
    port_cfg = config.get("portfolio", {})
    lot = int(exec_cfg.get("lot_size", 100))
    threshold = float(port_cfg.get("rebalance_threshold_pct", 0.0))
    sd = pd.Timestamp(signal_date).date()
    ed = next_data_trading_day(market_data, sd)
    if ed is None:
        return [], {"no_next_trading_day": True}
    strategy_id = strategy_cfg.get("id", "etf_rotation_v0")
    gen_version = str(exec_cfg.get("order_generation_version_prefix", "v1"))

    target_map: dict[str, float] = {t.symbol: float(t.target_weight) for t in targets}
    all_symbols = sorted(set(target_map) | set(current_positions))
    prices = _reference_prices(market_data, all_symbols, sd)

    orders: list[PlannedOrder] = []
    diagnostics: dict = {"skipped_buys": []}

    # ---- 第一轮：卖单（先卖） ----
    sell_proceeds = 0.0
    for sym in sorted(current_positions):
        pos = current_positions[sym]
        qty = int(pos.get("quantity", 0))
        available = int(pos.get("available_quantity", qty))
        target_w = target_map.get(sym, 0.0)
        if qty <= 0:
            continue
        current_value = qty * prices[sym]
        target_value = total_equity * target_w
        delta_value = target_value - current_value
        if abs(delta_value) <= total_equity * threshold:
            continue
        if delta_value < 0:
            q = min(available, floor_to_lot(abs(delta_value) / prices[sym], lot))
            if q <= 0:
                continue
            sell_proceeds += q * prices[sym]
            orders.append(
                PlannedOrder(
                    order_key="|".join([strategy_id, sd.isoformat(), ed.isoformat(), sym, "SELL", gen_version]),
                    strategy_id=strategy_id,
                    signal_date=sd,
                    execution_date=ed,
                    symbol=sym,
                    side="SELL",
                    planned_quantity=q,
                    reference_price=prices[sym],
                    planned_value=money_round(q * prices[sym]),
                    target_weight=target_w,
                    priority=0,
                    reason=f"目标权重 {target_w:.2%} < 当前权重，减持 {q} 股",
                )
            )

    # ---- 第二轮：买单 ----
    budget = max(0.0, cash + sell_proceeds)
    buy_candidates: list[tuple] = []
    for t in sorted(
        [t for t in targets if float(t.target_weight) > 0],
        key=lambda x: (getattr(x, "rank", 999), x.symbol),
    ):
        sym = t.symbol
        qty = int(current_positions.get(sym, {}).get("quantity", 0))
        current_value = qty * prices[sym]
        target_value = total_equity * float(t.target_weight)
        delta_value = target_value - current_value
        if delta_value <= total_equity * threshold:
            continue
        raw = delta_value / prices[sym]
        q = floor_to_lot(raw, lot)
        if q <= 0:
            continue
        buy_candidates.append((t, q, q * prices[sym]))

    remaining = budget
    for t, q, value in buy_candidates:
        affordable = floor_to_lot(remaining / prices[t.symbol], lot)
        final_q = min(q, affordable)
        if final_q < lot:
            diagnostics["skipped_buys"].append(
                {"symbol": t.symbol, "reason": "资金不足或不足一手", "planned_qty": q}
            )
            continue
        remaining -= final_q * prices[t.symbol]
        orders.append(
            PlannedOrder(
                order_key="|".join([strategy_id, sd.isoformat(), ed.isoformat(), t.symbol, "BUY", gen_version]),
                strategy_id=strategy_id,
                signal_date=sd,
                execution_date=ed,
                symbol=t.symbol,
                side="BUY",
                planned_quantity=final_q,
                reference_price=prices[t.symbol],
                planned_value=money_round(final_q * prices[t.symbol]),
                target_weight=float(t.target_weight),
                priority=int(getattr(t, "rank", 999)),
                reason=t.reason,
            )
        )

    return orders, diagnostics
