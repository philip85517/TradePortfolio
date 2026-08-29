"""固定因子历史截面研究的端到端行为。"""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from alphalab.research import (
    DuckDBMarketDataAdapter,
    HistoricalResearchLab,
    InMemoryMarketDataAdapter,
    PortfolioSpec,
    ResearchSpec,
)
from alphalab.research.runs import ResearchRunStore
from alphalab.research.universe_history import upsert_universe_history


def _bars() -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=190)
    rows: list[dict] = []
    for symbol_index in range(12):
        symbol = f"STK{symbol_index:03d}"
        for day_index, current_date in enumerate(dates):
            close = 10.0 + day_index * (0.02 + symbol_index * 0.003)
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


def test_fixed_factor_builds_top10_portfolio_and_forward_metrics(tmp_path):
    adapter = InMemoryMarketDataAdapter(_bars())
    lab = HistoricalResearchLab(adapter, runs_dir=tmp_path)

    report = lab.run(ResearchSpec(requested_date="2025-07-01"))

    assert report.signal_date == date(2025, 7, 1)
    assert report.portfolio["symbol"].tolist() == [
        f"STK{i:03d}" for i in range(11, 1, -1)
    ]
    assert report.portfolio["target_weight"].tolist() == [0.1] * 10
    assert report.candidate_table["selected"].sum() == 10
    assert report.performance[21].status == "COMPLETE"
    assert report.performance[42].status == "COMPLETE"
    assert report.performance[21].total_return > 0
    assert report.performance[21].gross_return > report.performance[21].total_return
    assert report.performance[42].total_return > report.performance[21].total_return
    assert report.performance[21].holding_win_rate == 1.0
    assert len(report.performance[21].stock_returns) == 10
    assert len(report.performance[21].stock_contributions) == 10
    assert sum(report.performance[21].stock_contributions.values()) == pytest.approx(
        report.performance[21].total_return
    )
    assert report.run_id
    assert report.artifact_dir.exists()
    manifest = json.loads((report.artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "COMPLETE"
    assert manifest["source_hash"]
    assert manifest["spec_hash"]
    assert manifest["config_hash"]
    assert manifest["spec_hash"] != manifest["config_hash"]
    assert manifest["artifact_content_hash"]
    assert set(manifest["artifact_hashes"]) == set(manifest["artifacts"]) - {"manifest.json"}
    assert manifest["performance"]["21"]["stock_returns"]
    assert (report.artifact_dir / "portfolio_returns.csv").exists()
    assert {"run_id", "portfolio_id"}.issubset(pd.read_csv(report.artifact_dir / "portfolio_returns.csv").columns)
    assert {"run_id", "portfolio_id"}.issubset(pd.read_csv(report.artifact_dir / "nav.csv").columns)


def test_fixed_factor_plugin_version_is_recorded_and_unknown_version_is_rejected(tmp_path):
    adapter = InMemoryMarketDataAdapter(_bars())
    report = HistoricalResearchLab(adapter, runs_dir=tmp_path / "fixed").run(
        ResearchSpec(requested_date="2025-07-01", rule_version="fixed_v0")
    )
    manifest = json.loads((report.artifact_dir / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["rule_version"] == "fixed_v0"
    assert manifest["rule_source_hash"]
    with pytest.raises(ValueError, match="未知因子插件"):
        HistoricalResearchLab(adapter, runs_dir=tmp_path / "unknown").run(
            ResearchSpec(requested_date="2025-07-01", rule_version="does_not_exist")
        )


class _CustomFactor:
    plugin_id = "momentum_v1"
    version = "1.0.0"
    role = "filter_and_score"
    supported_markets = ("a_share",)
    required_fields = ("close",)
    min_history_days = 10
    score_direction = "higher_is_better"
    parameter_schema = {"lookback": {"type": "integer", "minimum": 1}}

    def score(self, before):
        symbols = sorted(before["symbol"].astype(str).unique())
        return pd.DataFrame(
            {
                "symbol": symbols,
                "eligible": True,
                "total_score": list(range(len(symbols))),
                "reason": "通过自定义因子",
            }
        ), {"universe": len(symbols), "rule_eligible": len(symbols)}

    def source_hash(self):
        return "custom-factor-source-hash"


def test_custom_factor_contract_is_recorded_and_validated(tmp_path):
    report = HistoricalResearchLab(
        InMemoryMarketDataAdapter(_bars()),
        runs_dir=tmp_path,
        plugins={"momentum_v1": _CustomFactor()},
    ).run(ResearchSpec(requested_date="2025-07-01", rule_version="momentum_v1", top_n=3, horizons=(3,)))

    assert report.portfolio["symbol"].tolist() == ["STK011", "STK010", "STK009"]
    manifest = json.loads((report.artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["factor"] == {
        "plugin_id": "momentum_v1",
        "version": "1.0.0",
        "role": "filter_and_score",
        "supported_markets": ["a_share"],
        "required_fields": ["close"],
        "min_history_days": 10,
        "score_direction": "higher_is_better",
        "parameter_schema": {"lookback": {"type": "integer", "minimum": 1}},
    }


class _InvalidFactor(_CustomFactor):
    plugin_id = "invalid_v1"

    def score(self, before):
        result, funnel = super().score(before)
        result.loc[0, "symbol"] = "OUTSIDE"
        return result, funnel


def test_custom_factor_output_outside_universe_fails(tmp_path):
    with pytest.raises(ValueError, match="universe 外股票"):
        HistoricalResearchLab(
            InMemoryMarketDataAdapter(_bars()),
            runs_dir=tmp_path,
            plugins={"invalid_v1": _InvalidFactor()},
        ).run(ResearchSpec(requested_date="2025-07-01", rule_version="invalid_v1"))


class _ParamFactor(_CustomFactor):
    plugin_id = "param_v1"

    def score_with_params(self, before, params):
        assert params == {"lookback": 20}
        return self.score(before)


def test_factor_params_are_validated_consumed_and_recorded(tmp_path):
    report = HistoricalResearchLab(
        InMemoryMarketDataAdapter(_bars()),
        runs_dir=tmp_path,
        plugins={"param_v1": _ParamFactor()},
    ).run(
        ResearchSpec(
            requested_date="2025-07-01",
            rule_version="param_v1",
            top_n=3,
            horizons=(3,),
            factor_params={"lookback": 20},
        )
    )

    manifest = json.loads((report.artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["spec"]["factor_params"] == {"lookback": 20}


def test_factor_params_reject_unknown_values(tmp_path):
    with pytest.raises(ValueError, match="未声明参数"):
        HistoricalResearchLab(
            InMemoryMarketDataAdapter(_bars()),
            runs_dir=tmp_path,
            plugins={"param_v1": _ParamFactor()},
        ).run(
            ResearchSpec(
                requested_date="2025-07-01",
                rule_version="param_v1",
                factor_params={"unknown": 1},
            )
        )


def test_multiple_portfolios_keep_independent_cash_and_metrics(tmp_path):
    report = HistoricalResearchLab(InMemoryMarketDataAdapter(_bars()), runs_dir=tmp_path).run(
        ResearchSpec(
            requested_date="2025-07-01",
            top_n=5,
            horizons=(3,),
            portfolios=(
                PortfolioSpec("small", name="小资金组合", initial_cash=50_000.0),
                PortfolioSpec("large", name="大资金组合", initial_cash=200_000.0),
            ),
        )
    )

    assert set(report.portfolios) == {"small", "large"}
    assert report.portfolio["portfolio_id"].eq("small").all()
    small = report.portfolio_performance["small"][3]
    large = report.portfolio_performance["large"][3]
    assert small.initial_cash == pytest.approx(50_000.0)
    assert large.initial_cash == pytest.approx(200_000.0)
    assert small.profit_loss is not None and large.profit_loss is not None
    assert large.profit_loss > small.profit_loss
    assert small.cash_residual is not None and large.cash_residual is not None
    assert small.cash_residual >= 0 and large.cash_residual >= 0
    assert (report.portfolios["small"]["shares"] % 100 == 0).all()
    assert (report.portfolios["large"]["shares"] % 100 == 0).all()

    manifest = json.loads((report.artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    assert [item["portfolio_id"] for item in manifest["portfolios"]] == ["small", "large"]
    assert manifest["portfolio_performance"]["small"]["3"]["profit_loss"] == pytest.approx(small.profit_loss)
    metrics = pd.read_csv(report.artifact_dir / "portfolio_metrics.csv")
    assert set(metrics["portfolio_id"]) == {"small", "large"}
    assert {"run_id", "portfolio_id", "initial_cash", "ending_equity", "profit_loss", "max_drawdown_amount", "sharpe"}.issubset(metrics.columns)
    portfolio_nav = pd.read_csv(report.artifact_dir / "portfolio_nav.csv")
    assert {"run_id", "portfolio_id", "date", "equity", "drawdown"}.issubset(portfolio_nav.columns)
    store = ResearchRunStore(tmp_path)
    listed = store.list()[0]
    assert {item["portfolio_id"] for item in listed["portfolios"]} == {"small", "large"}
    comparison = store.compare(report.run_id, report.run_id)
    assert set(comparison["portfolios"]) == {"small", "large"}
    assert comparison["portfolios"]["large"]["3"]["profit_loss_delta"] == pytest.approx(0.0)

    second = HistoricalResearchLab(InMemoryMarketDataAdapter(_bars()), runs_dir=tmp_path / "study").run_study(
        [
            ResearchSpec(
                requested_date="2025-07-01",
                top_n=5,
                horizons=(3,),
                portfolios=(
                    PortfolioSpec("small", initial_cash=50_000.0),
                    PortfolioSpec("large", initial_cash=200_000.0),
                ),
            ),
            ResearchSpec(
                requested_date="2025-07-15",
                top_n=5,
                horizons=(3,),
                portfolios=(
                    PortfolioSpec("small", initial_cash=50_000.0),
                    PortfolioSpec("large", initial_cash=200_000.0),
                ),
            ),
        ]
    )
    assert set(second.portfolio_summary["portfolio_id"]) == {"small", "large"}
    assert (second.artifact_dir / "portfolio_summary.csv").exists()


def test_portfolio_weighting_supports_score_weights_and_single_stock_cap(tmp_path):
    report = HistoricalResearchLab(InMemoryMarketDataAdapter(_bars()), runs_dir=tmp_path).run(
        ResearchSpec(requested_date="2025-07-01", top_n=5, horizons=(3,), portfolio_weighting="score", max_single_weight=0.4)
    )

    weights = report.portfolio["target_weight"].tolist()
    assert len(weights) == 5
    assert sum(weights) == pytest.approx(1.0)
    assert max(weights) <= 0.4 + 1e-9
    assert len(set(round(weight, 8) for weight in weights)) > 1


def test_portfolio_industry_cap_and_minimum_holdings_are_explicit(tmp_path):
    data = _bars()
    data["industry_level1"] = data["symbol"].map({"STK%03d" % index: ("行业A" if index % 2 == 0 else "行业B") for index in range(12)})
    report = HistoricalResearchLab(InMemoryMarketDataAdapter(data), runs_dir=tmp_path / "capped").run(
        ResearchSpec(requested_date="2025-07-01", top_n=6, horizons=(3,), max_industry_weight=0.5)
    )

    industry_weights = report.portfolio.groupby("industry")["target_weight"].sum().to_dict()
    assert industry_weights
    assert max(industry_weights.values()) <= 0.5 + 1e-9
    assert report.diagnostics["portfolio_status"] == "OK"

    insufficient = HistoricalResearchLab(InMemoryMarketDataAdapter(data), runs_dir=tmp_path / "minimum").run(
        ResearchSpec(requested_date="2025-07-01", top_n=2, horizons=(3,), min_holdings=3)
    )
    assert insufficient.portfolio.empty
    assert insufficient.diagnostics["portfolio_status"] == "INSUFFICIENT_HOLDINGS"


def test_run_includes_same_universe_equal_weight_benchmark(tmp_path):
    report = HistoricalResearchLab(InMemoryMarketDataAdapter(_bars()), runs_dir=tmp_path).run(
        ResearchSpec(requested_date="2025-07-01", top_n=5, horizons=(3,))
    )

    assert report.benchmark_performance[3].status == "COMPLETE"
    assert report.benchmark_performance[3].total_return is not None
    assert not report.benchmark_nav.empty
    manifest = json.loads((report.artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["benchmark"]["3"]["total_return"] is not None
    assert (report.artifact_dir / "benchmark_nav.csv").exists()


def test_point_in_time_universe_uses_listing_windows_and_records_quality(tmp_path):
    data = _bars()
    data["listed_date"] = data["symbol"].map(
        {"STK010": "2025-07-02", "STK011": "2025-01-02", **{f"STK{i:03d}": "2025-01-02" for i in range(10)}}
    )
    data["delisted_date"] = pd.NaT
    report = HistoricalResearchLab(InMemoryMarketDataAdapter(data), runs_dir=tmp_path / "pit").run(
        ResearchSpec(requested_date="2025-07-01", universe_mode="point-in-time")
    )

    assert report.diagnostics["universe_mode"] == "point-in-time"
    assert report.diagnostics["universe"]["excluded_after_as_of"] == 1
    assert "STK010" not in set(report.candidate_table["symbol"])


def test_point_in_time_universe_requires_effective_listing_fields(tmp_path):
    with pytest.raises(ValueError, match="point-in-time universe 需要"):
        HistoricalResearchLab(InMemoryMarketDataAdapter(_bars()), runs_dir=tmp_path).run(
            ResearchSpec(requested_date="2025-07-01", universe_mode="point-in-time")
        )


def test_strict_point_in_time_fails_closed_without_historical_industry(tmp_path):
    data = _bars()
    data["listed_date"] = pd.Timestamp("2025-01-02")
    data["delisted_date"] = pd.NaT

    with pytest.raises(ValueError, match="严格模式要求完整 point-in-time universe"):
        HistoricalResearchLab(InMemoryMarketDataAdapter(data), runs_dir=tmp_path).run(
            ResearchSpec(
                requested_date="2025-07-01",
                universe_mode="point-in-time",
                data_quality_mode="strict",
            )
        )

    manifest = json.loads(next(tmp_path.glob("*/manifest.json")).read_text(encoding="utf-8"))
    assert manifest["status"] == "FAILED"
    assert manifest["diagnostics"]["universe"]["point_in_time_quality"] == "listing-only"


def test_strict_data_quality_fails_closed_on_duplicate_selection_bars(tmp_path):
    data = pd.concat([_bars(), _bars().iloc[[0]]], ignore_index=True)

    with pytest.raises(ValueError, match="数据质量校验失败.*重复日线"):
        HistoricalResearchLab(InMemoryMarketDataAdapter(data), runs_dir=tmp_path).run(
            ResearchSpec(requested_date="2025-07-01", data_quality_mode="strict")
        )


def test_strict_data_quality_rejects_mixed_adjustment_and_missing_trade_data(tmp_path):
    data = _bars()
    data.loc[data["symbol"].eq("STK000") & (data["ts"] <= pd.Timestamp("2025-07-01")), "adjustment"] = "none"
    data.loc[data["symbol"].eq("STK001") & (data["ts"] <= pd.Timestamp("2025-07-01")), "volume"] = pd.NA
    data.loc[data["symbol"].eq("STK001") & (data["ts"] <= pd.Timestamp("2025-07-01")), "amount"] = pd.NA

    with pytest.raises(ValueError, match="数据质量校验失败.*(未复权行情|缺少成交量和成交额)"):
        HistoricalResearchLab(InMemoryMarketDataAdapter(data), runs_dir=tmp_path).run(
            ResearchSpec(requested_date="2025-07-01", data_quality_mode="strict")
        )


def test_strict_data_quality_failure_writes_queryable_failed_run(tmp_path):
    data = pd.concat([_bars(), _bars().iloc[[0]]], ignore_index=True)

    with pytest.raises(ValueError, match="数据质量校验失败.*重复日线"):
        HistoricalResearchLab(InMemoryMarketDataAdapter(data), runs_dir=tmp_path).run(
            ResearchSpec(requested_date="2025-07-01", data_quality_mode="strict")
        )

    manifests = list(tmp_path.glob("*/manifest.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["status"] == "FAILED"
    assert manifest["diagnostics"]["data_quality"]["status"] == "FAILED"
    assert "重复日线" in manifest["error"]


def test_point_in_time_run_uses_automatic_history_sidecar(tmp_path):
    import duckdb

    db_path = tmp_path / "market.duckdb"
    with duckdb.connect(str(db_path)) as con:
        con.execute(
            """
            CREATE TABLE market_ohlcv (
                market VARCHAR, symbol VARCHAR, timeframe VARCHAR, ts TIMESTAMP,
                trade_date DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
                volume DOUBLE, amount DOUBLE, adjusted BOOLEAN, adjustment VARCHAR
            )
            """
        )
        con.register("bars", _bars())
        con.execute("INSERT INTO market_ohlcv SELECT * FROM bars")
    history = pd.DataFrame(
        [
            {
                "market": "a_share",
                "symbol": f"STK{index:03d}",
                "effective_from": "2025-01-02" if index != 10 else "2025-07-02",
                "effective_to": None,
                "status": "active",
                "industry_level1": "科技",
                "source": "baostock",
                "snapshot_id": "bs-2026-08-29",
            }
            for index in range(12)
        ]
    )
    upsert_universe_history(history, db_path)

    report = HistoricalResearchLab(DuckDBMarketDataAdapter(db_path), runs_dir=tmp_path / "runs").run(
        ResearchSpec(requested_date="2025-07-01", universe_mode="point-in-time")
    )

    assert "STK010" not in set(report.candidate_table["symbol"])
    assert set(report.candidate_table["industry"]) == {"科技"}
    assert report.diagnostics["universe"]["snapshot_id"] == "bs-2026-08-29"
    assert report.diagnostics["universe"]["history_rows"] == 11
    assert report.diagnostics["universe"]["industry_missing_symbols"] == 0


def test_point_in_time_diagnostic_counts_missing_industry_symbols_not_price_rows(tmp_path):
    import duckdb

    db_path = tmp_path / "market.duckdb"
    with duckdb.connect(str(db_path)) as con:
        con.execute(
            """
            CREATE TABLE market_ohlcv (
                market VARCHAR, symbol VARCHAR, timeframe VARCHAR, ts TIMESTAMP,
                trade_date DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
                volume DOUBLE, amount DOUBLE, adjusted BOOLEAN, adjustment VARCHAR
            )
            """
        )
        con.register("bars", _bars())
        con.execute("INSERT INTO market_ohlcv SELECT * FROM bars")
    history = pd.DataFrame(
        [
            {
                "market": "a_share",
                "symbol": f"STK{index:03d}",
                "effective_from": "2025-01-02",
                "effective_to": None,
                "status": "active",
                "industry_level1": "科技" if index == 0 else None,
                "source": "baostock",
                "snapshot_id": "bs-2026-08-29",
            }
            for index in range(12)
        ]
    )
    upsert_universe_history(history, db_path)

    report = HistoricalResearchLab(DuckDBMarketDataAdapter(db_path), runs_dir=tmp_path / "runs").run(
        ResearchSpec(requested_date="2025-07-01", universe_mode="point-in-time")
    )

    assert report.diagnostics["universe"]["industry_missing_symbols"] == 11


def test_selection_is_unchanged_when_future_prices_are_extreme(tmp_path):
    original = _bars()
    mutated = original.copy()
    mutated.loc[mutated["ts"] > pd.Timestamp("2025-07-01"), "close"] *= 100
    mutated.loc[mutated["ts"] > pd.Timestamp("2025-07-01"), "open"] *= 100

    spec = ResearchSpec(requested_date="2025-07-01", lot_size=1)
    first = HistoricalResearchLab(InMemoryMarketDataAdapter(original), runs_dir=tmp_path / "first").run(spec)
    second = HistoricalResearchLab(InMemoryMarketDataAdapter(mutated), runs_dir=tmp_path / "second").run(spec)

    assert first.candidate_table[["symbol", "rank", "total_score"]].to_dict("records") == second.candidate_table[
        ["symbol", "rank", "total_score"]
    ].to_dict("records")
    assert first.portfolio["symbol"].tolist() == second.portfolio["symbol"].tolist()


def test_incomplete_forward_window_is_explicit(tmp_path):
    data = _bars()
    data = data[data["ts"] <= pd.Timestamp("2025-08-15")].copy()
    report = HistoricalResearchLab(InMemoryMarketDataAdapter(data), runs_dir=tmp_path).run(
        ResearchSpec(requested_date="2025-07-01")
    )

    assert len(report.portfolio) == 10
    assert report.performance[21].status == "COMPLETE"
    assert report.performance[42].status == "INSUFFICIENT_FORWARD_DATA"
    assert report.performance[42].total_return is None


def test_non_trading_request_date_resolves_to_previous_signal_day(tmp_path):
    report = HistoricalResearchLab(InMemoryMarketDataAdapter(_bars()), runs_dir=tmp_path).run(
        ResearchSpec(requested_date="2025-07-05")
    )

    assert report.requested_date == date(2025, 7, 5)
    assert report.signal_date == date(2025, 7, 4)


def test_repeated_runs_write_distinct_immutable_artifacts(tmp_path):
    lab = HistoricalResearchLab(InMemoryMarketDataAdapter(_bars()), runs_dir=tmp_path)
    spec = ResearchSpec(requested_date="2025-07-01")

    first = lab.run(spec)
    first_manifest = (first.artifact_dir / "manifest.json").read_text(encoding="utf-8")
    second = lab.run(spec)

    assert first.run_id != second.run_id
    assert first.artifact_dir != second.artifact_dir
    assert (first.artifact_dir / "candidates.csv").exists()
    assert (first.artifact_dir / "portfolio.csv").exists()
    assert (first.artifact_dir / "nav.csv").exists()
    assert (first.artifact_dir / "manifest.json").read_text(encoding="utf-8") == first_manifest


def test_research_run_store_lists_and_compares_immutable_runs(tmp_path):
    lab = HistoricalResearchLab(InMemoryMarketDataAdapter(_bars()), runs_dir=tmp_path / "runs")
    first = lab.run(ResearchSpec(requested_date="2025-07-01", top_n=5, horizons=(3,)))
    second = lab.run(ResearchSpec(requested_date="2025-07-01", top_n=10, horizons=(3,)))
    store = ResearchRunStore(tmp_path / "runs")

    listed = store.list()
    comparison = store.compare(first.run_id, second.run_id)

    assert [item["run_id"] for item in listed] == sorted([first.run_id, second.run_id])
    assert comparison["left"]["run_id"] == first.run_id
    assert comparison["right"]["run_id"] == second.run_id
    assert comparison["performance"]["3"]["total_return_delta"] is not None
    assert comparison["portfolio"]["added"]
    with pytest.raises(ValueError, match="运行 ID 无效"):
        store.manifest("../escape")


def test_research_run_store_compares_each_portfolio_history(tmp_path):
    lab = HistoricalResearchLab(InMemoryMarketDataAdapter(_bars()), runs_dir=tmp_path / "runs")
    first = lab.run(
        ResearchSpec(
            requested_date="2025-07-01",
            top_n=2,
            horizons=(3,),
            portfolios=(PortfolioSpec("small", initial_cash=50_000), PortfolioSpec("large", initial_cash=200_000)),
        )
    )
    second = lab.run(
        ResearchSpec(
            requested_date="2025-07-01",
            top_n=1,
            horizons=(3,),
            portfolios=(PortfolioSpec("small", initial_cash=50_000), PortfolioSpec("large", initial_cash=200_000)),
        )
    )

    comparison = ResearchRunStore(tmp_path / "runs").compare(first.run_id, second.run_id)

    assert set(comparison["portfolios"]) == {"small", "large"}
    assert set(comparison["portfolio_symbols"]) == {"small", "large"}
    assert comparison["portfolios"]["large"]["3"]["profit_loss_delta"] is not None
    assert comparison["portfolio_symbols"]["large"]["removed"]


def test_research_cli_can_list_show_and_compare_runs(tmp_path, monkeypatch):
    from io import StringIO

    lab = HistoricalResearchLab(InMemoryMarketDataAdapter(_bars()), runs_dir=tmp_path / "runs")
    first = lab.run(ResearchSpec(requested_date="2025-07-01", top_n=5, horizons=(3,)))
    second = lab.run(ResearchSpec(requested_date="2025-07-01", top_n=10, horizons=(3,)))

    from alphalab.paper.cli import main

    output = StringIO()
    monkeypatch.setattr("sys.stdout", output)
    assert main(["research", "list", "--runs-dir", str(tmp_path / "runs")]) == 0
    assert main(["research", "show", "--run-id", first.run_id, "--runs-dir", str(tmp_path / "runs")]) == 0
    assert main(
        [
            "research",
            "compare",
            "--left",
            first.run_id,
            "--right",
            second.run_id,
            "--runs-dir",
            str(tmp_path / "runs"),
        ]
    ) == 0
    text = output.getvalue()
    assert first.run_id in text
    assert second.run_id in text
    assert "收益差" in text


def test_research_review_auto_binds_recorded_run_data_source(tmp_path, monkeypatch):
    import duckdb

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
    con.register("bars", _bars())
    con.execute("INSERT INTO market_ohlcv SELECT * FROM bars")
    con.close()

    from alphalab.paper import cli

    binding = cli.auto_bind_research_db(db_path=db_path, market="a_share")
    lab = HistoricalResearchLab(InMemoryMarketDataAdapter(_bars()), runs_dir=tmp_path / "runs", data_binding=binding)
    report = lab.run(ResearchSpec(requested_date="2025-07-01", horizons=(3,)))
    captured: dict[str, object] = {}

    def fake_serve_review(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(cli, "serve_review", fake_serve_review)
    assert cli.main(
        [
            "research",
            "review",
            "--run-id",
            report.run_id,
            "--runs-dir",
            str(tmp_path / "runs"),
            "--db",
            "auto",
        ]
    ) == 0

    assert captured["db_path"] == db_path


def test_research_study_aggregates_multiple_dates_without_claiming_significance(tmp_path):
    lab = HistoricalResearchLab(InMemoryMarketDataAdapter(_bars()), runs_dir=tmp_path / "runs")
    study = lab.run_study(
        [
            ResearchSpec(requested_date="2025-07-01", top_n=5, horizons=(3,)),
            ResearchSpec(requested_date="2025-07-15", top_n=5, horizons=(3,)),
            ResearchSpec(requested_date="2025-08-01", top_n=5, horizons=(3,)),
        ]
    )

    assert len(study.reports) == 3
    assert study.summary.loc[study.summary["horizon"] == 3, "sample_count"].iloc[0] == 3
    assert study.summary.loc[study.summary["horizon"] == 3, "evidence_label"].iloc[0] == "DESCRIPTIVE_ONLY"
    assert study.summary.loc[study.summary["horizon"] == 3, "ci95_low"].notna().iloc[0]
    assert study.summary.loc[study.summary["horizon"] == 3, "excess_ci95_low"].notna().iloc[0]
    assert (study.artifact_dir / "summary.csv").exists()
    assert (study.artifact_dir / "manifest.json").exists()


def test_research_cli_runs_study_from_multiple_dates(tmp_path, monkeypatch):
    import duckdb
    from io import StringIO

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
    con.register("bars", _bars())
    con.execute("INSERT INTO market_ohlcv SELECT * FROM bars")
    con.close()

    from alphalab.paper.cli import main

    output = StringIO()
    monkeypatch.setattr("sys.stdout", output)
    runs_dir = tmp_path / "runs"
    assert main(
        [
            "research",
            "study",
            "--as-of",
            "2025-07-01,2025-07-15,2025-08-01",
            "--horizons",
            "3",
            "--db",
            str(db_path),
            "--runs-dir",
            str(runs_dir),
        ]
    ) == 0
    assert "DESCRIPTIVE_ONLY" in output.getvalue()
    assert list((runs_dir / "studies").glob("*/summary.csv"))


def test_low_liquidity_and_invalid_ohlc_are_excluded_with_reasons(tmp_path):
    data = _bars()
    data.loc[data["symbol"] == "STK011", "amount"] = 1.0
    bad = (data["symbol"] == "STK010") & (data["ts"] == pd.Timestamp("2025-07-01"))
    data.loc[bad, "high"] = data.loc[bad, "close"] - 1.0

    report = HistoricalResearchLab(InMemoryMarketDataAdapter(data), runs_dir=tmp_path).run(
        ResearchSpec(requested_date="2025-07-01")
    )

    rows = report.candidate_table.set_index("symbol")
    assert not bool(rows.loc["STK011", "eligible"])
    assert "成交额" in rows.loc["STK011", "reason"]
    assert not bool(rows.loc["STK010", "eligible"])
    assert "OHLC" in rows.loc["STK010", "reason"]
    assert "invalid_ohlc_rows" in report.diagnostics["data_quality"]


def test_duckdb_adapter_reads_only_requested_market_and_window(tmp_path):
    import duckdb

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
    con.execute(
        """
        INSERT INTO market_ohlcv VALUES
        ('a_share', 'AAA', '1d', '2025-07-01', '2025-07-01', 1, 1.1, .9, 1.05, 100, 1000000, true, 'qfq'),
        ('us', 'BBB', '1d', '2025-07-01', '2025-07-01', 2, 2.1, 1.9, 2.05, 100, 1000000, true, 'qfq'),
        ('a_share', 'AAA', '1w', '2025-07-01', '2025-07-01', 1, 1.1, .9, 1.05, 100, 1000000, true, 'qfq')
        """
    )
    con.close()

    loaded = DuckDBMarketDataAdapter(db_path).load(date(2025, 7, 1), date(2025, 7, 1))

    assert loaded["symbol"].tolist() == ["AAA"]
    assert loaded["timeframe"].tolist() == ["1d"]


def test_duckdb_adapter_can_push_symbol_filter_down(tmp_path):
    import duckdb

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
    con.execute(
        """
        INSERT INTO market_ohlcv VALUES
        ('a_share', 'AAA', '1d', '2025-07-01', '2025-07-01', 1, 1.1, .9, 1.05, 100, 1000000, true, 'qfq'),
        ('a_share', 'CCC', '1d', '2025-07-01', '2025-07-01', 3, 3.1, 2.9, 3.05, 100, 1000000, true, 'qfq')
        """
    )
    con.close()

    loaded = DuckDBMarketDataAdapter(db_path).load(
        date(2025, 7, 1), date(2025, 7, 1), symbols=["AAA"]
    )

    assert loaded["symbol"].tolist() == ["AAA"]


def test_duckdb_adapter_preserves_optional_point_in_time_universe_fields(tmp_path):
    import duckdb

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
    con.execute(
        """
        CREATE TABLE market_universe (
            market VARCHAR, symbol VARCHAR, name VARCHAR, industry_level1 VARCHAR,
            listed_date DATE, delisted_date DATE
        )
        """
    )
    con.execute(
        """
        INSERT INTO market_ohlcv VALUES
        ('a_share', 'AAA', '1d', '2025-07-01', '2025-07-01', 1, 1.1, .9, 1.05, 100, 1000000, true, 'qfq')
        """
    )
    con.execute(
        """
        INSERT INTO market_universe VALUES
        ('a_share', 'AAA', '示例股份', '行业A', '2020-01-01', NULL)
        """
    )
    con.close()

    loaded = DuckDBMarketDataAdapter(db_path).load(date(2025, 7, 1), date(2025, 7, 1))

    assert loaded.loc[0, "listed_date"] == pd.Timestamp("2020-01-01")
    assert pd.isna(loaded.loc[0, "delisted_date"])
    assert loaded.loc[0, "industry_level1"] == "行业A"


def test_research_cli_runs_fixed_experiment_and_reports_artifact(tmp_path):
    import duckdb

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
    con.register("bars", _bars())
    con.execute("INSERT INTO market_ohlcv SELECT * FROM bars")
    con.close()

    from alphalab.paper.cli import main

    runs_dir = tmp_path / "runs"
    rc = main(
        [
            "research",
            "run",
            "--as-of",
            "2025-07-01",
            "--db",
            str(db_path),
            "--runs-dir",
            str(runs_dir),
        ]
    )

    assert rc == 0
    assert len(list(runs_dir.glob("*/manifest.json"))) == 1


def test_research_cli_accepts_horizons_and_costs(tmp_path):
    import duckdb

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
    con.register("bars", _bars())
    con.execute("INSERT INTO market_ohlcv SELECT * FROM bars")
    con.close()

    from alphalab.paper.cli import main

    runs_dir = tmp_path / "runs"
    rc = main(
        [
            "research",
            "run",
            "--as-of",
            "2025-07-01",
            "--db",
            str(db_path),
            "--runs-dir",
            str(runs_dir),
            "--horizons",
            "3,5",
            "--commission-rate",
            "0.001",
            "--slippage-rate",
            "0.002",
            "--portfolio",
            "small=50000",
            "--portfolio",
            "large=200000",
        ]
    )

    assert rc == 0
    manifest = json.loads(next(runs_dir.glob("*/manifest.json")).read_text(encoding="utf-8"))
    assert manifest["spec"]["horizons"] == [3, 5]
    assert manifest["spec"]["commission_rate"] == pytest.approx(0.001)
    assert manifest["spec"]["slippage_rate"] == pytest.approx(0.002)
    assert [item["portfolio_id"] for item in manifest["portfolios"]] == ["small", "large"]
    assert manifest["portfolio_performance"]["large"]["3"]["profit_loss"] is not None


def test_empty_candidate_pool_is_a_valid_non_investable_result(tmp_path):
    data = _bars()
    data["amount"] = 1.0

    report = HistoricalResearchLab(InMemoryMarketDataAdapter(data), runs_dir=tmp_path).run(
        ResearchSpec(requested_date="2025-07-01")
    )

    assert report.portfolio.empty
    assert report.performance[21].status == "INSUFFICIENT_FORWARD_DATA"
    assert report.candidate_table["eligible"].sum() == 0
