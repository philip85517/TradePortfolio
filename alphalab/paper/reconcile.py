"""T+1 日收盘后对账（SPEC 12.3 / 第 19 节）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from ..config import ensure_required_keys
from ..data import Universe
from ..data.loader import load_market_data
from ..storage import PaperDatabase
from ..utils import parse_date
from ..validation import check_no_future_data, run_account_checks
from .common import apply_universe_data_flags


@dataclass
class ReconcileResult:
    trade_date: str
    anomalies: list = field(default_factory=list)
    run_id: str = ""


def reconcile(
    trade_date: date | str,
    config: dict,
    db: PaperDatabase,
    universe: Universe,
    market_data: pd.DataFrame | None = None,
) -> ReconcileResult:
    """执行每日对账，将异常写入账本。"""
    ensure_required_keys(config)
    d = parse_date(trade_date)
    d_str = d.isoformat()
    symbols = universe.symbols()
    if market_data is None:
        data_config = apply_universe_data_flags(config, universe)
        market_data, _ = load_market_data(symbols, d, d, data_config)
    md = market_data[pd.to_datetime(market_data["date"]) <= pd.Timestamp(d)]
    check_no_future_data(md, d)

    run_id = db.start_run("RECONCILE", d_str)
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
    return ReconcileResult(trade_date=d_str, anomalies=anomalies, run_id=run_id)
