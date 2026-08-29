"""历史研究冻结产物审阅页的 HTTP 行为。"""

from __future__ import annotations

import json
import threading
from datetime import date
from urllib.parse import urlencode
from urllib.request import urlopen

import duckdb
import pandas as pd
import pytest

from alphalab.research import HistoricalResearchLab, InMemoryMarketDataAdapter, ResearchSpec
from alphalab.research.review import ReviewState, create_review_server, load_review_run


def _bars() -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=180)
    rows: list[dict] = []
    for symbol_index, symbol in enumerate(["002104", "300468"]):
        for day_index, current_date in enumerate(dates):
            close = 10.0 + day_index * (0.03 + symbol_index * 0.01)
            rows.append(
                {
                    "market": "a_share",
                    "symbol": symbol,
                    "timeframe": "1d",
                    "ts": current_date,
                    "trade_date": current_date.date(),
                    "open": close - 0.05,
                    "high": close + 0.1,
                    "low": close - 0.1,
                    "close": close,
                    "volume": 2_000_000,
                    "amount": 80_000_000,
                    "adjusted": True,
                    "adjustment": "qfq",
                }
            )
    return pd.DataFrame(rows)


def _state(tmp_path) -> ReviewState:
    bars = _bars()
    run_dir = tmp_path / "runs"
    report = HistoricalResearchLab(InMemoryMarketDataAdapter(bars), runs_dir=run_dir).run(
        ResearchSpec(requested_date="2025-06-30", top_n=2, horizons=(3, 5))
    )
    db_path = tmp_path / "market.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(
        """
        CREATE TABLE market_ohlcv (
            market VARCHAR, symbol VARCHAR, timeframe VARCHAR, ts TIMESTAMP,
            trade_date DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            volume DOUBLE, amount DOUBLE, adjusted BOOLEAN, adjustment VARCHAR
        )
        """
    )
    con.register("bars", bars)
    con.execute("INSERT INTO market_ohlcv SELECT * FROM bars")
    con.close()
    return ReviewState(load_review_run(run_dir, report.run_id), db_path)


def _get_json(server, path: str) -> dict:
    with urlopen(f"http://{server.server_address[0]}:{server.server_address[1]}{path}") as response:
        return json.loads(response.read())


def test_review_api_hides_future_bars_until_explicit_evaluation(tmp_path):
    state = _state(tmp_path)
    server = create_review_server(state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        selection = _get_json(server, "/api/stock?" + urlencode({"symbol": "300468", "mode": "selection"}))
        evaluation = _get_json(server, "/api/stock?" + urlencode({"symbol": "300468", "mode": "evaluation"}))
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    signal_date = date.fromisoformat(selection["signal_date"])
    selection_dates = [date.fromisoformat(row["date"]) for row in selection["rows"]]
    evaluation_dates = [date.fromisoformat(row["date"]) for row in evaluation["rows"]]
    assert selection["mode"] == "selection"
    assert selection_dates and max(selection_dates) <= signal_date
    assert not any(current_date > signal_date for current_date in selection_dates)
    assert selection["portfolio"] is None
    assert selection["performance"] == {}
    assert evaluation["mode"] == "evaluation"
    assert any(current_date > signal_date for current_date in evaluation_dates)
    assert evaluation["markers"]["signal_date"] == selection["signal_date"]
    assert evaluation["markers"]["entry_date"]
    assert evaluation["performance"]["3"]["stock_return"] is not None
    assert evaluation["portfolio_performance"]["3"]["total_return"] is not None


def test_review_portfolio_payload_exposes_nav_and_holdings(tmp_path):
    state = _state(tmp_path)

    payload = state.portfolio_detail()

    assert payload["status"] == "OK"
    assert payload["horizons"] == [3, 5]
    assert payload["performance"]["3"]["status"] == "COMPLETE"
    assert payload["performance"]["3"]["total_return"] is not None
    assert payload["holdings"]
    assert {row["symbol"] for row in payload["holdings"]} == {"002104", "300468"}
    assert payload["nav"]
    assert {row["horizon"] for row in payload["nav"]} == {3, 5}
    assert payload["benchmark_nav"]
    assert payload["benchmark_performance"]["3"]["status"] == "COMPLETE"
    assert "total_return_delta" in payload["comparison"]["3"]
    assert {"date", "equity", "drawdown"}.issubset(payload["nav"][0])


def test_review_portfolio_endpoint_is_read_only(tmp_path):
    state = _state(tmp_path)
    server = create_review_server(state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload = _get_json(server, "/api/portfolio")
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert payload["status"] == "OK"
    assert payload["nav"]
    assert payload["holdings"]


def test_review_candidates_filter_without_changing_frozen_rank(tmp_path):
    state = _state(tmp_path)
    all_rows = state.candidates()
    selected_rows = state.candidates(status="selected")
    searched_rows = state.candidates(search="300468")
    reasons = state.reasons()
    reason_rows = state.candidates(reason=reasons[0])

    assert [row["rank"] for row in all_rows] == sorted(
        [row["rank"] for row in all_rows if row["rank"] is not None]
    ) + [None] * sum(row["rank"] is None for row in all_rows)
    assert len(selected_rows) == 2
    assert all(row["target_weight"] == pytest.approx(0.5) for row in selected_rows)
    assert {row["symbol"] for row in searched_rows} == {"300468"}
    assert reasons
    assert reason_rows
    assert all(reasons[0] in row["reason"] for row in reason_rows)


def test_review_page_and_summary_are_read_only_and_explain_run(tmp_path):
    state = _state(tmp_path)
    summary = state.summary()
    assert summary["rule_version"] == "fixed_v0"
    assert summary["signal_date"] == "2025-06-30"
    assert summary["universe_mode"] == "observed-history"
    assert summary["selected_count"] == 2
    assert "performance" not in summary

    server = create_review_server(state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://{server.server_address[0]}:{server.server_address[1]}/") as response:
            body = response.read().decode("utf-8")
        assert "选股审阅" in body
        assert "事后评估" in body
        assert "LightweightCharts" in body
        assert "class=\"app-shell\"" in body
        assert "class=\"sidebar\"" in body
        assert "class=\"workbench-nav\"" in body
        assert "class=\"global-control-strip\"" in body
        assert "股票池口径" in body
        assert "class=\"chart-layout\"" in body
        assert "组合净值" in body
        assert "组合回撤" in body
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_missing_or_incomplete_run_is_rejected(tmp_path):
    bad_run = tmp_path / "runs" / "bad"
    bad_run.mkdir(parents=True)
    (bad_run / "manifest.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="产物不完整"):
        load_review_run(tmp_path / "runs", "bad")
