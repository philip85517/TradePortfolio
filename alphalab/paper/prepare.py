"""T 日收盘后生成 T+1 交易计划（SPEC 12.1）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd

from ..config import config_hash, ensure_required_keys
from ..data import Universe
from ..data.loader import load_etf_metadata, load_market_data
from ..portfolio.order_planner import plan_orders
from ..storage import PaperDatabase
from ..strategies.etf_rotation_v0 import generate_target_portfolio
from ..utils import code_commit, json_hash, parse_date
from ..validation import check_no_future_data
from .common import apply_universe_data_flags, close_prices, strategy_positions


class AlreadyPreparedError(RuntimeError):
    pass


@dataclass
class PrepareResult:
    signal_date: str
    run_id: str
    targets: list
    orders: list
    diagnostics: dict
    snapshot: dict
    total_equity: float
    cash: float


def prepare(
    signal_date: date | str,
    config: dict,
    db: PaperDatabase,
    universe: Universe,
    force: bool = False,
    market_data: pd.DataFrame | None = None,
) -> PrepareResult:
    """执行 T 日计划生成。"""
    ensure_required_keys(config)
    sd = parse_date(signal_date)
    sd_str = sd.isoformat()
    if db.has_successful_run("PREPARE", sd_str) and not force:
        raise AlreadyPreparedError(f"{sd_str} 已成功生成计划（--force 可重跑，重跑将产生新的 run_id）")

    symbols = universe.symbols()
    if not symbols:
        raise ValueError("目标标的池为空，请先执行 `python -m alphalab universe add <代码> ...`")

    if market_data is None:
        data_config = apply_universe_data_flags(config, universe)
        # 多加载约 12 个自然日，用于确定下一真实交易日（节假日安全）
        market_data, snapshot = load_market_data(symbols, sd, sd + timedelta(days=12), data_config)
    else:
        snapshot = {"reused": True, "symbols": symbols}
    md = market_data[pd.to_datetime(market_data["date"]) <= pd.Timestamp(sd)]
    check_no_future_data(md, sd)
    if md[pd.to_datetime(md["date"]) == pd.Timestamp(sd)].empty:
        raise ValueError(f"{sd.isoformat()} 无行情数据（非交易日或数据缺失），无法生成计划")

    meta = load_etf_metadata(symbols, config, md)
    positions = strategy_positions(db, sd)
    cash = db.get_cash(sd_str)
    closes = close_prices(md, sd)
    market_value = sum(int(p["quantity"]) * closes[s] for s, p in positions.items() if s in closes)
    total_equity = cash + market_value

    result = generate_target_portfolio(sd, md, meta, positions, total_equity, config)
    orders, order_diag = plan_orders(
        list(result.targets), positions, cash, total_equity, market_data, sd, config
    )

    snapshot_id = json_hash(snapshot)
    strategy_cfg = config.get("strategy", {})
    run_id = db.start_run(
        "PREPARE",
        sd_str,
        strategy_id=strategy_cfg.get("id", "etf_rotation_v0"),
        strategy_version=str(strategy_cfg.get("version", "0.1.0")),
        config_hash=config_hash(config),
        code_commit=code_commit(),
        data_snapshot_id=snapshot_id,
    )
    db.insert_signals(run_id, sd_str, result.targets)
    db.insert_orders(run_id, orders)
    db.finish_run(run_id, "SUCCESS")

    diagnostics = {**result.diagnostics, **order_diag}
    return PrepareResult(
        signal_date=sd_str,
        run_id=run_id,
        targets=list(result.targets),
        orders=orders,
        diagnostics=diagnostics,
        snapshot=snapshot,
        total_equity=total_equity,
        cash=cash,
    )
