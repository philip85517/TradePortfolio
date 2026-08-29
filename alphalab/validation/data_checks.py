"""数据日期与完整性检查（SPEC 18：未来函数防护）。"""

from __future__ import annotations

from datetime import date

import pandas as pd

from ..strategies.base import FutureDataError
from ..utils import parse_date


def check_no_future_data(market_data: pd.DataFrame, as_of_date: date | str, reject: bool = True) -> bool:
    """确保 market_data 的最大日期不超过 as_of_date。"""
    if market_data is None or market_data.empty:
        return True
    max_date = pd.to_datetime(market_data["date"]).max()
    cutoff = pd.Timestamp(parse_date(as_of_date))
    if max_date > cutoff:
        if reject:
            raise FutureDataError(
                f"检测到未来数据: 数据最大日期 {max_date.date()} > 截止日期 {cutoff.date()}"
            )
        return False
    return True


def check_required_columns(df: pd.DataFrame, required: list[str]) -> list[str]:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"行情数据缺少必需列: {missing}")
    return missing


def check_no_missing_prices(market_data: pd.DataFrame, trade_date: date | str) -> list[str]:
    """检查 trade_date 当天行情是否缺开盘价/收盘价。"""
    d = parse_date(trade_date)
    day = market_data[pd.to_datetime(market_data["date"]) == pd.Timestamp(d)]
    bad: list[str] = []
    for _, row in day.iterrows():
        if pd.isna(row.get("open")) or pd.isna(row.get("close")):
            bad.append(str(row["symbol"]))
    return bad

