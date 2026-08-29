from __future__ import annotations

import pandas as pd

from src.market_data_store import latest_bar_ts, load_bars, market_database_summary, upsert_bars


def _bars(close: float = 10.5) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "market": ["us", "us"],
            "symbol": ["AAPL", "AAPL"],
            "timeframe": ["1d", "1d"],
            "ts": ["2024-01-02", "2024-01-03"],
            "open": [10.0, 11.0],
            "high": [11.0, 12.0],
            "low": [9.5, 10.5],
            "close": [10.5, close],
            "volume": [1000, 1200],
            "amount": [10500, 15000],
            "source": ["unit", "unit"],
        }
    )


def test_upsert_bars_is_idempotent_and_replaces_existing_rows(tmp_path):
    db = tmp_path / "market.duckdb"

    first = upsert_bars(_bars(close=12.5), db_path=db, source="unit")
    second = upsert_bars(_bars(close=13.5), db_path=db, source="unit")

    data = load_bars(db, markets=["us"], symbols=["AAPL"], timeframes=["1d"])
    summary = market_database_summary(db)

    assert first["rows"] == 2
    assert second["rows"] == 2
    assert len(data) == 2
    assert data.loc[data["ts"] == pd.Timestamp("2024-01-03"), "close"].iloc[0] == 13.5
    assert summary["rows"] == 2
    assert summary["instruments"] == 1


def test_latest_bar_ts_returns_latest_timestamp(tmp_path):
    db = tmp_path / "market.duckdb"
    upsert_bars(_bars(), db_path=db, source="unit")

    latest = latest_bar_ts(db, "us", "AAPL", "1d")

    assert latest == pd.Timestamp("2024-01-03")
