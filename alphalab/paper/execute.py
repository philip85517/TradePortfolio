"""T+1 日按开盘价模拟成交（SPEC 12.2）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from ..brokers import PaperBrokerAdapter
from ..config import config_hash, ensure_required_keys
from ..data import Universe
from ..data.loader import load_market_data
from ..storage import PaperDatabase
from ..utils import code_commit, json_hash, money_round, now_iso, parse_date
from ..validation import check_no_future_data, run_account_checks
from .common import apply_universe_data_flags, close_prices, latest_target_weights


class AlreadyExecutedError(RuntimeError):
    pass


@dataclass
class ExecuteResult:
    execution_date: str
    run_id: str
    fills: list = field(default_factory=list)
    rejected: list = field(default_factory=list)
    cash: float = 0.0
    positions: dict = field(default_factory=dict)
    total_equity: float = 0.0
    anomalies: list = field(default_factory=list)


def execute(
    execution_date: date | str,
    config: dict,
    db: PaperDatabase,
    universe: Universe,
    force: bool = False,
    market_data: pd.DataFrame | None = None,
    run_checks: bool = True,
) -> ExecuteResult:
    """执行 execution_date 待成交订单，更新账本并写日终快照。"""
    ensure_required_keys(config)
    d = parse_date(execution_date)
    d_str = d.isoformat()
    if db.date_already_executed(d_str) and not force:
        raise AlreadyExecutedError(f"{d_str} 已成功执行（--force 可重跑，重跑将产生新的 run_id）")

    symbols = universe.symbols()
    if not symbols:
        raise ValueError("目标标的池为空，请先执行 `python -m alphalab universe add <代码> ...`")

    if market_data is None:
        data_config = apply_universe_data_flags(config, universe)
        market_data, snapshot = load_market_data(symbols, d, d, data_config)
    else:
        snapshot = {"reused": True, "symbols": symbols}
    md = market_data[pd.to_datetime(market_data["date"]) <= pd.Timestamp(d)]
    check_no_future_data(md, d)

    strategy_cfg = config.get("strategy", {})
    run_id = db.start_run(
        "EXECUTE",
        d_str,
        strategy_id=strategy_cfg.get("id", "etf_rotation_v0"),
        strategy_version=str(strategy_cfg.get("version", "0.1.0")),
        config_hash=config_hash(config),
        code_commit=code_commit(),
        data_snapshot_id=json_hash(snapshot),
    )

    broker = PaperBrokerAdapter(db, config)
    result = broker.execute_pending(d, md, run_id)
    cash = result["cash"]
    positions = result["positions"]
    closes = close_prices(md, d)
    target_w = latest_target_weights(db, d)

    market_value = 0.0
    position_rows: list[dict] = []
    for sym, pos in positions.items():
        qty = int(pos.get("quantity", 0))
        price = closes.get(sym)
        if qty <= 0:
            # 平仓当日写入 0 数量快照，防止 get_positions 回退到卖出前旧持仓
            position_rows.append(
                {
                    "trade_date": d_str,
                    "symbol": sym,
                    "quantity": 0,
                    "available_quantity": 0,
                    "average_cost": 0.0,
                    "close_price": price or 0.0,
                    "market_value": 0.0,
                    "unrealized_pnl": 0.0,
                    "realized_pnl": money_round(float(pos.get("realized_pnl", 0.0))),
                    "actual_weight": 0.0,
                    "target_weight": float(target_w.get(sym, 0.0)),
                    "created_at": now_iso(),
                }
            )
            continue
        if price is None:
            db.insert_anomaly(
                run_id=run_id,
                trade_date=d_str,
                severity="WARN",
                anomaly_type="MISSING_CLOSE",
                symbol=sym,
                message=f"{sym} 在 {d_str} 无收盘价，持仓无法估值",
            )
            continue
        value = qty * price
        market_value += value
        avg_cost = float(pos.get("average_cost", 0.0))
        position_rows.append(
            {
                "trade_date": d_str,
                "symbol": sym,
                "quantity": qty,
                "available_quantity": int(pos.get("available_quantity", qty)),
                "average_cost": avg_cost,
                "close_price": price,
                "market_value": money_round(value),
                "unrealized_pnl": money_round((price - avg_cost) * qty),
                "realized_pnl": money_round(float(pos.get("realized_pnl", 0.0))),
                "actual_weight": 0.0,
                "target_weight": float(target_w.get(sym, 0.0)),
                "created_at": now_iso(),
            }
        )

    total_equity = cash + market_value
    for row in position_rows:
        row["actual_weight"] = money_round(row["market_value"] / total_equity, 6) if total_equity else 0.0
        db.upsert_position(row)
    prev = db.nav_before(d_str)
    prev_equity = float(prev["total_equity"]) if prev else float(db.get_initial_cash())
    daily_pnl = money_round(total_equity - prev_equity)
    daily_return = daily_pnl / prev_equity if prev_equity else 0.0
    cumulative_return = total_equity / db.get_initial_cash() - 1.0 if db.get_initial_cash() else 0.0
    turnover = sum(f.gross_amount for f in result["fills"]) / total_equity if total_equity else 0.0
    commission = sum(f.commission for f in result["fills"])
    slippage = sum(f.slippage_amount for f in result["fills"])

    db.upsert_daily_nav(
        {
            "trade_date": d_str,
            "cash": money_round(cash),
            "market_value": money_round(market_value),
            "total_equity": money_round(total_equity),
            "daily_pnl": daily_pnl,
            "daily_return": money_round(daily_return, 6),
            "cumulative_return": money_round(cumulative_return, 6),
            "turnover": money_round(turnover, 6),
            "commission": money_round(commission),
            "slippage": money_round(slippage),
            "created_at": now_iso(),
        }
    )

    anomalies: list[dict] = []
    if run_checks:
        anomalies = run_account_checks(db, d_str, md, config)
        for a in anomalies:
            db.insert_anomaly(
                run_id=run_id,
                trade_date=d_str,
                severity=a["severity"],
                anomaly_type=a["anomaly_type"],
                symbol=None,
                message=a["message"],
            )
    fatal = any(a["severity"] == "FATAL" for a in anomalies)
    db.finish_run(run_id, "FAILED" if fatal else "SUCCESS")

    return ExecuteResult(
        execution_date=d_str,
        run_id=run_id,
        fills=result["fills"],
        rejected=result["rejected"],
        cash=cash,
        positions=positions,
        total_equity=total_equity,
        anomalies=anomalies,
    )
