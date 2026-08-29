from __future__ import annotations

import pandas as pd

from src.market_data_quality import ValidationWindow, validate_database
from src.market_data_store import upsert_bars
from src.market_data_updater import Instrument


def test_validate_database_accepts_complete_qfq_equity_data(tmp_path):
    db = tmp_path / "market.duckdb"
    upsert_bars(
        pd.DataFrame(
            {
                "market": ["us", "us", "us"],
                "symbol": ["AAPL", "AAPL", "AAPL"],
                "timeframe": ["1d", "1d", "1d"],
                "ts": ["2024-01-01", "2024-01-02", "2024-01-03"],
                "open": [10, 11, 12],
                "high": [11, 12, 13],
                "low": [9, 10, 11],
                "close": [10.5, 11.5, 12.5],
                "volume": [100, 120, 140],
                "adjusted": [True, True, True],
                "adjustment": ["qfq", "qfq", "qfq"],
            }
        ),
        db_path=db,
        source="unit",
    )

    coverage, problems = validate_database(
        db,
        [Instrument(market="us", symbol="AAPL")],
        ["1d"],
        ValidationWindow(start=pd.Timestamp("2024-01-01"), end=pd.Timestamp("2024-01-03")),
    )

    assert problems.empty
    assert coverage.loc[0, "status"] == "ok"


def test_validate_database_flags_adjustment_mismatch(tmp_path):
    db = tmp_path / "market.duckdb"
    upsert_bars(
        pd.DataFrame(
            {
                "market": ["a_share"],
                "symbol": ["000001"],
                "timeframe": ["1d"],
                "ts": ["2024-01-01"],
                "open": [10],
                "high": [11],
                "low": [9],
                "close": [10.5],
                "volume": [100],
                "adjusted": [False],
                "adjustment": ["none"],
            }
        ),
        db_path=db,
        source="unit",
    )

    _, problems = validate_database(
        db,
        [Instrument(market="a_share", symbol="000001")],
        ["1d"],
        ValidationWindow(start=pd.Timestamp("2024-01-01"), end=pd.Timestamp("2024-01-01")),
    )

    assert problems.loc[0, "status"] == "adjustment_mismatch"
