"""Frozen portfolio result projection contract tests."""

from __future__ import annotations

import json
import hashlib
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pandas as pd
import pytest

from alphalab.research.review import ReviewRun, ReviewState, create_review_server
from alphalab.research.workbench import create_workbench_server
from alphalab.research.result_projection import build_review_projection


def _manifest() -> dict:
    return {
        "run_id": "run-projection",
        "requested_date": "2025-01-01",
        "signal_date": "2024-12-31",
        "status": "COMPLETE",
        "spec": {
            "market": "a_share",
            "horizons": [2, 4],
            "data_quality_mode": "strict",
            "wizard_metadata": {
                "dates": {
                    "entry_date": "2025-01-02",
                    "exit_date": "2025-01-07",
                    "calendar_source": "frozen-test-calendar",
                },
                "research_sessions": [
                    "2025-01-02",
                    "2025-01-03",
                    "2025-01-06",
                    "2025-01-07",
                ],
            },
        },
        "diagnostics": {
            "entry_date": "2025-01-02",
            "data_range": ["2025-01-02", "2025-01-07"],
            "portfolios": {
                "small": {
                    "name": "小组合 <safe>",
                    "initial_cash": 1_000,
                    "reasons": {"000002": "建仓日停牌，买入未成交，预定权重保留现金"},
                },
                "large": {"name": "大组合", "initial_cash": 2_000},
            },
        },
        "portfolios": [
            {"portfolio_id": "small", "name": "小组合 <safe>", "initial_cash": 1_000},
            {"portfolio_id": "large", "name": "大组合", "initial_cash": 2_000},
        ],
        "portfolio_performance": {
            "small": {
                "2": {
                    "horizon": 2,
                    "status": "COMPLETE",
                    "initial_cash": 1_000,
                    "ending_equity": 1_050,
                    "total_return": 0.05,
                    "max_drawdown": 0.0,
                    "liquidation_status": "LIQUIDATED",
                    "open_positions": {},
                    "execution_events": [
                        {
                            "action": "DEFER_SELL",
                            "date": "2025-01-03",
                            "symbol": "000001",
                            "event_id": "trigger-1",
                            "reason": "before_trading_resumes",
                        },
                        {
                            "action": "SELL",
                            "date": "2025-01-06",
                            "symbol": "000001",
                            "shares": 100,
                            "price": 10.0,
                            "commission": 0.2,
                            "net_proceeds": 999.8,
                            "event_id": "trigger-1",
                            "reason": "termination_decision",
                        },
                        {
                            "action": "SELECT",
                            "date": "2025-01-06",
                            "symbol": "000003",
                            "budget": 999.8,
                            "rank_cutoff": "2025-01-06",
                            "event_id": "trigger-1",
                        },
                        {
                            "action": "CASH",
                            "date": "2025-01-07",
                            "symbol": "000003",
                            "budget": 999.8,
                            "reason": "lot_budget",
                            "event_id": "trigger-1",
                        },
                    ],
                },
                "4": {
                    "horizon": 4,
                    "status": "INSUFFICIENT_FORWARD_DATA",
                    "initial_cash": 1_000,
                    "execution_events": None,
                },
            },
            "large": {
                "2": {
                    "horizon": 2,
                    "status": "COMPLETE",
                    "initial_cash": 2_000,
                    "ending_equity": 2_000,
                    "total_return": 0.0,
                    "max_drawdown": 0.0,
                    "liquidation_status": "LIQUIDATED",
                    "open_positions": {},
                }
            },
        },
    }


def _portfolio_frame(portfolio_id: str = "small") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "portfolio_id": portfolio_id,
                "portfolio_name": "小组合 <safe>",
                "symbol": "000001",
                "name": "甲公司",
                "entry_date": "2025-01-02",
                "entry_price": 10.01,
                "shares": 100,
                "target_weight": 1.0,
            }
        ]
    )


def _nav_frame(portfolio_id: str = "small") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"portfolio_id": portfolio_id, "date": "2025-01-02", "horizon": 2, "equity": 1_010.0, "daily_return": 0.01, "drawdown": 0.0},
            {"portfolio_id": portfolio_id, "date": "2025-01-03", "horizon": 2, "equity": None, "daily_return": None, "drawdown": None, "stale_symbols": "000001", "max_valuation_stale_days": 2},
            {"portfolio_id": portfolio_id, "date": "2025-01-06", "horizon": 2, "equity": 0.0, "daily_return": -1.0, "drawdown": -1.0},
        ]
    )


def test_projection_keeps_frozen_values_and_groups_only_explicit_trigger():
    projection = build_review_projection(
        _manifest(),
        portfolio_id="small",
        portfolio_frame=_portfolio_frame(),
        nav_frame=_nav_frame(),
    )

    assert projection["schema_version"] == 1
    assert projection["portfolio_id"] == "small"
    assert projection["name"] == "小组合 <safe>"
    assert projection["horizons"] == [2, 4]
    horizon = projection["by_horizon"]["2"]
    assert horizon["nav"][0]["unit_nav"] == 1.01
    assert horizon["nav"][0]["cumulative_return"] == pytest.approx(0.01)
    assert horizon["nav"][1]["equity"] is None
    assert horizon["nav"][1]["unit_nav"] is None
    assert horizon["nav"][2]["equity"] == 0.0
    assert horizon["nav"][2]["unit_nav"] == 0.0
    assert horizon["nav"][1]["stale_symbols"] == "000001"

    events = horizon["events"]
    assert len([event for event in events if event["filled"]]) == 2
    assert not any(event["action"] == "BUY" and event["symbol"] == "000003" for event in events)
    chain_events = [event for event in events if event["chain_id"]]
    assert len(chain_events) == 4
    assert len({event["chain_id"] for event in chain_events}) == 1
    defer = next(event for event in chain_events if event["action"] == "DEFER_SELL")
    sell = next(event for event in chain_events if event["action"] == "SELL")
    assert defer["reason_text"] == "公告明确复牌前等待"
    assert sell["net_cash"] == 999.8
    assert sell["price_basis"] is None
    assert horizon["chains"][0]["chain_id"] == defer["chain_id"]
    assert "保留现金" in horizon["chains"][0]["summary"]
    assert any(event["source_kind"] == "derived_frozen_entry" for event in events)
    assert any(event["action"] == "INITIAL_NOT_FILLED" and not event["filled"] for event in events)
    assert horizon["initial_holdings"][0]["shares"] == 100
    assert horizon["ending_holdings"] == []


def test_projection_keeps_portfolios_and_horizons_isolated():
    projection = build_review_projection(
        _manifest(),
        portfolio_id="large",
        portfolio_frame=pd.DataFrame(
            [{"portfolio_id": "large", "symbol": "000009", "name": "乙公司", "entry_price": 20.0, "shares": 100}]
        ),
        nav_frame=pd.DataFrame(
            [{"portfolio_id": "large", "date": "2025-01-02", "horizon": 2, "equity": 2_000.0, "daily_return": 0.0, "drawdown": 0.0}]
        ),
    )
    assert projection["initial_cash"] == 2_000
    assert projection["by_horizon"]["2"]["summary"]["total_return"] == 0.0
    assert projection["by_horizon"]["2"]["events"]
    assert all(event["source_kind"] == "derived_frozen_entry" for event in projection["by_horizon"]["4"]["events"])


def test_portfolio_endpoint_does_not_need_market_database(tmp_path: Path):
    run = ReviewRun(
        run_dir=tmp_path,
        manifest=_manifest(),
        candidates_frame=pd.DataFrame({"symbol": pd.Series(["000001"], dtype="string"), "selected": [True]}),
        portfolio_frame=_portfolio_frame(),
        nav_frame=_nav_frame(),
        portfolio_returns_frame=pd.DataFrame(),
    )
    state = ReviewState(run, tmp_path / "missing.duckdb")
    server = create_review_server(state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://{server.server_address[0]}:{server.server_address[1]}/api/portfolio?portfolio_id=small") as response:
            payload = json.loads(response.read())
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert payload["review"]["by_horizon"]["2"]["nav"]


def test_projection_preserves_planned_status_range_and_independent_partial_periods():
    manifest = _manifest()
    manifest["status"] = "PLANNED"
    manifest["portfolio_performance"]["small"]["2"]["status"] = "PLANNED"
    projection = build_review_projection(
        manifest,
        portfolio_id="small",
        portfolio_frame=_portfolio_frame(),
        nav_frame=_nav_frame(),
    )

    horizon = projection["by_horizon"]["2"]
    assert horizon["status"]["code"] == "PLANNED"
    assert horizon["status"]["label"] == "待运行"
    assert projection["scope"]["actual_date_range"] == ["2025-01-02", "2025-01-06"]
    assert horizon["capabilities"]["aggregation"]["weekly"]["last_period_may_be_partial"] is True
    assert horizon["capabilities"]["aggregation"]["monthly"]["last_period_may_be_partial"] is True

    complete_manifest = _manifest()
    complete_manifest["spec"]["wizard_metadata"]["research_sessions"] = ["2025-01-30", "2025-01-31"]
    complete_nav = pd.DataFrame(
        [
            {"portfolio_id": "small", "date": "2025-01-30", "horizon": 2, "equity": 1_000.0},
            {"portfolio_id": "small", "date": "2025-01-31", "horizon": 2, "equity": 1_000.0},
        ]
    )
    complete = build_review_projection(
        complete_manifest,
        portfolio_id="small",
        portfolio_frame=_portfolio_frame(),
        nav_frame=complete_nav,
    )["by_horizon"]["2"]["capabilities"]["aggregation"]
    assert complete["weekly"]["last_period_may_be_partial"] is False
    assert complete["monthly"]["last_period_may_be_partial"] is False


def _write_workbench_run(tmp_path: Path, db_path: Path) -> Path:
    run_dir = tmp_path / "runs" / "run-projection"
    run_dir.mkdir(parents=True)
    manifest = _manifest()
    manifest["diagnostics"]["data_source"] = {"db_path": str(db_path)}
    candidates = pd.DataFrame(
        [{"symbol": "000001", "name": "甲公司", "selected": True, "eligible": True}]
    )
    portfolio = _portfolio_frame()
    nav = _nav_frame()
    portfolios = portfolio.copy()
    portfolio_nav = nav.copy()
    returns = pd.DataFrame(columns=["run_id", "portfolio_id", "horizon", "symbol", "return", "contribution", "winning"])
    frames = {
        "candidates.csv": candidates,
        "portfolio.csv": portfolio,
        "nav.csv": nav,
        "portfolio_returns.csv": returns,
        "portfolios.csv": portfolios,
        "portfolio_nav.csv": portfolio_nav,
    }
    for filename, frame in frames.items():
        frame.to_csv(run_dir / filename, index=False)
    manifest["artifacts"] = [*frames, "manifest.json"]
    manifest["artifact_hashes"] = {
        filename: hashlib.sha256((run_dir / filename).read_bytes()).hexdigest()
        for filename in frames
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return run_dir


class _ReviewFlow:
    def __init__(self, runs_dir: Path):
        self.runs_dir = runs_dir


def _workbench_json(server, path: str) -> dict:
    with urlopen(f"http://{server.server_address[0]}:{server.server_address[1]}{path}") as response:
        return json.loads(response.read())


def test_workbench_static_review_asset_does_not_load_run(tmp_path: Path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    server = create_workbench_server(_ReviewFlow(runs_dir))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(
            f"http://{server.server_address[0]}:{server.server_address[1]}"
            "/research/review/missing-run/app.js"
        ) as response:
            body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
    assert "LightweightCharts" in body


def test_workbench_frozen_routes_skip_db_and_preserve_artifacts(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "configured.duckdb"
    db_path.touch()
    run_dir = _write_workbench_run(tmp_path, db_path)
    before = {
        filename: hashlib.sha256((run_dir / filename).read_bytes()).hexdigest()
        for filename in ["manifest.json", "nav.csv"]
    }
    calls: list[str] = []

    class SpyAdapter:
        def __init__(self, *args, **kwargs):
            calls.append("init")

        def load(self, *args, **kwargs):
            calls.append("load")
            raise AssertionError("frozen summary/portfolio must not access market DB")

    import alphalab.research.review as review_module

    monkeypatch.setattr(review_module, "DuckDBMarketDataAdapter", SpyAdapter)
    server = create_workbench_server(_ReviewFlow(run_dir.parent))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        summary = _workbench_json(server, "/research/review/run-projection/api/summary")
        portfolio = _workbench_json(server, "/research/review/run-projection/api/portfolio")
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
    after = {
        filename: hashlib.sha256((run_dir / filename).read_bytes()).hexdigest()
        for filename in ["manifest.json", "nav.csv"]
    }
    assert summary["run_id"] == "run-projection"
    assert portfolio["review"]["schema_version"] == 1
    assert calls == []
    assert after == before


def test_workbench_stock_route_rejects_missing_db(tmp_path: Path):
    run_dir = _write_workbench_run(tmp_path, tmp_path / "missing.duckdb")
    server = create_workbench_server(_ReviewFlow(run_dir.parent))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(HTTPError) as error:
            urlopen(
                f"http://{server.server_address[0]}:{server.server_address[1]}"
                "/research/review/run-projection/api/stock?symbol=000001"
            )
        assert error.value.code == 400
        payload = json.loads(error.value.read())
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
    assert "行情数据库不存在" in payload["error"]
