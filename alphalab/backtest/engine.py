"""内存回测：与模拟交易使用同一策略函数、订单规划与撮合纯函数。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from ..brokers.paper_broker import simulate_fills
from ..config import ensure_required_keys
from ..data.loader import load_etf_metadata, load_market_data
from ..portfolio.order_planner import plan_orders
from ..strategies.etf_rotation_v0 import generate_target_portfolio
from ..utils import money_round, parse_date
from .common_data_dates import data_dates


@dataclass
class BacktestResult:
    start: str
    end: str
    nav: pd.DataFrame = field(default_factory=pd.DataFrame)
    fills: list = field(default_factory=list)
    orders: list = field(default_factory=list)
    position_snapshots: list = field(default_factory=list)
    final_equity: float = 0.0


def run_backtest(
    symbols: list[str],
    start: date | str,
    end: date | str,
    config: dict,
    initial_cash: float,
    market_data: pd.DataFrame | None = None,
) -> BacktestResult:
    """按交易日回测：先撮合当日待执行订单（开盘），再在收盘后生成次日计划。"""
    ensure_required_keys(config)
    s = parse_date(start)
    e = parse_date(end)
    if market_data is None:
        cfg_data = dict(config)
        cfg_data.setdefault("data", {})
        market_data, _ = load_market_data(symbols, s, e, cfg_data)
    days = data_dates(market_data, s, e)
    if not days:
        raise ValueError(f"区间 [{s}, {e}] 内无行情数据")
    meta = load_etf_metadata(symbols, config, market_data)

    cash = float(initial_cash)
    positions: dict[str, dict] = {}
    pending: list = []
    nav_rows: list[dict] = []
    fills_out: list = []
    orders_out: list = []
    snapshots: list[dict] = []

    for d in days:
        d_ts = pd.Timestamp(d)
        day = market_data[pd.to_datetime(market_data["date"]) == d_ts]
        due_orders = [o for o in pending if o.execution_date.isoformat() == d]
        open_prices = {
            row["symbol"]: float(row["open"])
            for _, row in day.iterrows()
            if row["symbol"] in {o.symbol for o in due_orders} and pd.notna(row["open"])
        }
        if due_orders:
            result = simulate_fills(due_orders, positions, cash, open_prices, config, trade_date=d)
            cash = result["cash"]
            positions = result["positions"]
            fills_out.extend(result["fills"])

        closes = {row["symbol"]: float(row["close"]) for _, row in day.iterrows()}
        market_value = sum(int(p.get("quantity", 0)) * closes.get(sym, 0.0) for sym, p in positions.items())
        total_equity = money_round(cash + market_value)

        md = market_data[pd.to_datetime(market_data["date"]) <= d_ts]
        strategy_positions = {
            sym: {
                "quantity": int(p.get("quantity", 0)),
                "available_quantity": int(p.get("available_quantity", 0)),
                "average_cost": float(p.get("average_cost", 0.0)),
            }
            for sym, p in positions.items()
        }
        result_t = generate_target_portfolio(d, md, meta, strategy_positions, total_equity, config)
        # plan_orders 需要完整行情以确定下一真实交易日；参考价在函数内部截断
        orders, _ = plan_orders(list(result_t.targets), strategy_positions, cash, total_equity, market_data, d, config)
        pending = orders
        orders_out.extend(orders)

        prev_equity = nav_rows[-1]["total_equity"] if nav_rows else initial_cash
        daily_pnl = money_round(total_equity - prev_equity)
        nav_rows.append(
            {
                "trade_date": d,
                "cash": money_round(cash),
                "market_value": money_round(market_value),
                "total_equity": total_equity,
                "daily_pnl": daily_pnl,
                "daily_return": money_round(daily_pnl / prev_equity, 6) if prev_equity else 0.0,
                "cumulative_return": money_round(total_equity / initial_cash - 1.0, 6) if initial_cash else 0.0,
                "turnover": money_round(sum(f.gross_amount for f in fills_out if f.trade_date == d) / total_equity, 6)
                if total_equity
                else 0.0,
                "commission": money_round(sum(f.commission for f in fills_out if f.trade_date == d)),
                "slippage": money_round(sum(f.slippage_amount for f in fills_out if f.trade_date == d)),
            }
        )
        for sym, p in positions.items():
            if int(p.get("quantity", 0)) <= 0:
                continue
            snapshots.append(
                {
                    "trade_date": d,
                    "symbol": sym,
                    "quantity": int(p["quantity"]),
                }
            )

    return BacktestResult(
        start=s.isoformat(),
        end=e.isoformat(),
        nav=pd.DataFrame(nav_rows),
        fills=fills_out,
        orders=orders_out,
        position_snapshots=snapshots,
        final_equity=nav_rows[-1]["total_equity"] if nav_rows else initial_cash,
    )
