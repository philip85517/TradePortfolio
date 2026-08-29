from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from alphalab.research.universe_history import (
    UniverseHistoryError,
    build_baostock_universe_history,
    load_universe_as_of,
    normalize_universe_history,
    upsert_universe_history,
    validate_universe_history,
)


def test_history_normalizes_adjacent_intervals_and_source_identity() -> None:
    frame = pd.DataFrame(
        [
            {
                "market": "a_share",
                "symbol": "000001",
                "effective_from": "2020-01-01",
                "effective_to": "2022-01-01",
                "status": "active",
                "source": "baostock",
                "snapshot_id": "bs-2026-08-29",
            },
            {
                "market": "a_share",
                "symbol": "000001",
                "effective_from": "2022-01-01",
                "effective_to": None,
                "status": "active",
                "source": "baostock",
                "snapshot_id": "bs-2026-08-29",
            },
        ]
    )

    normalized = normalize_universe_history(frame, source=None, snapshot_id=None)

    assert normalized["effective_from"].dtype == "datetime64[ns]"
    assert normalized["effective_to"].isna().sum() == 1
    assert set(normalized["source"]) == {"baostock"}


def test_history_validator_reports_overlapping_intervals() -> None:
    frame = pd.DataFrame(
        [
            {
                "market": "a_share",
                "symbol": "000001",
                "effective_from": "2020-01-01",
                "effective_to": "2022-06-01",
                "status": "active",
                "source": "baostock",
                "snapshot_id": "s1",
            },
            {
                "market": "a_share",
                "symbol": "000001",
                "effective_from": "2022-01-01",
                "effective_to": None,
                "status": "active",
                "source": "baostock",
                "snapshot_id": "s1",
            },
        ]
    )

    report = validate_universe_history(frame)

    assert report["interval_conflicts"]


def test_history_upsert_and_as_of_query_use_latest_effective_row(tmp_path: Path) -> None:
    frame = pd.DataFrame(
        [
            {
                "market": "a_share",
                "symbol": "000001",
                "effective_from": "2020-01-01",
                "effective_to": "2023-01-01",
                "status": "active",
                "industry_level1": "金融",
                "source": "baostock",
                "snapshot_id": "s1",
            },
            {
                "market": "a_share",
                "symbol": "000001",
                "effective_from": "2023-01-01",
                "effective_to": None,
                "status": "active",
                "industry_level1": "科技",
                "source": "baostock",
                "snapshot_id": "s1",
            },
        ]
    )

    result = upsert_universe_history(frame, tmp_path / "market.duckdb")
    loaded = load_universe_as_of(tmp_path / "market.duckdb", date(2024, 1, 2), "a_share")

    assert result["rows"] == 2
    assert loaded.loc[0, "industry_level1"] == "科技"
    assert loaded.loc[0, "snapshot_id"] == "s1"


def test_history_upsert_rejects_bad_intervals(tmp_path: Path) -> None:
    frame = pd.DataFrame(
        [
            {
                "market": "a_share",
                "symbol": "000001",
                "effective_from": "2023-01-01",
                "effective_to": "2022-01-01",
                "status": "active",
                "source": "baostock",
                "snapshot_id": "s1",
            }
        ]
    )

    with pytest.raises(UniverseHistoryError, match="历史 universe"):
        upsert_universe_history(frame, tmp_path / "market.duckdb")


def test_baostock_basic_rows_become_pit_listing_intervals() -> None:
    raw = pd.DataFrame(
        [
            {"code": "sh.600000", "code_name": "浦发银行", "ipoDate": "1999-11-10", "outDate": "", "type": "1", "status": "1"},
            {"code": "sh.000001", "code_name": "上证指数", "ipoDate": "1991-07-15", "outDate": "", "type": "2", "status": "1"},
            {"code": "sz.000004", "code_name": "退市国农", "ipoDate": "1991-01-01", "outDate": "2022-01-01", "type": "1", "status": "0"},
        ]
    )

    history = build_baostock_universe_history(raw, snapshot_id="bs-2026-08-29")

    assert set(history["symbol"]) == {"600000", "000004"}
    delisted = history.set_index("symbol").loc["000004"]
    assert delisted["effective_from"].date() == date(1991, 1, 1)
    assert delisted["effective_to"].date() == date(2022, 1, 1)
    assert delisted["status"] == "active"
    assert set(history["source"]) == {"baostock"}
