"""回测数据日期工具（与 paper 共用）。"""

from __future__ import annotations

import pandas as pd


def data_dates(market_data: pd.DataFrame, start, end) -> list[str]:
    dates = sorted({pd.to_datetime(d).date() for d in pd.to_datetime(market_data["date"])})
    return [d.isoformat() for d in dates if start <= d <= end]

