from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from alphalab.research.data_binding import (
    DataBindingError,
    auto_bind_research_db,
    ensure_research_data,
)
from alphalab.research.universe_history import upsert_industry_snapshot, upsert_universe_history


def _write_market_db(path: Path, start: str, end: str, adjustment: str = "qfq") -> None:
    with duckdb.connect(str(path)) as con:
        con.execute(
            """
            CREATE TABLE market_ohlcv (
                market VARCHAR,
                symbol VARCHAR,
                timeframe VARCHAR,
                ts TIMESTAMP,
                trade_date DATE,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume DOUBLE,
                amount DOUBLE,
                source VARCHAR,
                adjusted BOOLEAN,
                adjustment VARCHAR
            )
            """
        )
        con.execute(
            """
            INSERT INTO market_ohlcv
            SELECT 'a_share', '000001', '1d', d, d, 10, 11, 9, 10, 1000, 10000,
                   'baostock', TRUE, ?
            FROM generate_series(?::DATE, ?::DATE, INTERVAL 1 DAY) AS dates(d)
            """,
            [adjustment, start, end],
        )


def test_auto_binding_prefers_complete_qfq_database_with_latest_coverage(tmp_path: Path) -> None:
    older = tmp_path / "older.duckdb"
    newer = tmp_path / "newer.duckdb"
    _write_market_db(older, "2021-01-01", "2025-12-31")
    _write_market_db(newer, "2021-01-01", "2026-06-25")

    binding = auto_bind_research_db(
        candidate_paths=[older, newer],
        market="a_share",
        start_date=date(2025, 1, 1),
        end_date=date(2026, 1, 1),
    )

    assert binding.db_path == newer
    assert binding.coverage_status == "complete"
    assert binding.adjustment_counts == {"qfq": 2002}
    assert binding.source_counts == {"baostock": 2002}
    assert binding.data_fingerprint
    assert binding.point_in_time_universe is False
    assert binding.to_dict()["source"] == "local-duckdb"
    assert binding.to_dict()["db_path"] == str(newer)


def test_auto_binding_rejects_database_without_market_ohlcv(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.duckdb"
    with duckdb.connect(str(invalid)) as con:
        con.execute("CREATE TABLE unrelated (value INTEGER)")

    with pytest.raises(DataBindingError, match="market_ohlcv"):
        auto_bind_research_db(db_path=invalid, market="a_share")


def test_auto_binding_reports_partial_coverage_without_filling_dates(tmp_path: Path) -> None:
    partial = tmp_path / "partial.duckdb"
    _write_market_db(partial, "2021-01-01", "2025-12-31")

    binding = auto_bind_research_db(
        candidate_paths=[partial],
        market="a_share",
        start_date=date(2025, 1, 1),
        end_date=date(2026, 1, 1),
    )

    assert binding.coverage_status == "partial"
    assert binding.max_date == date(2025, 12, 31)


def test_ensure_research_data_does_not_update_complete_database(tmp_path: Path) -> None:
    complete = tmp_path / "complete.duckdb"
    _write_market_db(complete, "2021-01-01", "2026-06-25")
    calls: list[Path] = []

    binding = ensure_research_data(
        candidate_paths=[complete],
        cache_path=tmp_path / "cache.duckdb",
        market="a_share",
        start_date=date(2025, 1, 1),
        end_date=date(2026, 1, 1),
        updater=lambda target, *_: calls.append(target),
    )

    assert binding.db_path == complete
    assert binding.coverage_status == "complete"
    assert calls == []


def test_ensure_research_data_uses_updater_for_partial_database(tmp_path: Path) -> None:
    partial = tmp_path / "partial.duckdb"
    cache = tmp_path / "cache.duckdb"
    _write_market_db(partial, "2021-01-01", "2025-12-31")

    def update(target: Path, market: str, start: date, end: date) -> None:
        assert market == "a_share"
        assert (start, end) == (date(2025, 1, 1), date(2026, 1, 1))
        _write_market_db(target, "2021-01-01", "2026-06-25")

    binding = ensure_research_data(
        candidate_paths=[partial],
        cache_path=cache,
        market="a_share",
        start_date=date(2025, 1, 1),
        end_date=date(2026, 1, 1),
        updater=update,
    )

    assert binding.db_path == cache
    assert binding.coverage_status == "complete"
    assert binding.provisioning == "etf_strategy.update_stock_data"


def test_ensure_research_data_surfaces_updater_failure(tmp_path: Path) -> None:
    with pytest.raises(DataBindingError, match="自动补数失败"):
        ensure_research_data(
            candidate_paths=[],
            cache_path=tmp_path / "cache.duckdb",
            market="a_share",
            start_date=date(2025, 1, 1),
            end_date=date(2026, 1, 1),
            updater=lambda *_: (_ for _ in ()).throw(RuntimeError("provider unavailable")),
        )


def test_ensure_research_data_builds_pit_sidecar_without_writing_price_database(tmp_path: Path) -> None:
    complete = tmp_path / "complete.duckdb"
    sidecar = tmp_path / "universe-history.duckdb"
    _write_market_db(complete, "2021-01-01", "2026-06-25")

    def update_universe(target: Path) -> None:
        upsert_universe_history(
            pd.DataFrame(
                [
                    {
                        "market": "a_share",
                        "symbol": "000001",
                        "effective_from": "1991-01-01",
                        "effective_to": None,
                        "status": "active",
                        "source": "baostock",
                        "snapshot_id": "bs-2026-08-29",
                    }
                ]
            ),
            target,
        )

    binding = ensure_research_data(
        candidate_paths=[complete],
        market="a_share",
        start_date=date(2025, 1, 1),
        end_date=date(2026, 1, 1),
        require_point_in_time=True,
        universe_cache_path=sidecar,
        universe_updater=update_universe,
    )

    assert binding.db_path == complete
    assert binding.universe_db_path == sidecar
    assert binding.point_in_time_universe is True
    assert binding.point_in_time_industry is False
    assert binding.universe_snapshot_id == "bs-2026-08-29"
    assert binding.to_dict()["point_in_time_quality"] == "listing-only"


def test_ensure_research_data_binds_industry_sidecar_when_provider_is_available(tmp_path: Path) -> None:
    complete = tmp_path / "complete.duckdb"
    sidecar = tmp_path / "industry.duckdb"
    _write_market_db(complete, "2021-01-01", "2026-06-25")

    def update_industry(target: Path) -> None:
        upsert_industry_snapshot(
            pd.DataFrame(
                [
                    {
                        "market": "a_share",
                        "symbol": "000001",
                        "industry_level1": "金融业",
                        "industry_level2": "J66 货币金融服务",
                        "industry_level3": "银行",
                        "source": "baostock",
                        "snapshot_id": "industry-2026-08-29",
                    }
                ]
            ),
            target,
        )

    binding = ensure_research_data(
        candidate_paths=[complete],
        market="a_share",
        start_date=date(2025, 1, 1),
        end_date=date(2026, 1, 1),
        industry_cache_path=sidecar,
        industry_updater=update_industry,
    )

    assert binding.industry_db_path == sidecar
    assert binding.industry_source == "baostock"
    assert binding.industry_snapshot_id == "industry-2026-08-29"
    assert binding.industry_coverage == pytest.approx(1.0)
    assert binding.point_in_time_industry is False
    assert binding.to_dict()["industry_provisioning"] == "baostock.query_stock_industry"
