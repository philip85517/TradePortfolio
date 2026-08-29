from __future__ import annotations

import pandas as pd

from src.market_data_providers import FetchRequest, SyntheticProvider, _standardize_akshare_frame


def test_standardize_akshare_frame_maps_chinese_columns():
    raw = pd.DataFrame(
        {
            "日期": ["2024-01-02"],
            "开盘": [10],
            "最高": [11],
            "最低": [9],
            "收盘": [10.5],
            "成交量": [1000],
            "成交额": [10500],
        }
    )
    request = FetchRequest(
        market="a_share",
        symbol="000001",
        timeframe="1d",
        start=pd.Timestamp("2024-01-01"),
        end=pd.Timestamp("2024-01-31"),
    )

    data = _standardize_akshare_frame(raw, request, "akshare")

    assert list(data.columns) == [
        "market",
        "symbol",
        "timeframe",
        "ts",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "source",
        "adjusted",
        "adjustment",
    ]
    assert data.loc[0, "market"] == "a_share"
    assert data.loc[0, "close"] == 10.5
    assert data.loc[0, "adjustment"] == "qfq"


def test_synthetic_provider_generates_normalized_bars():
    provider = SyntheticProvider()
    request = FetchRequest(
        market="us",
        symbol="AAPL",
        timeframe="1h",
        start=pd.Timestamp("2024-01-01"),
        end=pd.Timestamp("2024-01-03"),
    )

    data = provider.fetch_ohlcv(request)

    assert not data.empty
    assert set(data["timeframe"]) == {"1h"}
    assert data["ts"].is_monotonic_increasing
