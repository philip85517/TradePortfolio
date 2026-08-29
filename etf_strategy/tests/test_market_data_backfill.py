from __future__ import annotations

import pandas as pd

from src.market_data_backfill import CoverageWindow, build_coverage_requests, inspect_coverage
from src.market_data_store import upsert_bars
from src.market_data_updater import Instrument


def _bars(symbol: str, dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "market": "a_share",
            "symbol": symbol,
            "timeframe": "1d",
            "ts": dates,
            "open": [10.0 + index for index in range(len(dates))],
            "high": [11.0 + index for index in range(len(dates))],
            "low": [9.0 + index for index in range(len(dates))],
            "close": [10.5 + index for index in range(len(dates))],
            "volume": [100.0] * len(dates),
            "adjusted": True,
            "adjustment": "qfq",
        }
    )


def test_inspect_coverage_reports_leading_and_trailing_gaps(tmp_path):
    db = tmp_path / "market.duckdb"
    upsert_bars(_bars("000001", ["2020-01-02", "2020-01-03"]), db_path=db, source="unit")

    report = inspect_coverage(
        db,
        [Instrument(market="a_share", symbol="000001")],
        ["1d"],
        CoverageWindow(start=pd.Timestamp("2018-01-01"), end=pd.Timestamp("2026-08-29")),
    )

    row = report.iloc[0]
    assert row["stored_start"] == pd.Timestamp("2020-01-02")
    assert row["stored_end"] == pd.Timestamp("2020-01-03")
    assert bool(row["leading_gap"])
    assert bool(row["trailing_gap"])
    assert row["status"] == "coverage_gap"


def test_build_coverage_requests_only_fetches_missing_edges(tmp_path):
    db = tmp_path / "market.duckdb"
    upsert_bars(_bars("000001", ["2020-01-02", "2020-01-03"]), db_path=db, source="unit")

    requests = build_coverage_requests(
        [Instrument(market="a_share", symbol="000001", provider="synthetic")],
        ["1d"],
        start=pd.Timestamp("2018-01-01"),
        end=pd.Timestamp("2026-08-29"),
        db_path=db,
    )

    assert [(request.start, request.end) for request in requests] == [
        (pd.Timestamp("2018-01-01"), pd.Timestamp("2020-01-01")),
        (pd.Timestamp("2020-01-04"), pd.Timestamp("2026-08-29")),
    ]


def test_build_coverage_requests_skips_complete_symbol(tmp_path):
    db = tmp_path / "market.duckdb"
    upsert_bars(_bars("000001", ["2018-01-01", "2026-08-29"]), db_path=db, source="unit")

    requests = build_coverage_requests(
        [Instrument(market="a_share", symbol="000001")],
        ["1d"],
        start=pd.Timestamp("2018-01-01"),
        end=pd.Timestamp("2026-08-29"),
        db_path=db,
    )

    assert requests == []
