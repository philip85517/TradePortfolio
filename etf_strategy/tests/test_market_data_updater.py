from __future__ import annotations

import pandas as pd

from src.market_data_store import latest_bar_ts, load_bars, upsert_bars
from src.market_data_updater import Instrument, build_requests, update_market_data


def test_build_requests_uses_incremental_start_after_latest_bar(tmp_path):
    db = tmp_path / "market.duckdb"
    upsert_bars(
        pd.DataFrame(
            {
                "market": ["a_share"],
                "symbol": ["000001"],
                "timeframe": ["1d"],
                "ts": ["2024-01-03"],
                "open": [1],
                "high": [2],
                "low": [1],
                "close": [1.5],
                "volume": [100],
            }
        ),
        db_path=db,
        source="unit",
    )

    requests = build_requests(
        [Instrument(market="a_share", symbol="000001", provider="synthetic")],
        ["1d"],
        pd.Timestamp("2024-01-01"),
        pd.Timestamp("2024-01-10"),
        db,
        incremental=True,
    )

    assert len(requests) == 1
    assert requests[0].start == pd.Timestamp("2024-01-04")


def test_update_market_data_writes_all_requested_timeframes(tmp_path):
    db = tmp_path / "market.duckdb"
    requests = build_requests(
        [
            Instrument(market="a_share", symbol="000001", provider="synthetic"),
            Instrument(market="crypto", symbol="BTC/USDT", provider="synthetic"),
        ],
        ["5m", "15m", "30m", "1h", "1d", "1w", "1mo"],
        pd.Timestamp("2024-01-01"),
        pd.Timestamp("2024-01-12"),
        db,
        incremental=False,
    )

    result = update_market_data(requests, db_path=db, provider_name="synthetic", chunk_days=3)
    data = load_bars(db)

    assert result["failures"] == []
    assert set(data["timeframe"]) == {"5m", "15m", "30m", "1h", "1d", "1w", "1mo"}
    assert set(data["market"]) == {"a_share", "crypto"}
    assert latest_bar_ts(db, "crypto", "BTC/USDT", "5m") is not None
