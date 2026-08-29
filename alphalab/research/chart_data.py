"""研究审阅图表的确定性日线聚合与技术指标。"""

from __future__ import annotations

from datetime import date
from typing import Iterable, Sequence

import numpy as np
import pandas as pd


SUPPORTED_TIMEFRAMES = ("1d", "1w", "1mo")
DEFAULT_EMA_PERIODS = (5, 20, 60)


class ChartDataError(ValueError):
    """Raised when chart input or timeframe is invalid."""


def prepare_chart_data(
    rows: pd.DataFrame,
    *,
    timeframe: str = "1d",
    ema_periods: Sequence[int] = DEFAULT_EMA_PERIODS,
) -> pd.DataFrame:
    """Aggregate daily OHLCV and append EMA columns.

    The research database is intentionally read at daily frequency. Weekly and
    monthly candles are derived locally from those daily rows, with the last
    observed trading day used as the candle timestamp. This keeps the chart
    deterministic and avoids relying on provider-specific higher-timeframe
    tables.
    """

    normalized = _normalize_rows(rows)
    if normalized.empty:
        return _empty_chart_frame(ema_periods)
    timeframe = normalize_timeframe(timeframe)
    periods = _normalize_ema_periods(ema_periods)
    grouped = _aggregate_ohlcv(normalized, timeframe)
    for period in periods:
        grouped[f"ema_{period}"] = (
            grouped["close"].astype(float).ewm(span=period, adjust=False, min_periods=period).mean()
        )
    return grouped


def normalize_timeframe(value: str | None) -> str:
    """Return a supported chart timeframe, accepting common UI aliases."""

    aliases = {
        "d": "1d",
        "day": "1d",
        "daily": "1d",
        "1d": "1d",
        "w": "1w",
        "week": "1w",
        "weekly": "1w",
        "1w": "1w",
        "m": "1mo",
        "month": "1mo",
        "monthly": "1mo",
        "1m": "1mo",
        "1mo": "1mo",
    }
    normalized = aliases.get(str(value or "1d").strip().lower())
    if normalized is None:
        raise ChartDataError("timeframe 必须是 1d、1w 或 1mo")
    return normalized


def _normalize_rows(rows: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "open", "high", "low", "close", "volume"}
    missing = sorted(required - set(rows.columns))
    if missing:
        raise ChartDataError(f"图表行情缺少字段: {missing}")
    data = rows.copy()
    data["date"] = pd.to_datetime(data["date"], errors="coerce").dt.tz_localize(None).dt.normalize()
    for column in ["open", "high", "low", "close", "volume", "amount"]:
        if column not in data.columns:
            data[column] = np.nan
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=["date", "open", "high", "low", "close"])
    return data.sort_values("date", kind="mergesort").drop_duplicates("date", keep="last").reset_index(drop=True)


def _aggregate_ohlcv(data: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if timeframe == "1d":
        return data[["date", "open", "high", "low", "close", "volume", "amount"]].copy()
    frequency = "W-FRI" if timeframe == "1w" else "ME"
    grouped = (
        data.set_index("date")
        .resample(frequency, label="right", closed="right")
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
                "amount": "sum",
            }
        )
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
    )
    # A period can end on a holiday. Use the last actual trading date for
    # Lightweight Charts markers and for consistent daily/weekly/monthly joins.
    actual_dates = data.assign(period=data["date"].dt.to_period("W-FRI" if timeframe == "1w" else "M"))
    period_dates = (
        actual_dates.groupby("period", sort=True)["date"].max().rename("actual_date").reset_index()
    )
    grouped["period"] = grouped["date"].dt.to_period("W-FRI" if timeframe == "1w" else "M")
    grouped = grouped.merge(period_dates, on="period", how="left").drop(columns=["date", "period"])
    grouped = grouped.rename(columns={"actual_date": "date"})
    return grouped[["date", "open", "high", "low", "close", "volume", "amount"]]


def _normalize_ema_periods(periods: Iterable[int]) -> tuple[int, ...]:
    normalized: list[int] = []
    for period in periods:
        try:
            value = int(period)
        except (TypeError, ValueError) as exc:
            raise ChartDataError("EMA 周期必须是正整数") from exc
        if value <= 0:
            raise ChartDataError("EMA 周期必须是正整数")
        normalized.append(value)
    return tuple(dict.fromkeys(normalized))


def _empty_chart_frame(periods: Iterable[int]) -> pd.DataFrame:
    columns = ["date", "open", "high", "low", "close", "volume", "amount"]
    columns.extend(f"ema_{period}" for period in _normalize_ema_periods(periods))
    return pd.DataFrame(columns=columns)


__all__ = [
    "ChartDataError",
    "DEFAULT_EMA_PERIODS",
    "SUPPORTED_TIMEFRAMES",
    "normalize_timeframe",
    "prepare_chart_data",
]
