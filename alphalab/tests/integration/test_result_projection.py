"""Frozen portfolio result projection contract tests."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.request import urlopen

import pandas as pd
import pytest

from alphalab.research.review import ReviewRun, ReviewState, create_review_server
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
