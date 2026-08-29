from __future__ import annotations

import pandas as pd
import pytest

from alphalab.research.chart_data import ChartDataError, normalize_timeframe, prepare_chart_data


def _rows() -> pd.DataFrame:
    dates = pd.to_datetime([
        "2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08",
        "2025-01-31", "2025-02-03",
    ])
    return pd.DataFrame(
        {
            "date": dates,
            "open": [10, 11, 12, 13, 14, 20, 21],
            "high": [12, 13, 14, 15, 16, 22, 23],
            "low": [9, 10, 11, 12, 13, 19, 20],
            "close": [11, 12, 13, 14, 15, 21, 22],
            "volume": [100, 110, 120, 130, 140, 200, 210],
            "amount": [1000, 1100, 1200, 1300, 1400, 2000, 2100],
        }
    )


def test_weekly_aggregation_uses_first_last_extremes_and_actual_date() -> None:
    weekly = prepare_chart_data(_rows(), timeframe="1w", ema_periods=())

    assert list(weekly["date"].dt.date) == [pd.Timestamp("2025-01-03").date(), pd.Timestamp("2025-01-08").date(), pd.Timestamp("2025-01-31").date(), pd.Timestamp("2025-02-03").date()]
    assert weekly.iloc[1][["open", "high", "low", "close", "volume", "amount"]].tolist() == [12, 16, 11, 15, 390, 3900]


def test_monthly_aggregation_keeps_month_boundary_and_sums_volume() -> None:
    monthly = prepare_chart_data(_rows(), timeframe="1mo", ema_periods=())

    assert list(monthly["date"].dt.date) == [pd.Timestamp("2025-01-31").date(), pd.Timestamp("2025-02-03").date()]
    assert monthly.iloc[0][["open", "high", "low", "close", "volume"]].tolist() == [10, 22, 9, 21, 800]


def test_ema_is_deterministic_and_has_warmup_nulls() -> None:
    daily = prepare_chart_data(_rows(), ema_periods=(3,))

    assert daily["ema_3"].iloc[:2].isna().all()
    assert daily["ema_3"].iloc[2] == pytest.approx(12.25)
    assert daily["ema_3"].iloc[-1] == pytest.approx(19.765625)


def test_timeframe_aliases_and_invalid_values() -> None:
    assert normalize_timeframe("weekly") == "1w"
    assert normalize_timeframe("1m") == "1mo"
    with pytest.raises(ChartDataError, match="timeframe"):
        normalize_timeframe("5m")
