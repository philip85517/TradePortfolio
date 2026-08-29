from __future__ import annotations

import numpy as np
import pandas as pd

from src.indicators import add_indicators


def _bars(close: list[float]) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-01", periods=len(close))
    return pd.DataFrame(
        {
            "date": dates,
            "symbol": "ETF001",
            "name": "ETF",
            "open": close,
            "high": np.array(close) + 1,
            "low": np.array(close) - 1,
            "close": close,
            "volume": 1_000_000,
            "amount": 50_000_000,
            "fund_size": 800_000_000,
            "premium_discount_rate": 0.0,
            "asset_class": "A_SHARE_BROAD",
            "is_leverage": False,
            "is_inverse": False,
            "is_active": False,
            "is_single_stock": False,
            "listing_date": pd.Timestamp("2020-01-01"),
        }
    )


def test_ema_atr_effective_move_and_close_position(config):
    close = list(range(10, 150))
    df = add_indicators(_bars(close), config)
    expected_ema = pd.Series(close).ewm(span=20, adjust=False).mean().iloc[-1]
    assert df.iloc[-1]["ema20"] == expected_ema
    assert df.iloc[-1]["atr20"] == 2.0
    assert df.iloc[-1]["effective_move_20d"] == 1.0
    assert df.iloc[-1]["daily_close_position"] == 0.5


def test_flat_day_close_position_is_half(config):
    df = _bars(list(range(10, 150)))
    df.loc[df.index[-1], ["high", "low", "close"]] = 100
    out = add_indicators(df, config)
    assert out.iloc[-1]["daily_close_position"] == 0.5

