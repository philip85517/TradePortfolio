"""paper 管线共享工具。"""

from __future__ import annotations

import copy
from datetime import date

import pandas as pd

from ..strategies.base import current_positions_to_dict
from ..storage import PaperDatabase
from ..utils import parse_date


def close_prices(market_data: pd.DataFrame, trade_date: date | str) -> dict[str, float]:
    d = pd.Timestamp(parse_date(trade_date))
    day = market_data[pd.to_datetime(market_data["date"]) == d]
    return {row["symbol"]: float(row["close"]) for _, row in day.iterrows()}


def equity_components(
    db: PaperDatabase,
    market_data: pd.DataFrame,
    trade_date: date | str,
) -> tuple[float, float, float]:
    """返回 (cash, market_value, total_equity)。"""
    d = str(trade_date)
    cash = db.get_cash(d)
    closes = close_prices(market_data, d)
    mv = 0.0
    for row in db.get_positions(d):
        price = closes.get(row["symbol"])
        if price is not None:
            mv += row["quantity"] * price
    return cash, mv, cash + mv


def strategy_positions(db: PaperDatabase, trade_date: date | str) -> dict[str, dict]:
    return current_positions_to_dict(db.get_positions(str(trade_date)))


def latest_target_weights(db: PaperDatabase, as_of_date: date | str) -> dict[str, float]:
    rows = db.get_latest_signals(str(as_of_date))
    return {r["symbol"]: float(r["target_weight"]) for r in rows}


def data_dates(market_data: pd.DataFrame, start: date, end: date) -> list[str]:
    """从行情中提取 [start, end] 区间内的交易日（按数据实际存在日期）。"""
    dates = sorted({pd.to_datetime(d).date() for d in pd.to_datetime(market_data["date"])})
    return [d.isoformat() for d in dates if start <= d <= end]


def apply_universe_data_flags(config: dict, universe) -> dict:
    """把标的池中的 synthetic 标记传给数据层（强制合成行情）。"""
    cfg = copy.deepcopy(config)
    force = [it["symbol"] for it in universe.items() if it.get("synthetic")]
    if force:
        cfg.setdefault("data", {})["force_synthetic_symbols"] = sorted(set(force))
    return cfg
