from __future__ import annotations

import pandas as pd

from src.signals import generate_entry_signal


def _history(close_position=0.8, three_below=False):
    dates = pd.bdate_range("2025-01-01", periods=8)
    rows = []
    closes = [10.5, 10.6, 10.7, 9.8, 9.7, 10.1, 10.2, 10.8]
    if three_below:
        closes[-4:-1] = [9.8, 9.7, 9.9]
    for i, date in enumerate(dates):
        high = 11
        low = 10
        close = closes[i]
        if i == len(dates) - 1:
            close = low + close_position * (high - low)
        rows.append(
            {
                "date": date,
                "symbol": "ETF001",
                "open": 10.2,
                "high": high,
                "low": low if i != 4 else 9.9,
                "close": close,
                "ema20": 10,
                "ma60": 9,
                "atr20": 0.2,
            }
        )
    return pd.DataFrame(rows)


def test_pullback_reclaim_triggers_entry(config):
    assert generate_entry_signal("ETF001", _history(), config)


def test_three_closes_below_ema_blocks_entry(config):
    assert not generate_entry_signal("ETF001", _history(three_below=True), config)


def test_low_close_position_blocks_entry(config):
    assert not generate_entry_signal("ETF001", _history(close_position=0.7), config)

