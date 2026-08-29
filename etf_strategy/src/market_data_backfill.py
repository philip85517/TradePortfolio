"""Plan and audit date-range coverage for stored market data.

The existing updater was designed around ``latest_bar_ts``.  That is enough for a
small incremental refresh, but it cannot repair a database whose first bars start
too late.  This module keeps the planning seam separate from provider/network
code so it can be tested against a temporary DuckDB and reused by both batch
scripts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from .market_data_providers import FetchRequest
from .market_data_store import TIMEFRAMES, connect_market_db
from .market_data_updater import Instrument


@dataclass(frozen=True)
class CoverageWindow:
    start: pd.Timestamp
    end: pd.Timestamp

    def __post_init__(self) -> None:
        start = pd.Timestamp(self.start).normalize()
        end = pd.Timestamp(self.end).normalize()
        if start > end:
            raise ValueError("Coverage window start must not be after end")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)


def inspect_coverage(
    db_path: str | Path,
    instruments: Iterable[Instrument],
    timeframes: Iterable[str],
    window: CoverageWindow,
) -> pd.DataFrame:
    """Return one auditable coverage row per instrument/timeframe.

    A non-trading start/end date is accepted within one bar interval.  For
    example, a daily series ending on Friday is complete for a Saturday audit.
    The stored range itself is never extended or inferred.
    """

    timeframe_values = list(timeframes)
    unknown = sorted(set(timeframe_values) - set(TIMEFRAMES))
    if unknown:
        raise ValueError(f"Unsupported timeframe(s): {unknown}")
    expected = pd.DataFrame(
        [
            {"market": item.market, "symbol": item.symbol, "timeframe": timeframe}
            for item in instruments
            for timeframe in timeframe_values
        ]
    )
    if expected.empty:
        return _empty_report()

    with connect_market_db(db_path, read_only=Path(db_path).exists()) as con:
        actual = con.execute(
            """
            SELECT market, symbol, timeframe,
                   count(*) AS rows,
                   min(ts) AS stored_start,
                   max(ts) AS stored_end,
                   string_agg(DISTINCT source, ', ' ORDER BY source) AS sources,
                   string_agg(DISTINCT adjustment, ', ' ORDER BY adjustment) AS adjustments
            FROM market_ohlcv
            WHERE timeframe IN (SELECT unnest(?::VARCHAR[]))
            GROUP BY market, symbol, timeframe
            """,
            [timeframe_values],
        ).fetch_df()

    report = expected.merge(actual, how="left", on=["market", "symbol", "timeframe"])
    report["rows"] = report["rows"].fillna(0).astype(int)
    report["stored_start"] = pd.to_datetime(report["stored_start"])
    report["stored_end"] = pd.to_datetime(report["stored_end"])
    report["stale_days"] = (window.end - report["stored_end"]).dt.days
    report.loc[report["stored_end"].isna(), "stale_days"] = pd.NA
    report["sources"] = report["sources"].fillna("")
    report["adjustments"] = report["adjustments"].fillna("")
    report["leading_gap"] = report.apply(lambda row: _has_leading_gap(row, window), axis=1)
    report["trailing_gap"] = report.apply(lambda row: _has_trailing_gap(row, window), axis=1)
    report["coverage_ok"] = ~(report["leading_gap"] | report["trailing_gap"])
    report["status"] = report.apply(_status, axis=1)
    return report[_report_columns()].sort_values(["market", "symbol", "timeframe"]).reset_index(drop=True)


def build_coverage_requests(
    instruments: Iterable[Instrument],
    timeframes: Iterable[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
    db_path: str | Path,
) -> list[FetchRequest]:
    """Create only the leading and trailing requests needed to cover a window."""

    window = CoverageWindow(start=start, end=end)
    instrument_values = list(instruments)
    timeframe_values = list(timeframes)
    coverage = inspect_coverage(db_path, instrument_values, timeframe_values, window)
    requests: list[FetchRequest] = []
    for item in instrument_values:
        for timeframe in timeframe_values:
            row = coverage[
                (coverage["market"] == item.market)
                & (coverage["symbol"] == item.symbol)
                & (coverage["timeframe"] == timeframe)
            ].iloc[0]
            interval = TIMEFRAMES[timeframe]
            stored_start = row["stored_start"]
            stored_end = row["stored_end"]
            if row["rows"] == 0:
                _append_request(requests, item, timeframe, window.start, window.end)
                continue
            if row["leading_gap"]:
                leading_end = min(pd.Timestamp(stored_start) - interval, window.end)
                if window.start <= leading_end:
                    _append_request(requests, item, timeframe, window.start, leading_end)
            if row["trailing_gap"]:
                trailing_start = max(pd.Timestamp(stored_end) + interval, window.start)
                if trailing_start <= window.end:
                    _append_request(requests, item, timeframe, trailing_start, window.end)
    return requests


def _append_request(
    requests: list[FetchRequest],
    instrument: Instrument,
    timeframe: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> None:
    requests.append(
        FetchRequest(
            market=instrument.market,
            symbol=instrument.symbol,
            timeframe=timeframe,
            start=pd.Timestamp(start),
            end=pd.Timestamp(end),
            source_symbol=instrument.source_symbol,
            provider=instrument.provider,
            options=instrument.options or {},
        )
    )


def _has_leading_gap(row: pd.Series, window: CoverageWindow) -> bool:
    return bool(row["rows"] == 0 or pd.isna(row["stored_start"]) or row["stored_start"] > window.start + TIMEFRAMES[str(row["timeframe"])])


def _has_trailing_gap(row: pd.Series, window: CoverageWindow) -> bool:
    return bool(row["rows"] == 0 or pd.isna(row["stored_end"]) or row["stored_end"] < window.end - TIMEFRAMES[str(row["timeframe"])])


def _status(row: pd.Series) -> str:
    if row["rows"] == 0:
        return "missing"
    if row["leading_gap"] or row["trailing_gap"]:
        return "coverage_gap"
    return "complete"


def _report_columns() -> list[str]:
    return [
        "market",
        "symbol",
        "timeframe",
        "rows",
        "stored_start",
        "stored_end",
        "stale_days",
        "sources",
        "adjustments",
        "leading_gap",
        "trailing_gap",
        "coverage_ok",
        "status",
    ]


def _empty_report() -> pd.DataFrame:
    return pd.DataFrame(columns=_report_columns())
