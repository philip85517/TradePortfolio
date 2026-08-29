"""历史回放：按交易日循环 prepare → execute → reconcile（可选 report）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from ..config import ensure_required_keys
from ..data import Universe
from ..data.loader import load_market_data
from ..storage import PaperDatabase
from ..utils import money_round, parse_date
from .common import apply_universe_data_flags, data_dates
from .execute import execute
from .prepare import prepare
from .reconcile import reconcile
from .report import build_report, write_report


@dataclass
class ReplayResult:
    start: str
    end: str
    trading_days: list = field(default_factory=list)
    execute_runs: int = 0
    prepare_runs: int = 0
    total_fills: int = 0
    total_rejected: int = 0
    anomalies: int = 0
    final_equity: float = 0.0
    final_cash: float = 0.0


def replay(
    start: date | str,
    end: date | str,
    config: dict,
    db: PaperDatabase,
    universe: Universe,
    *,
    reset_account: bool = False,
    generate_reports: bool = False,
    market_data: pd.DataFrame | None = None,
) -> ReplayResult:
    """按数据实际交易日回放 [start, end]。"""
    ensure_required_keys(config)
    s = parse_date(start)
    e = parse_date(end)
    symbols = universe.symbols()
    if not symbols:
        raise ValueError("目标标的池为空，请先执行 `python -m alphalab universe add <代码> ...`")

    if reset_account:
        db.initialize(force=True)
        db.init_account(float(config.get("account", {}).get("initial_cash", 100000)), s)
    elif not db.is_initialized():
        db.initialize()
        db.init_account(float(config.get("account", {}).get("initial_cash", 100000)), s)

    if market_data is None:
        data_config = apply_universe_data_flags(config, universe)
        market_data, _ = load_market_data(symbols, s, e, data_config)
    days = data_dates(market_data, s, e)
    if not days:
        raise ValueError(f"区间 [{s}, {e}] 内无行情数据")

    result = ReplayResult(start=s.isoformat(), end=e.isoformat(), trading_days=days)
    for d in days:
        ex = execute(d, config, db, universe, market_data=market_data, run_checks=False)
        result.execute_runs += 1
        result.total_fills += len(ex.fills)
        result.total_rejected += len(ex.rejected)
        pr = prepare(d, config, db, universe, market_data=market_data)
        result.prepare_runs += 1
        rc = reconcile(d, config, db, universe, market_data=market_data)
        result.anomalies += len(rc.anomalies)
        if generate_reports:
            write_report(d, build_report(d, db, universe, config, market_data))

    nav = db.get_daily_nav(days[-1])
    result.final_equity = money_round(nav["total_equity"]) if nav else 0.0
    result.final_cash = money_round(nav["cash"]) if nav else db.get_cash(days[-1])
    return result
