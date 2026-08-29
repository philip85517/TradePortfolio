"""固定 V0 历史截面研究引擎。

研究模块刻意与模拟交易账本分离：它只读取行情、生成冻结的研究产物，
不会创建订单或写入账户状态。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Protocol, Sequence

import numpy as np
import pandas as pd

from ..utils import code_commit, json_hash, parse_date
from .plugins import ResearchFactorPlugin, factor_definition, resolve_plugin, validate_plugin_output


class ResearchDataAdapter(Protocol):
    def load(
        self,
        start_date: date,
        end_date: date,
        market: str = "a_share",
        symbols: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        """加载指定日期区间内的单市场日线。"""


@dataclass(frozen=True)
class ResearchSpec:
    requested_date: str | date
    top_n: int = 10
    horizons: tuple[int, ...] = (21, 42)
    initial_cash: float = 100_000.0
    commission_rate: float = 0.0003
    slippage_rate: float = 0.0005
    market: str = "a_share"
    rule_version: str = "fixed_v0"
    portfolio_weighting: str = "equal"
    max_single_weight: float | None = None
    max_industry_weight: float | None = None
    min_holdings: int = 0
    universe_mode: str = "observed-history"
    lot_size: int = 100
    data_quality_mode: str = "exploratory"
    factor_params: dict[str, Any] = field(default_factory=dict)
    portfolios: tuple["PortfolioSpec", ...] = ()


@dataclass(frozen=True)
class PortfolioSpec:
    """一个实验内独立组合的资金和约束配置。

    ``None`` 表示继承 ``ResearchSpec`` 的兼容默认值；这样旧的单组合
    调用方式仍然有效，而多个组合可以只覆盖自己需要的配置。
    """

    portfolio_id: str
    name: str | None = None
    initial_cash: float | None = None
    portfolio_weighting: str | None = None
    max_single_weight: float | None = None
    max_industry_weight: float | None = None
    min_holdings: int | None = None
    lot_size: int | None = None


@dataclass(frozen=True)
class HorizonPerformance:
    horizon: int
    status: str
    total_return: float | None
    max_drawdown: float | None
    gross_return: float | None
    evaluated_date: date | None
    message: str | None = None
    holding_win_rate: float | None = None
    stock_returns: dict[str, float] | None = None
    stock_contributions: dict[str, float] | None = None
    initial_cash: float | None = None
    ending_equity: float | None = None
    profit_loss: float | None = None
    max_drawdown_amount: float | None = None
    daily_volatility: float | None = None
    annualized_return: float | None = None
    annualized_volatility: float | None = None
    sharpe: float | None = None
    commission_paid: float | None = None
    slippage_paid: float | None = None
    cash_residual: float | None = None


@dataclass
class ResearchReport:
    run_id: str
    requested_date: date
    signal_date: date
    candidate_table: pd.DataFrame
    portfolio: pd.DataFrame
    nav: pd.DataFrame
    performance: dict[int, HorizonPerformance]
    diagnostics: dict[str, Any]
    artifact_dir: Path
    benchmark_performance: dict[int, HorizonPerformance] = field(default_factory=dict)
    benchmark_nav: pd.DataFrame = field(default_factory=pd.DataFrame)
    portfolios: dict[str, pd.DataFrame] = field(default_factory=dict)
    portfolio_performance: dict[str, dict[int, HorizonPerformance]] = field(default_factory=dict)
    portfolio_nav: dict[str, pd.DataFrame] = field(default_factory=dict)
    benchmarks: dict[str, pd.DataFrame] = field(default_factory=dict)
    benchmark_performance_by_portfolio: dict[str, dict[int, HorizonPerformance]] = field(default_factory=dict)
    benchmark_nav_by_portfolio: dict[str, pd.DataFrame] = field(default_factory=dict)


@dataclass
class ResearchStudyReport:
    """多个历史截面的描述性汇总。"""

    study_id: str
    reports: tuple[ResearchReport, ...]
    summary: pd.DataFrame
    diagnostics: dict[str, Any]
    artifact_dir: Path
    portfolio_summary: pd.DataFrame = field(default_factory=pd.DataFrame)


class InMemoryMarketDataAdapter:
    """测试用行情 adapter；生产运行使用 DuckDB adapter。"""

    def __init__(self, bars: pd.DataFrame):
        self.bars = bars.copy()

    def load(
        self,
        start_date: date,
        end_date: date,
        market: str = "a_share",
        symbols: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        data = self.bars.copy()
        if "market" in data.columns:
            data = data[data["market"].astype(str) == market]
        if symbols:
            requested_symbols = {str(symbol) for symbol in symbols}
            data = data[data["symbol"].astype(str).isin(requested_symbols)]
        data = _normalise_dates(data)
        return data[(data["date"] >= pd.Timestamp(start_date)) & (data["date"] <= pd.Timestamp(end_date))]


class DuckDBMarketDataAdapter:
    """生产只读 DuckDB adapter，默认仅读取单市场日线。"""

    def __init__(self, db_path: str | Path, universe_db_path: str | Path | None = None):
        self.db_path = Path(db_path).expanduser()
        self.universe_db_path = Path(universe_db_path).expanduser() if universe_db_path else None

    def load(
        self,
        start_date: date,
        end_date: date,
        market: str = "a_share",
        symbols: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        if not self.db_path.exists():
            raise FileNotFoundError(f"行情数据库不存在: {self.db_path}")
        try:
            import duckdb
        except ImportError as exc:  # pragma: no cover - dependency is installed in the project env.
            raise RuntimeError("读取 DuckDB 需要安装 duckdb") from exc
        con = duckdb.connect(str(self.db_path), read_only=True)
        try:
            tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
            if "market_ohlcv" not in tables:
                raise ValueError(f"行情数据库缺少 market_ohlcv 表: {self.db_path}")
            symbol_values = [str(symbol) for symbol in symbols or () if str(symbol).strip()]
            symbol_clause = ""
            symbol_params: list[Any] = []
            if symbol_values:
                symbol_clause = f" AND symbol IN ({', '.join('?' for _ in symbol_values)})"
                symbol_params.extend(symbol_values)
            data = con.execute(
                f"""
                SELECT market, symbol, timeframe, ts, trade_date, open, high, low,
                       close, volume, amount, adjusted, adjustment
                FROM market_ohlcv
                WHERE market = ? AND timeframe = '1d'
                  AND trade_date >= ? AND trade_date <= ?
                  {symbol_clause}
                ORDER BY symbol, trade_date
                """,
                [market, start_date, end_date, *symbol_params],
            ).fetch_df()
            if "market_universe" in tables and not data.empty:
                universe_columns = {
                    str(row[0]) for row in con.execute("DESCRIBE market_universe").fetchall()
                }
                # ``market_universe`` 在旧数据库里只有当前名称/行业，
                # 新版数据库可能额外提供上市/退市生效字段。只选择实际
                # 存在的白名单列，避免升级前的数据库因缺列而无法读取。
                metadata_candidates = (
                    "market",
                    "symbol",
                    "name",
                    "industry_level1",
                    "listed_date",
                    "list_date",
                    "上市日期",
                    "delisted_date",
                    "delist_date",
                    "退市日期",
                )
                selected_metadata_columns = [
                    column for column in metadata_candidates if column in universe_columns
                ]
                if not {"market", "symbol"}.issubset(selected_metadata_columns):
                    selected_metadata_columns = []
                metadata_select = ", ".join(
                    f'"{column.replace(chr(34), chr(34) * 2)}"'
                    for column in selected_metadata_columns
                )
                if not metadata_select:
                    metadata = pd.DataFrame()
                else:
                    metadata = con.execute(
                        f"""
                        SELECT {metadata_select}
                        FROM market_universe
                        WHERE market = ?
                          {symbol_clause}
                        """,
                        [market, *symbol_params],
                    ).fetch_df()
                if not metadata.empty:
                    data = data.merge(metadata, on=["market", "symbol"], how="left")
        finally:
            con.close()
        return _normalise_dates(data)

    def load_universe_as_of(
        self,
        as_of: date,
        market: str = "a_share",
        symbols: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        """从历史 universe 表读取信号日生效的标的元数据。"""

        from .universe_history import load_universe_as_of as load_history

        return load_history(
            self.universe_db_path or self.db_path,
            as_of,
            market,
            tuple(symbols) if symbols else None,
        )


class HistoricalResearchLab:
    def __init__(
        self,
        adapter: ResearchDataAdapter,
        runs_dir: str | Path,
        plugins: dict[str, ResearchFactorPlugin] | None = None,
        data_binding: Any | None = None,
    ):
        self.adapter = adapter
        self.runs_dir = Path(runs_dir)
        self.plugins = plugins
        self.data_binding = data_binding

    def run(self, spec: ResearchSpec) -> ResearchReport:
        context: dict[str, Any] = {"requested": None, "signal_date": None, "diagnostics": {}}
        try:
            return self._run(spec, context)
        except Exception as exc:
            try:
                self._write_failure_artifact(
                    spec,
                    requested=context.get("requested"),
                    signal_date=context.get("signal_date"),
                    diagnostics=context.get("diagnostics") or {},
                    error=str(exc),
                )
            except Exception:
                # Never hide the original research error if diagnostics cannot be written.
                pass
            raise

    def _run(self, spec: ResearchSpec, context: dict[str, Any]) -> ResearchReport:
        requested = parse_date(spec.requested_date)
        context["requested"] = requested
        if spec.top_n <= 0:
            raise ValueError("top_n 必须为正数")
        horizons = tuple(sorted({int(h) for h in spec.horizons}))
        if not horizons or any(h <= 0 for h in horizons):
            raise ValueError("horizons 必须包含正整数")
        plugin = resolve_plugin(spec.rule_version, self.plugins)
        factor = factor_definition(plugin)
        _validate_factor_parameters(factor, spec.factor_params)
        if spec.market not in factor["supported_markets"]:
            raise ValueError(f"因子插件 {factor['plugin_id']} 不支持市场: {spec.market}")

        start = requested - timedelta(days=450)
        end = requested + timedelta(days=max(horizons) * 3 + 15)
        raw = self.adapter.load(start, end, spec.market)
        data = _normalise_dates(raw)
        if data.empty:
            raise ValueError("指定市场和日期区间内没有日线行情")
        _validate_columns(data)

        signal_date = _resolve_signal_date(data, requested)
        context["signal_date"] = signal_date
        history_snapshot = None
        if str(spec.universe_mode).strip().lower() == "point-in-time":
            loader = getattr(self.adapter, "load_universe_as_of", None)
            if loader is not None:
                history_snapshot = loader(signal_date, spec.market, tuple(data["symbol"].astype(str).unique()))
        data, universe_diagnostics = _apply_universe_mode(
            data,
            signal_date,
            spec.universe_mode,
            history_snapshot=history_snapshot if history_snapshot is not None and not history_snapshot.empty else None,
        )
        context["diagnostics"] = {"universe": universe_diagnostics}
        if data.empty:
            raise ValueError("指定 universe 模式下没有可用股票")
        _validate_universe_quality(universe_diagnostics, spec.data_quality_mode)
        data_quality = _quality_summary(data, signal_date)
        context["diagnostics"]["data_quality"] = data_quality
        _validate_quality_mode(data_quality, spec.data_quality_mode)
        before = data[data["date"] <= pd.Timestamp(signal_date)].copy()
        after = data[data["date"] > pd.Timestamp(signal_date)].copy()
        missing_factor_fields = sorted(set(factor["required_fields"]) - set(before.columns))
        if missing_factor_fields:
            raise ValueError(f"因子插件 {factor['plugin_id']} 输入缺少字段: {missing_factor_fields}")
        if spec.factor_params:
            scorer = getattr(plugin, "score_with_params", None)
            if not callable(scorer):
                raise ValueError(f"因子插件 {factor['plugin_id']} 未声明 score_with_params，不能接收 factor_params")
            candidates, funnel = scorer(before.copy(deep=True), dict(spec.factor_params))
        else:
            candidates, funnel = plugin.score(before.copy(deep=True))
        candidates = validate_plugin_output(
            candidates,
            before,
            plugin_id=factor["plugin_id"],
            min_history_days=factor["min_history_days"],
        )
        candidates, funnel = _prepare_plugin_candidates(candidates, funnel, before, factor)
        selected = candidates[candidates["eligible"]].sort_values(
            ["total_score", "symbol"], ascending=[False, True], kind="mergesort"
        ).head(spec.top_n)
        candidates["selected"] = False
        if not selected.empty:
            candidates.loc[candidates["symbol"].isin(selected["symbol"]), "selected"] = True

        entry_date = _next_market_date(after)
        portfolio_specs = _resolve_portfolio_specs(spec)
        portfolios: dict[str, pd.DataFrame] = {}
        portfolio_performance: dict[str, dict[int, HorizonPerformance]] = {}
        portfolio_nav: dict[str, pd.DataFrame] = {}
        benchmarks: dict[str, pd.DataFrame] = {}
        benchmark_performance_by_portfolio: dict[str, dict[int, HorizonPerformance]] = {}
        benchmark_nav_by_portfolio: dict[str, pd.DataFrame] = {}
        portfolio_diagnostics: dict[str, dict[str, Any]] = {}
        for index, portfolio_spec in enumerate(portfolio_specs):
            effective_spec = _effective_portfolio_research_spec(spec, portfolio_spec)
            portfolio, portfolio_status, portfolio_reasons = _build_portfolio(
                selected,
                after,
                entry_date,
                effective_spec,
            )
            portfolio = _annotate_portfolio(portfolio, portfolio_spec)
            performance, nav = _evaluate_forward(portfolio, after, entry_date, horizons, effective_spec)
            benchmark = _build_benchmark(candidates, after, entry_date, effective_spec)
            benchmark_performance, benchmark_nav = _evaluate_forward(
                benchmark,
                after,
                entry_date,
                horizons,
                replace(effective_spec, portfolio_weighting="equal", max_single_weight=None, max_industry_weight=None),
                include_stock_details=False,
                allow_partial=True,
            )
            portfolio_id = portfolio_spec.portfolio_id
            portfolios[portfolio_id] = portfolio
            portfolio_performance[portfolio_id] = performance
            portfolio_nav[portfolio_id] = nav
            benchmarks[portfolio_id] = _annotate_portfolio(benchmark, portfolio_spec)
            benchmark_performance_by_portfolio[portfolio_id] = benchmark_performance
            benchmark_nav_by_portfolio[portfolio_id] = benchmark_nav
            portfolio_diagnostics[portfolio_id] = {
                "name": portfolio_spec.name or portfolio_id,
                "initial_cash": effective_spec.initial_cash,
                "status": portfolio_status,
                "selected_count": int(len(portfolio)),
                "reasons": portfolio_reasons,
            }
            selected_column = "portfolio_selected" if index == 0 else f"portfolio_{_safe_id(portfolio_id)}_selected"
            reason_column = "portfolio_reason" if index == 0 else f"portfolio_{_safe_id(portfolio_id)}_reason"
            candidates[selected_column] = candidates["symbol"].isin(set(portfolio.get("symbol", [])))
            candidates[reason_column] = candidates["symbol"].map(portfolio_reasons).fillna("")

        primary_id = portfolio_specs[0].portfolio_id
        portfolio = portfolios[primary_id]
        performance = portfolio_performance[primary_id]
        nav = portfolio_nav[primary_id]
        benchmark = benchmarks[primary_id]
        benchmark_performance = benchmark_performance_by_portfolio[primary_id]
        benchmark_nav = benchmark_nav_by_portfolio[primary_id]
        portfolio_status = portfolio_diagnostics[primary_id]["status"]

        diagnostics = {
            "market": spec.market,
            "universe_mode": spec.universe_mode,
            "selection_rule": "amount_20d >= 30000000 and close > ma60; score = pct(return_60d)*0.6 + pct(return_20d)*0.3 + pct(amount_20d)*0.1",
            "factor": factor,
            "funnel": funnel,
            "entry_date": entry_date,
            "portfolio_status": portfolio_status,
            "benchmark_status": "OK" if not benchmark.empty else "EMPTY_UNIVERSE",
            "portfolios": portfolio_diagnostics,
            "universe": universe_diagnostics,
            "data_range": [data["date"].min().date(), data["date"].max().date()],
            "data_quality": data_quality,
        }
        if self.data_binding is not None:
            diagnostics["data_source"] = _data_binding_metadata(self.data_binding)
        run_id = _new_run_id(spec, signal_date, candidates, portfolios)
        artifact_dir = self._write_artifacts(
            run_id,
            spec,
            signal_date,
            candidates,
            portfolio,
            nav,
            performance,
            diagnostics,
            rule_version=plugin.plugin_id,
            rule_source_hash=plugin.source_hash(),
            factor_definition=factor,
            benchmark_performance=benchmark_performance,
            benchmark_nav=benchmark_nav,
            portfolios=portfolios,
            portfolio_performance=portfolio_performance,
            portfolio_nav=portfolio_nav,
            benchmarks=benchmarks,
            benchmark_performance_by_portfolio=benchmark_performance_by_portfolio,
            benchmark_nav_by_portfolio=benchmark_nav_by_portfolio,
            portfolio_specs=portfolio_specs,
        )
        return ResearchReport(
            run_id=run_id,
            requested_date=requested,
            signal_date=signal_date,
            candidate_table=candidates,
            portfolio=portfolio,
            nav=nav,
            performance=performance,
            diagnostics=diagnostics,
            artifact_dir=artifact_dir,
            benchmark_performance=benchmark_performance,
            benchmark_nav=benchmark_nav,
            portfolios=portfolios,
            portfolio_performance=portfolio_performance,
            portfolio_nav=portfolio_nav,
            benchmarks=benchmarks,
            benchmark_performance_by_portfolio=benchmark_performance_by_portfolio,
            benchmark_nav_by_portfolio=benchmark_nav_by_portfolio,
        )

    def run_study(
        self,
        specs: Sequence[ResearchSpec],
        *,
        bootstrap_seed: int = 0,
        bootstrap_samples: int = 2000,
    ) -> ResearchStudyReport:
        """重复运行多个历史截面并生成描述性汇总。"""
        requested_specs = tuple(specs)
        if not requested_specs:
            raise ValueError("study 至少需要一个历史日期")
        if bootstrap_samples <= 0:
            raise ValueError("bootstrap_samples 必须为正数")
        reports = tuple(self.run(spec) for spec in requested_specs)
        horizons = sorted({horizon for report in reports for horizon in report.performance})
        summary_rows: list[dict[str, Any]] = []
        for horizon in horizons:
            results = [report.performance.get(horizon) for report in reports]
            benchmark_results = [report.benchmark_performance.get(horizon) for report in reports]
            values = np.asarray(
                [result.total_return for result in results if result and result.status == "COMPLETE" and result.total_return is not None],
                dtype=float,
            )
            excess_values = np.asarray(
                [
                    result.total_return - benchmark.total_return
                    for result, benchmark in zip(results, benchmark_results, strict=True)
                    if result
                    and benchmark
                    and result.status == "COMPLETE"
                    and benchmark.status == "COMPLETE"
                    and result.total_return is not None
                    and benchmark.total_return is not None
                ],
                dtype=float,
            )
            if values.size:
                rng = np.random.default_rng(bootstrap_seed + int(horizon))
                bootstrap_means = rng.choice(values, size=(bootstrap_samples, values.size), replace=True).mean(axis=1)
                ci_low, ci_high = np.percentile(bootstrap_means, [2.5, 97.5]).tolist()
                mean_return = float(values.mean())
                median_return = float(np.median(values))
                win_rate = float(np.mean(values > 0))
                p25, p75 = np.percentile(values, [25, 75]).tolist()
            else:
                ci_low = ci_high = mean_return = median_return = win_rate = p25 = p75 = None
            if excess_values.size:
                rng = np.random.default_rng(bootstrap_seed + int(horizon) + 10_000)
                excess_bootstrap = rng.choice(excess_values, size=(bootstrap_samples, excess_values.size), replace=True).mean(axis=1)
                excess_ci_low, excess_ci_high = np.percentile(excess_bootstrap, [2.5, 97.5]).tolist()
                mean_excess = float(excess_values.mean())
                median_excess = float(np.median(excess_values))
                excess_win_rate = float(np.mean(excess_values > 0))
            else:
                excess_ci_low = excess_ci_high = mean_excess = median_excess = excess_win_rate = None
            summary_rows.append(
                {
                    "horizon": horizon,
                    "sample_count": int(values.size),
                    "requested_count": len(reports),
                    "mean_return": mean_return,
                    "median_return": median_return,
                    "win_rate": win_rate,
                    "p25": None if p25 is None else float(p25),
                    "p75": None if p75 is None else float(p75),
                    "ci95_low": None if ci_low is None else float(ci_low),
                    "ci95_high": None if ci_high is None else float(ci_high),
                    "mean_excess": mean_excess,
                    "median_excess": median_excess,
                    "excess_win_rate": excess_win_rate,
                    "excess_sample_count": int(excess_values.size),
                    "excess_ci95_low": None if excess_ci_low is None else float(excess_ci_low),
                    "excess_ci95_high": None if excess_ci_high is None else float(excess_ci_high),
                    "evidence_label": _evidence_label(excess_values, float(excess_ci_low) if excess_ci_low is not None else None, reports, horizon),
                }
            )
        summary = pd.DataFrame(summary_rows)
        portfolio_ids = sorted(
            {
                portfolio_id
                for report in reports
                for portfolio_id in (report.portfolio_performance or {"strategy": report.performance})
            }
        )
        primary_portfolio_id = (
            next(iter(reports[0].portfolio_performance), "strategy")
            if reports and reports[0].portfolio_performance
            else "strategy"
        )
        portfolio_summary_frames = []
        primary_summary = summary.copy()
        primary_summary.insert(0, "portfolio_id", primary_portfolio_id)
        portfolio_summary_frames.append(primary_summary)
        for portfolio_id in portfolio_ids:
            if portfolio_id == primary_portfolio_id:
                continue
            rows = [
                _study_summary_row(
                    reports,
                    horizon,
                    portfolio_id,
                    bootstrap_seed=bootstrap_seed,
                    bootstrap_samples=bootstrap_samples,
                )
                for horizon in horizons
            ]
            portfolio_frame = pd.DataFrame(rows)
            portfolio_frame.insert(0, "portfolio_id", portfolio_id)
            portfolio_summary_frames.append(portfolio_frame)
        portfolio_summary = (
            pd.concat(portfolio_summary_frames, ignore_index=True, sort=False)
            if portfolio_summary_frames
            else pd.DataFrame()
        )
        overlap_pairs = _overlap_pairs(reports, max(horizons) if horizons else 0)
        overlap_pairs_by_portfolio = {
            portfolio_id: _overlap_pairs(
                reports,
                max(horizons) if horizons else 0,
                portfolio_id=portfolio_id,
            )
            for portfolio_id in portfolio_ids
        }
        diagnostics = {
            "universe_modes": sorted({str(report.diagnostics.get("universe_mode", "unknown")) for report in reports}),
            "overlap_pairs": overlap_pairs,
            "overlap_pairs_by_portfolio": overlap_pairs_by_portfolio,
            "bootstrap_seed": bootstrap_seed,
            "bootstrap_samples": bootstrap_samples,
            "portfolio_ids": portfolio_ids,
            "note": "当前 observed-history 结果仅作描述性研究，不输出统计显著优势结论。",
        }
        if self.data_binding is not None:
            diagnostics["data_source"] = _data_binding_metadata(self.data_binding)
        study_id = f"study-{pd.Timestamp.now(tz='UTC').strftime('%Y%m%dT%H%M%S%fZ')}-{json_hash({'runs': [report.run_id for report in reports]})[:10]}"
        artifact_dir = self.runs_dir / "studies" / study_id
        artifact_dir.mkdir(parents=True, exist_ok=False)
        summary.to_csv(artifact_dir / "summary.csv", index=False)
        portfolio_summary.to_csv(artifact_dir / "portfolio_summary.csv", index=False)
        study_artifacts = ["summary.csv", "portfolio_summary.csv"]
        manifest = {
            "study_id": study_id,
            "run_ids": [report.run_id for report in reports],
            "specs": [_jsonable(spec.__dict__) for spec in requested_specs],
            "spec_hash": json_hash([_jsonable(spec.__dict__) for spec in requested_specs]),
            "summary": _jsonable(summary.to_dict("records")),
            "portfolio_summary": _jsonable(portfolio_summary.to_dict("records")),
            "diagnostics": _jsonable(diagnostics),
            "status": "COMPLETE",
            "artifacts": [*study_artifacts, "manifest.json"],
            "artifact_hashes": _artifact_hashes(artifact_dir, study_artifacts),
        }
        manifest["artifact_content_hash"] = json_hash(manifest["artifact_hashes"])
        (artifact_dir / "manifest.json").write_text(
            json.dumps(_jsonable(manifest), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return ResearchStudyReport(study_id, reports, summary, diagnostics, artifact_dir, portfolio_summary)

    def _write_artifacts(
        self,
        run_id: str,
        spec: ResearchSpec,
        signal_date: date,
        candidates: pd.DataFrame,
        portfolio: pd.DataFrame,
        nav: pd.DataFrame,
        performance: dict[int, HorizonPerformance],
        diagnostics: dict[str, Any],
        *,
        rule_version: str,
        rule_source_hash: str,
        factor_definition: dict[str, Any] | None = None,
        benchmark_performance: dict[int, HorizonPerformance] | None = None,
        benchmark_nav: pd.DataFrame | None = None,
        portfolios: dict[str, pd.DataFrame] | None = None,
        portfolio_performance: dict[str, dict[int, HorizonPerformance]] | None = None,
        portfolio_nav: dict[str, pd.DataFrame] | None = None,
        benchmarks: dict[str, pd.DataFrame] | None = None,
        benchmark_performance_by_portfolio: dict[str, dict[int, HorizonPerformance]] | None = None,
        benchmark_nav_by_portfolio: dict[str, pd.DataFrame] | None = None,
        portfolio_specs: Sequence[PortfolioSpec] = (),
    ) -> Path:
        artifact_dir = self.runs_dir / run_id
        artifact_dir.mkdir(parents=True, exist_ok=False)
        portfolios = portfolios or {"strategy": portfolio}
        portfolio_performance = portfolio_performance or {"strategy": performance}
        portfolio_nav = portfolio_nav or {"strategy": nav}
        benchmarks = benchmarks or {"strategy": pd.DataFrame()}
        benchmark_performance_by_portfolio = benchmark_performance_by_portfolio or {
            "strategy": benchmark_performance or {}
        }
        benchmark_nav_by_portfolio = benchmark_nav_by_portfolio or {"strategy": benchmark_nav or pd.DataFrame()}
        candidate_artifact = candidates.copy()
        candidate_artifact.insert(0, "run_id", run_id)
        portfolio_artifact = portfolio.copy()
        if "run_id" not in portfolio_artifact.columns:
            portfolio_artifact.insert(0, "run_id", run_id)
        nav_artifact = nav.copy()
        if "portfolio_id" not in nav_artifact.columns:
            nav_artifact.insert(0, "portfolio_id", next(iter(portfolios)))
        nav_artifact.insert(0, "run_id", run_id)
        benchmark_nav_artifact = (benchmark_nav if benchmark_nav is not None else pd.DataFrame()).copy()
        if "portfolio_id" not in benchmark_nav_artifact.columns:
            benchmark_nav_artifact.insert(0, "portfolio_id", next(iter(portfolios)))
        benchmark_nav_artifact.insert(0, "run_id", run_id)
        candidate_artifact.to_csv(artifact_dir / "candidates.csv", index=False)
        portfolio_artifact.to_csv(artifact_dir / "portfolio.csv", index=False)
        nav_artifact.to_csv(artifact_dir / "nav.csv", index=False)
        benchmark_nav_artifact.to_csv(
            artifact_dir / "benchmark_nav.csv", index=False
        )
        all_portfolios = _concat_portfolio_frames(portfolios)
        all_portfolio_nav = _concat_nav_frames(portfolio_nav)
        all_benchmark_nav = _concat_nav_frames(benchmark_nav_by_portfolio)
        all_portfolios.insert(0, "run_id", run_id)
        all_portfolio_nav.insert(0, "run_id", run_id)
        all_benchmark_nav.insert(0, "run_id", run_id)
        all_portfolios.to_csv(artifact_dir / "portfolios.csv", index=False)
        all_portfolio_nav.to_csv(artifact_dir / "portfolio_nav.csv", index=False)
        all_benchmark_nav.to_csv(artifact_dir / "benchmark_navs.csv", index=False)
        portfolio_returns = [
            {
                "run_id": run_id,
                "portfolio_id": portfolio_id,
                "horizon": horizon,
                "symbol": symbol,
                "return": stock_return,
                "contribution": (results[horizon].stock_contributions or {}).get(symbol),
                "winning": stock_return > 0,
            }
            for portfolio_id, results in portfolio_performance.items()
            for horizon, result in results.items()
            for symbol, stock_return in (result.stock_returns or {}).items()
        ]
        pd.DataFrame(
            portfolio_returns,
            columns=["run_id", "portfolio_id", "horizon", "symbol", "return", "contribution", "winning"],
        ).to_csv(artifact_dir / "portfolio_returns.csv", index=False)
        performance_json = _performance_json(performance)
        portfolio_performance_json = {
            portfolio_id: _performance_json(results)
            for portfolio_id, results in portfolio_performance.items()
        }
        benchmark_json = _performance_json(benchmark_performance or {})
        benchmark_by_portfolio_json = {
            portfolio_id: _performance_json(results)
            for portfolio_id, results in benchmark_performance_by_portfolio.items()
        }
        metrics = [
            {"run_id": run_id, "portfolio_id": portfolio_id, **_performance_payload(result)}
            for portfolio_id, results in portfolio_performance.items()
            for result in results.values()
        ]
        pd.DataFrame(metrics).to_csv(artifact_dir / "portfolio_metrics.csv", index=False)
        portfolio_manifest = []
        for item in portfolio_specs:
            effective = _effective_portfolio_research_spec(spec, item)
            portfolio_manifest.append(
                {
                    "portfolio_id": item.portfolio_id,
                    "name": item.name or item.portfolio_id,
                    "initial_cash": effective.initial_cash,
                    "portfolio_weighting": effective.portfolio_weighting,
                    "max_single_weight": effective.max_single_weight,
                    "max_industry_weight": effective.max_industry_weight,
                    "min_holdings": effective.min_holdings,
                    "lot_size": effective.lot_size,
                }
            )
        spec_payload = _jsonable(spec.__dict__)
        config_payload = {key: value for key, value in spec_payload.items() if key != "requested_date"}
        manifest = {
            "run_id": run_id,
            "status": "COMPLETE",
            "requested_date": parse_date(spec.requested_date).isoformat(),
            "signal_date": signal_date.isoformat(),
            "spec": {
                "market": spec.market,
                "rule_version": spec.rule_version,
                "portfolio_weighting": spec.portfolio_weighting,
                "max_single_weight": spec.max_single_weight,
                "max_industry_weight": spec.max_industry_weight,
                "min_holdings": spec.min_holdings,
                "universe_mode": spec.universe_mode,
                "top_n": spec.top_n,
                "horizons": list(spec.horizons),
                "initial_cash": spec.initial_cash,
                "commission_rate": spec.commission_rate,
                "slippage_rate": spec.slippage_rate,
                "lot_size": spec.lot_size,
                "data_quality_mode": spec.data_quality_mode,
                "factor_params": spec.factor_params,
                "portfolios": _jsonable(spec.portfolios),
            },
            "portfolios": portfolio_manifest,
            "rule_version": rule_version,
            "factor": factor_definition or {"plugin_id": rule_version},
            "spec_hash": json_hash(spec_payload),
            "config_hash": json_hash(config_payload),
            "rule_source_hash": rule_source_hash,
            "source_hash": _source_hash(),
            "code_commit": code_commit(),
            "diagnostics": _jsonable(diagnostics),
            "performance": performance_json,
            "portfolio_performance": portfolio_performance_json,
            "benchmark": benchmark_json,
            "benchmark_by_portfolio": benchmark_by_portfolio_json,
            "artifacts": [
                "candidates.csv",
                "portfolio.csv",
                "portfolios.csv",
                "portfolio_returns.csv",
                "portfolio_metrics.csv",
                "nav.csv",
                "portfolio_nav.csv",
                "benchmark_nav.csv",
                "benchmark_navs.csv",
                "manifest.json",
            ],
        }
        artifact_names = [name for name in manifest["artifacts"] if name != "manifest.json"]
        manifest["artifact_hashes"] = _artifact_hashes(artifact_dir, artifact_names)
        manifest["artifact_content_hash"] = json_hash(manifest["artifact_hashes"])
        (artifact_dir / "manifest.json").write_text(
            json.dumps(_jsonable(manifest), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return artifact_dir

    def _write_failure_artifact(
        self,
        spec: ResearchSpec,
        *,
        requested: date | None,
        signal_date: date | None,
        diagnostics: dict[str, Any],
        error: str,
    ) -> Path:
        """保存不会被后续成功运行覆盖的失败诊断。"""

        payload = {
            "requested_date": requested.isoformat() if requested else None,
            "signal_date": signal_date.isoformat() if signal_date else None,
            "spec": _jsonable(spec.__dict__),
            "diagnostics": diagnostics,
            "error": error,
        }
        run_id = (
            f"research-failed-{pd.Timestamp.now(tz='UTC').strftime('%Y%m%dT%H%M%S%fZ')}"
            f"-{json_hash(payload)[:10]}"
        )
        artifact_dir = self.runs_dir / run_id
        artifact_dir.mkdir(parents=True, exist_ok=False)
        manifest = {
            "run_id": run_id,
            "status": "FAILED",
            **payload,
            "source_hash": _source_hash(),
            "code_commit": code_commit(),
            "artifacts": ["manifest.json"],
        }
        if self.data_binding is not None:
            manifest["diagnostics"]["data_source"] = _data_binding_metadata(self.data_binding)
        (artifact_dir / "manifest.json").write_text(
            json.dumps(_jsonable(manifest), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return artifact_dir


def _normalise_dates(data: pd.DataFrame) -> pd.DataFrame:
    out = data.copy()
    if "date" not in out.columns:
        if "trade_date" in out.columns:
            out["date"] = out["trade_date"]
        elif "ts" in out.columns:
            out["date"] = out["ts"]
        else:
            raise ValueError("行情缺少 date/trade_date/ts 列")
    out["date"] = pd.to_datetime(out["date"]).dt.tz_localize(None).dt.normalize()
    out["symbol"] = out["symbol"].astype(str)
    for column in ["open", "high", "low", "close", "volume", "amount"]:
        if column not in out.columns:
            out[column] = np.nan
        out[column] = pd.to_numeric(out[column], errors="coerce")
    if "adjusted" not in out.columns:
        out["adjusted"] = False
    if "adjustment" not in out.columns:
        out["adjustment"] = "unknown"
    out["adjustment"] = out["adjustment"].fillna("unknown").astype(str).str.lower()
    return out.sort_values(["symbol", "date"]).reset_index(drop=True)


def _validate_columns(data: pd.DataFrame) -> None:
    if "symbol" not in data.columns or data["symbol"].eq("").any():
        raise ValueError("行情缺少有效 symbol")
    if data["date"].isna().any():
        raise ValueError("行情包含无效日期")
    if data[["open", "high", "low", "close"]].notna().all(axis=1).sum() == 0:
        raise ValueError("行情没有完整 OHLC 数据")


def _resolve_signal_date(data: pd.DataFrame, requested: date) -> date:
    available = sorted(data.loc[data["close"].notna(), "date"].drop_duplicates())
    prior = [d.date() for d in available if d.date() <= requested]
    if not prior:
        raise ValueError(f"请求日期 {requested} 之前没有完整收盘行情")
    return prior[-1]


def _apply_universe_mode(
    data: pd.DataFrame,
    signal_date: date,
    mode: str,
    history_snapshot: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    mode = str(mode).strip().lower() or "observed-history"
    symbols = data["symbol"].astype(str).drop_duplicates()
    if mode == "observed-history":
        return data, {
            "mode": mode,
            "total_symbols": int(len(symbols)),
            "eligible_symbols": int(len(symbols)),
            "excluded_after_as_of": 0,
        }
    if mode != "point-in-time":
        raise ValueError("universe_mode 必须是 observed-history 或 point-in-time")

    if history_snapshot is not None:
        snapshot = history_snapshot.copy()
        snapshot["symbol"] = snapshot["symbol"].astype(str)
        symbols_set = set(symbols.astype(str))
        history_symbols = set(snapshot["symbol"])
        filtered = data[data["symbol"].astype(str).isin(history_symbols)].copy()
        metadata_columns = [
            "symbol",
            "name",
            "industry_level1",
            "industry_level2",
            "industry_level3",
        ]
        metadata = snapshot[[column for column in metadata_columns if column in snapshot.columns]].drop_duplicates("symbol")
        for column in metadata.columns:
            if column != "symbol" and column in filtered.columns:
                filtered = filtered.drop(columns=[column])
        filtered = filtered.merge(metadata, on="symbol", how="left")
        snapshot_ids = sorted({str(value) for value in snapshot["snapshot_id"].dropna()})
        industry_column = filtered.get("industry_level1", pd.Series(dtype="string"))
        missing_industry_symbols = int(
            filtered.loc[industry_column.isna(), "symbol"].astype(str).nunique()
            if not filtered.empty and "symbol" in filtered.columns
            else 0
        )
        return filtered, {
            "mode": mode,
            "snapshot_id": snapshot_ids[0] if len(snapshot_ids) == 1 else "mixed",
            "history_rows": int(len(snapshot)),
            "total_symbols": int(len(symbols_set)),
            "eligible_symbols": int(len(history_symbols)),
            "excluded_after_as_of": int(len(symbols_set - history_symbols)),
            "missing_symbols": sorted(symbols_set - history_symbols),
            "industry_missing_symbols": missing_industry_symbols,
            "industry_history_available": missing_industry_symbols == 0,
            "point_in_time_quality": "complete" if missing_industry_symbols == 0 else "listing-only",
        }

    listed_column = next((column for column in ("listed_date", "list_date", "上市日期") if column in data.columns), None)
    delisted_column = next((column for column in ("delisted_date", "delist_date", "退市日期") if column in data.columns), None)
    if listed_column is None or delisted_column is None:
        raise ValueError("point-in-time universe 需要 listed_date 和 delisted_date 生效字段")
    metadata = data[["symbol", listed_column, delisted_column]].copy()
    # Keep the metadata as timezone-naive timestamps while comparing.  Using
    # ``.dt.date`` here turns an all-NaT column into a datetime64 array on
    # some pandas versions, which then cannot be compared with a ``date``
    # scalar.  Normalised timestamps also make mixed string/date inputs
    # deterministic.
    metadata["_listed"] = pd.to_datetime(metadata[listed_column], errors="coerce").dt.normalize()
    metadata["_delisted"] = pd.to_datetime(metadata[delisted_column], errors="coerce").dt.normalize()
    metadata = metadata.groupby("symbol", sort=False)[["_listed", "_delisted"]].first()
    if metadata["_listed"].notna().sum() == 0:
        raise ValueError("point-in-time universe 需要有效的 listed_date 生效字段")
    signal_timestamp = pd.Timestamp(signal_date)
    eligible = metadata["_listed"].notna() & (metadata["_listed"] <= signal_timestamp)
    eligible &= metadata["_delisted"].isna() | (metadata["_delisted"] > signal_timestamp)
    eligible_symbols = set(metadata.index[eligible].astype(str))
    filtered = data[data["symbol"].astype(str).isin(eligible_symbols)].copy()
    return filtered, {
        "mode": mode,
        "total_symbols": int(len(symbols)),
        "eligible_symbols": int(len(eligible_symbols)),
        "excluded_after_as_of": int(len(symbols) - len(eligible_symbols)),
        "missing_listing_metadata": int(metadata["_listed"].isna().sum()),
        "industry_history_available": False,
        "point_in_time_quality": "listing-only",
    }


def _validate_factor_parameters(factor: dict[str, Any], params: dict[str, Any]) -> None:
    if params is None:
        return
    if not isinstance(params, dict):
        raise ValueError("factor_params 必须是对象")
    schema = factor.get("parameter_schema") or {}
    unknown = sorted(set(params) - set(schema))
    if unknown:
        raise ValueError(f"因子插件 {factor['plugin_id']} 收到未声明参数: {unknown}")
    for name, value in params.items():
        rule = schema.get(name) or {}
        expected = rule.get("type")
        valid_type = {
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "string": isinstance(value, str),
            "boolean": isinstance(value, bool),
        }.get(expected, True)
        if not valid_type:
            raise ValueError(f"因子插件 {factor['plugin_id']} 参数 {name} 类型无效")
        if "minimum" in rule and value < rule["minimum"]:
            raise ValueError(f"因子插件 {factor['plugin_id']} 参数 {name} 小于最小值")
        if "maximum" in rule and value > rule["maximum"]:
            raise ValueError(f"因子插件 {factor['plugin_id']} 参数 {name} 大于最大值")
    required = [name for name, rule in schema.items() if isinstance(rule, dict) and rule.get("required")]
    missing = sorted(set(required) - set(params))
    if missing:
        raise ValueError(f"因子插件 {factor['plugin_id']} 缺少参数: {missing}")


def _prepare_plugin_candidates(
    candidates: pd.DataFrame,
    funnel: dict[str, Any] | None,
    before: pd.DataFrame,
    factor: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """由引擎统一补齐展示元数据、百分位前的稳定排名和漏斗默认值。"""

    output = candidates.copy()
    latest = before.sort_values(["symbol", "date"], kind="mergesort").groupby("symbol", sort=False).tail(1)
    metadata = latest.set_index(latest["symbol"].astype(str))
    if "name" not in output.columns:
        output["name"] = output["symbol"].map(metadata.get("name", pd.Series(dtype=str)))
    if "industry" not in output.columns:
        output["industry"] = output["symbol"].map(
            metadata.get("industry_level1", pd.Series(dtype=str))
        )
    output["name"] = output["name"].fillna(output["symbol"])
    output["industry"] = output["industry"].fillna("UNKNOWN")
    if "reason" not in output.columns:
        output["reason"] = np.where(output["eligible"], "通过因子", "未通过因子")
    output["total_score"] = pd.to_numeric(output["total_score"], errors="coerce")
    output["rank"] = np.nan
    eligible = output["eligible"].astype(bool)
    ascending = factor["score_direction"] == "lower_is_better"
    ranked = output[eligible].sort_values(
        ["total_score", "symbol"],
        ascending=[ascending, True],
        kind="mergesort",
    )
    output.loc[ranked.index, "rank"] = np.arange(1, len(ranked) + 1)
    normalized_funnel = dict(funnel or {})
    normalized_funnel.setdefault("universe", int(len(output)))
    normalized_funnel.setdefault("rule_eligible", int(eligible.sum()))
    normalized_funnel.setdefault("history_eligible", int(len(output)))
    return output.sort_values(["rank", "symbol"], na_position="last", kind="mergesort").reset_index(drop=True), normalized_funnel


def _score_fixed_v0(before: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    rows: list[dict[str, Any]] = []
    duplicate_groups = int(
        before.groupby(["symbol", "date"], dropna=False).size().gt(1).sum()
    )
    for symbol, group in before.groupby("symbol", sort=True):
        group = group.sort_values("date")
        reasons: list[str] = []
        clean = group.drop_duplicates("date", keep="last")
        valid = clean[clean["close"].notna() & (clean["close"] > 0)].copy()
        history_count = len(valid)
        if history_count < 120:
            reasons.append("历史不足120个交易日")
        latest = valid.iloc[-1] if not valid.empty else None
        if latest is None:
            rows.append(_ineligible_row(symbol, history_count, reasons + ["无有效收盘价"]))
            continue
        closes = valid["close"].astype(float)
        amounts = valid["amount"].dropna().astype(float)
        amount_20d = float(amounts.tail(20).mean()) if len(amounts) >= 20 else np.nan
        ma60 = float(closes.tail(60).mean()) if len(closes) >= 60 else np.nan
        return_20d = float(closes.iloc[-1] / closes.iloc[-21] - 1) if len(closes) >= 21 else np.nan
        return_60d = float(closes.iloc[-1] / closes.iloc[-61] - 1) if len(closes) >= 61 else np.nan
        if not np.isfinite(amount_20d) or amount_20d < 30_000_000:
            reasons.append("20日日均成交额低于3000万元")
        if not np.isfinite(ma60) or float(latest["close"]) <= ma60:
            reasons.append("收盘价未站上MA60")
        ohlc = clean[["open", "high", "low", "close"]].dropna()
        if not ohlc.empty:
            invalid_ohlc = (
                (ohlc["high"] < ohlc[["open", "close"]].max(axis=1))
                | (ohlc["low"] > ohlc[["open", "close"]].min(axis=1))
                | (ohlc[["open", "high", "low", "close"]] <= 0).any(axis=1)
            ).any()
            if invalid_ohlc:
                reasons.append("包含非法OHLC")
        if clean["adjustment"].nunique() != 1 or clean["adjustment"].iloc[0] in {"unknown", "none", "nan"}:
            reasons.append("复权口径未知或不一致")
        rows.append(
            {
                "symbol": symbol,
                "name": _latest_text(clean, "name", symbol),
                "industry": _latest_text(clean, "industry_level1", "UNKNOWN"),
                "history_count": history_count,
                "close": float(latest["close"]),
                "ma60": ma60,
                "return_20d": return_20d,
                "return_60d": return_60d,
                "amount_20d": amount_20d,
                "eligible": not reasons,
                "reason": "通过固定V0规则" if not reasons else "；".join(dict.fromkeys(reasons)),
                "adjustment": clean["adjustment"].iloc[-1],
            }
        )
    frame = pd.DataFrame(rows)
    eligible = frame[frame["eligible"]].copy()
    for source, output in [
        ("return_60d", "return_60d_pct"),
        ("return_20d", "return_20d_pct"),
        ("amount_20d", "amount_20d_pct"),
    ]:
        frame[output] = np.nan
        if not eligible.empty:
            frame.loc[eligible.index, output] = eligible[source].rank(pct=True) * 100.0
    frame["total_score"] = (
        frame["return_60d_pct"] * 0.6
        + frame["return_20d_pct"] * 0.3
        + frame["amount_20d_pct"] * 0.1
    )
    frame["rank"] = np.nan
    ranked = frame[frame["eligible"]].sort_values(
        ["total_score", "symbol"], ascending=[False, True], kind="mergesort"
    )
    frame.loc[ranked.index, "rank"] = np.arange(1, len(ranked) + 1)
    funnel = {
        "universe": int(len(frame)),
        "history_eligible": int((frame["history_count"] >= 120).sum()),
        "rule_eligible": int(frame["eligible"].sum()),
        "duplicate_groups": duplicate_groups,
    }
    return frame.sort_values(["rank", "symbol"], na_position="last").reset_index(drop=True), funnel


def _ineligible_row(symbol: str, history_count: int, reasons: list[str]) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": symbol,
        "industry": "UNKNOWN",
        "history_count": history_count,
        "close": np.nan,
        "ma60": np.nan,
        "return_20d": np.nan,
        "return_60d": np.nan,
        "amount_20d": np.nan,
        "eligible": False,
        "reason": "；".join(dict.fromkeys(reasons)),
        "adjustment": "unknown",
    }


def _resolve_portfolio_specs(spec: ResearchSpec) -> tuple[PortfolioSpec, ...]:
    if not spec.portfolios:
        return (
            PortfolioSpec(
                portfolio_id="strategy",
                name="策略组合",
                initial_cash=spec.initial_cash,
                portfolio_weighting=spec.portfolio_weighting,
                max_single_weight=spec.max_single_weight,
                max_industry_weight=spec.max_industry_weight,
                min_holdings=spec.min_holdings,
                lot_size=spec.lot_size,
            ),
        )
    resolved: list[PortfolioSpec] = []
    seen: set[str] = set()
    for item in spec.portfolios:
        if not isinstance(item, PortfolioSpec):
            raise ValueError("portfolios 必须由 PortfolioSpec 组成")
        portfolio_id = str(item.portfolio_id).strip()
        if not portfolio_id:
            raise ValueError("portfolio_id 不能为空")
        if portfolio_id in seen:
            raise ValueError(f"portfolio_id 重复: {portfolio_id}")
        safe_id = _safe_id(portfolio_id)
        if any(_safe_id(existing) == safe_id for existing in seen):
            raise ValueError(f"portfolio_id 经过列名标准化后重复: {portfolio_id}")
        seen.add(portfolio_id)
        resolved.append(replace(item, portfolio_id=portfolio_id))
    return tuple(resolved)


def _effective_portfolio_research_spec(spec: ResearchSpec, portfolio: PortfolioSpec) -> ResearchSpec:
    return replace(
        spec,
        initial_cash=spec.initial_cash if portfolio.initial_cash is None else float(portfolio.initial_cash),
        portfolio_weighting=(
            spec.portfolio_weighting if portfolio.portfolio_weighting is None else portfolio.portfolio_weighting
        ),
        max_single_weight=(
            spec.max_single_weight if portfolio.max_single_weight is None else portfolio.max_single_weight
        ),
        max_industry_weight=(
            spec.max_industry_weight if portfolio.max_industry_weight is None else portfolio.max_industry_weight
        ),
        min_holdings=spec.min_holdings if portfolio.min_holdings is None else int(portfolio.min_holdings),
        lot_size=spec.lot_size if portfolio.lot_size is None else int(portfolio.lot_size),
        portfolios=(),
    )


def _annotate_portfolio(frame: pd.DataFrame, portfolio: PortfolioSpec) -> pd.DataFrame:
    data = frame.copy()
    data.insert(0, "portfolio_id", portfolio.portfolio_id)
    data.insert(1, "portfolio_name", portfolio.name or portfolio.portfolio_id)
    return data


def _safe_id(value: str) -> str:
    sanitized = "".join(character if character.isalnum() or character == "_" else "_" for character in value)
    return sanitized or "portfolio"


def _latest_text(frame: pd.DataFrame, column: str, fallback: str) -> str:
    if column not in frame.columns:
        return fallback
    values = frame[column].dropna().astype(str)
    return values.iloc[-1] if not values.empty and values.iloc[-1] else fallback


def _next_market_date(after: pd.DataFrame) -> date | None:
    if after.empty:
        return None
    dates = sorted(after.loc[after["open"].notna(), "date"].drop_duplicates())
    return dates[0].date() if dates else None


def _build_portfolio(
    selected: pd.DataFrame,
    after: pd.DataFrame,
    entry_date: date | None,
    spec: ResearchSpec,
) -> tuple[pd.DataFrame, str, dict[str, str]]:
    columns = [
        "symbol",
        "name",
        "industry",
        "rank",
        "total_score",
        "target_weight",
        "entry_date",
        "entry_price",
        "shares",
    ]
    reasons: dict[str, str] = {}
    if spec.initial_cash <= 0:
        raise ValueError("initial_cash 必须为正数")
    if int(spec.lot_size) <= 0:
        raise ValueError("lot_size 必须为正整数")
    if float(spec.lot_size) != int(spec.lot_size):
        raise ValueError("lot_size 必须为正整数")
    if selected.empty or entry_date is None:
        return pd.DataFrame(columns=columns), "EMPTY_CANDIDATE_POOL", reasons
    if spec.min_holdings < 0:
        raise ValueError("min_holdings 不能为负数")
    if spec.portfolio_weighting not in {"equal", "score"}:
        raise ValueError("portfolio_weighting 必须是 equal 或 score")
    for value, label in [
        (spec.max_single_weight, "max_single_weight"),
        (spec.max_industry_weight, "max_industry_weight"),
    ]:
        if value is not None and not 0 < float(value) <= 1:
            raise ValueError(f"{label} 必须在 (0, 1] 范围内")

    selected = selected.sort_values(["rank", "symbol"], kind="mergesort").reset_index(drop=True)
    entry_rows: list[dict[str, Any]] = []
    for _, row in selected.iterrows():
        part = after[(after["symbol"] == row["symbol"]) & (after["date"] == pd.Timestamp(entry_date))]
        if part.empty or pd.isna(part.iloc[0]["open"]):
            reasons[str(row["symbol"])] = "缺少建仓日开盘价"
            continue
        entry_price = float(part.iloc[0]["open"]) * (1.0 + spec.slippage_rate)
        entry_rows.append(
            {
                "symbol": row["symbol"],
                "name": row["name"],
                "industry": row["industry"],
                "rank": int(row["rank"]),
                "total_score": float(row["total_score"]),
                "entry_date": entry_date,
                "entry_price": entry_price,
            }
        )
    if len(entry_rows) < spec.min_holdings:
        for row in entry_rows:
            reasons[str(row["symbol"])] = f"可建仓数量少于最低持仓数 {spec.min_holdings}"
        return pd.DataFrame(columns=columns), "INSUFFICIENT_HOLDINGS", reasons
    if not entry_rows:
        return pd.DataFrame(columns=columns), "NO_ENTRY_DATA", reasons

    frame = pd.DataFrame(entry_rows)
    raw_weights = _raw_portfolio_weights(frame, spec.portfolio_weighting)
    weights = _constrained_weights(
        raw_weights,
        frame["industry"].fillna("UNKNOWN").astype(str).tolist(),
        spec.max_single_weight,
        spec.max_industry_weight,
    )
    if weights is None:
        for row in entry_rows:
            reasons[str(row["symbol"])] = "组合约束不可行"
        return pd.DataFrame(columns=columns), "INFEASIBLE_CONSTRAINTS", reasons

    rows: list[dict[str, Any]] = []
    for row, weight in zip(entry_rows, weights, strict=True):
        allocated = spec.initial_cash * float(weight) / (1.0 + spec.commission_rate)
        raw_shares = allocated / row["entry_price"] if row["entry_price"] > 0 else 0.0
        shares = float(np.floor(raw_shares / spec.lot_size) * spec.lot_size)
        if shares <= 0:
            reasons[str(row["symbol"])] = f"资金不足以买入一手（整手 {spec.lot_size}）"
            continue
        rows.append({**row, "target_weight": round(float(weight), 10), "shares": shares})
    if len(rows) < spec.min_holdings:
        for row in rows:
            reasons[str(row["symbol"])] = f"可执行数量少于最低持仓数 {spec.min_holdings}"
        return pd.DataFrame(columns=columns), "INSUFFICIENT_HOLDINGS", reasons
    if not rows:
        return pd.DataFrame(columns=columns), "NO_EXECUTABLE_HOLDINGS", reasons
    portfolio = pd.DataFrame(rows, columns=columns)
    return portfolio, "OK", reasons


def _build_benchmark(
    candidates: pd.DataFrame,
    after: pd.DataFrame,
    entry_date: date | None,
    spec: ResearchSpec,
) -> pd.DataFrame:
    """用相同历史截面中的数据合格股票构造等权基准。"""
    if "history_count" not in candidates.columns:
        return pd.DataFrame()
    benchmark = candidates[pd.to_numeric(candidates["history_count"], errors="coerce") >= 120].copy()
    if "reason" in benchmark.columns:
        quality_reasons = benchmark["reason"].fillna("").astype(str)
        benchmark = benchmark[~quality_reasons.str.contains("非法OHLC|复权口径未知|无有效收盘价", regex=True)]
    if benchmark.empty:
        return pd.DataFrame()
    benchmark = benchmark.sort_values("symbol", kind="mergesort").reset_index(drop=True)
    benchmark["rank"] = np.arange(1, len(benchmark) + 1)
    benchmark["eligible"] = True
    benchmark["total_score"] = 0.0
    benchmark_spec = replace(
        spec,
        portfolio_weighting="equal",
        max_single_weight=None,
        max_industry_weight=None,
        min_holdings=0,
    )
    portfolio, _, _ = _build_portfolio(benchmark, after, entry_date, benchmark_spec)
    return portfolio


def _raw_portfolio_weights(frame: pd.DataFrame, weighting: str) -> np.ndarray:
    count = len(frame)
    if count == 0:
        return np.asarray([], dtype=float)
    if weighting == "score":
        scores = pd.to_numeric(frame["total_score"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
        scores = np.clip(scores, 0.0, None)
        if scores.sum() > 0:
            return scores / scores.sum()
    return np.full(count, 1.0 / count, dtype=float)


def _constrained_weights(
    raw_weights: np.ndarray,
    industries: Sequence[str],
    max_single_weight: float | None,
    max_industry_weight: float | None,
) -> np.ndarray | None:
    """将原始权重压入单股/行业上限，无法满足时返回 None。"""
    if raw_weights.size == 0:
        return raw_weights
    weights = np.asarray(raw_weights, dtype=float).copy()
    weights /= weights.sum()
    if max_single_weight is not None and raw_weights.size * max_single_weight < 1.0 - 1e-9:
        return None
    groups: dict[str, list[int]] = {}
    for index, industry in enumerate(industries):
        groups.setdefault(industry or "UNKNOWN", []).append(index)
    if max_industry_weight is not None and len(groups) * max_industry_weight < 1.0 - 1e-9:
        return None

    for _ in range(20):
        changed = False
        if max_industry_weight is not None:
            group_masses = np.asarray([weights[indexes].sum() for indexes in groups.values()], dtype=float)
            capped = _cap_weights(group_masses, max_industry_weight)
            if capped is None:
                return None
            for mass, indexes in zip(capped, groups.values(), strict=True):
                current = weights[indexes].sum()
                if current <= 0:
                    weights[indexes] = mass / len(indexes)
                else:
                    weights[indexes] *= mass / current
                changed |= abs(current - mass) > 1e-10
        if max_single_weight is not None:
            capped = _cap_weights(weights, max_single_weight)
            if capped is None:
                return None
            changed |= not np.allclose(weights, capped, atol=1e-10)
            weights = capped
        if not changed:
            break
    if max_industry_weight is not None and any(weights[indexes].sum() > max_industry_weight + 1e-8 for indexes in groups.values()):
        return None
    if max_single_weight is not None and np.max(weights) > max_single_weight + 1e-8:
        return None
    weights /= weights.sum()
    return weights


def _cap_weights(weights: np.ndarray, cap: float) -> np.ndarray | None:
    values = np.asarray(weights, dtype=float).copy()
    if values.size == 0:
        return values
    if values.size * cap < 1.0 - 1e-9:
        return None
    for _ in range(20):
        over = values > cap + 1e-12
        if not over.any():
            break
        excess = float((values[over] - cap).sum())
        values[over] = cap
        free = ~over
        capacity = float(np.clip(cap - values[free], 0, None).sum())
        if capacity + 1e-12 < excess:
            return None
        if free.any() and capacity > 0:
            room = np.clip(cap - values[free], 0, None)
            values[free] += excess * room / capacity
        elif excess > 1e-12:
            return None
    total = values.sum()
    if total <= 0:
        return None
    return values / total


def _evaluate_forward(
    portfolio: pd.DataFrame,
    after: pd.DataFrame,
    entry_date: date | None,
    horizons: tuple[int, ...],
    spec: ResearchSpec,
    *,
    include_stock_details: bool = True,
    allow_partial: bool = False,
) -> tuple[dict[int, HorizonPerformance], pd.DataFrame]:
    empty_nav = pd.DataFrame(columns=["date", "horizon", "equity", "daily_return", "drawdown"])
    if portfolio.empty or entry_date is None:
        return {
            h: HorizonPerformance(
                h,
                "INSUFFICIENT_FORWARD_DATA",
                None,
                None,
                None,
                None,
                "没有可建仓的股票",
                initial_cash=spec.initial_cash,
            )
            for h in horizons
        }, empty_nav
    available_dates = sorted(after.loc[after["close"].notna(), "date"].drop_duplicates())
    try:
        entry_index = available_dates.index(pd.Timestamp(entry_date))
    except ValueError:
        entry_index = -1
    if entry_index < 0:
        return {
            h: HorizonPerformance(
                h,
                "INSUFFICIENT_FORWARD_DATA",
                None,
                None,
                None,
                None,
                "没有完整建仓日行情",
                initial_cash=spec.initial_cash,
            )
            for h in horizons
        }, empty_nav

    rows: list[dict[str, Any]] = []
    performance: dict[int, HorizonPerformance] = {}
    for horizon in horizons:
        target_index = entry_index + horizon
        if target_index >= len(available_dates):
            performance[horizon] = HorizonPerformance(
                horizon,
                "INSUFFICIENT_FORWARD_DATA",
                None,
                None,
                None,
                None,
                "未来行情不足指定观察周期",
                initial_cash=spec.initial_cash,
            )
            continue
        window_dates = available_dates[entry_index : target_index + 1]
        prices = after[after["date"].isin(window_dates)].pivot_table(
            index="date", columns="symbol", values="close", aggfunc="last"
        ).reindex(window_dates)
        entry_prices = portfolio.set_index("symbol")["entry_price"]
        shares = portfolio.set_index("symbol")["shares"]
        selected_prices = prices.reindex(columns=entry_prices.index)
        if selected_prices.isna().any().any():
            if not allow_partial:
                performance[horizon] = HorizonPerformance(
                    horizon,
                    "INSUFFICIENT_FORWARD_DATA",
                    None,
                    None,
                    None,
                    None,
                    "选中股票缺少完整未来行情",
                    initial_cash=spec.initial_cash,
                )
                continue
            complete_columns = selected_prices.columns[~selected_prices.isna().any(axis=0)]
            if len(complete_columns) == 0:
                performance[horizon] = HorizonPerformance(
                    horizon,
                    "INSUFFICIENT_FORWARD_DATA",
                    None,
                    None,
                    None,
                    None,
                    "基准没有完整未来行情",
                    initial_cash=spec.initial_cash,
                )
                continue
            selected_prices = selected_prices.loc[:, complete_columns]
            entry_prices = entry_prices.reindex(complete_columns).astype(float)
            equal_weight = 1.0 / len(complete_columns)
            shares = np.floor(
                (spec.initial_cash * equal_weight / (1.0 + spec.commission_rate) / entry_prices) / spec.lot_size
            ) * spec.lot_size
            shares = shares.astype(float)
        raw_entry_prices = entry_prices / (1.0 + spec.slippage_rate)
        buy_notional = raw_entry_prices * shares
        buy_values = buy_notional * (1.0 + spec.commission_rate)
        cash_residual = float(spec.initial_cash - buy_values.sum())
        values = selected_prices.mul(shares, axis="columns")
        equity = values.sum(axis=1, min_count=1) + cash_residual
        equity = equity.dropna()
        if equity.empty or len(equity) <= horizon:
            performance[horizon] = HorizonPerformance(
                horizon,
                "INSUFFICIENT_FORWARD_DATA",
                None,
                None,
                None,
                None,
                "选中股票缺少完整未来行情",
                initial_cash=spec.initial_cash,
            )
            continue
        # 终点卖出成本单独计入净收益；净值路径按收盘市值计算。
        exit_cost_factor = (1.0 - spec.slippage_rate) * (1.0 - spec.commission_rate)
        exit_prices = selected_prices.iloc[-1].astype(float)
        net_exit_values = exit_prices * shares * exit_cost_factor
        all_stock_returns = {
            str(symbol): float(value)
            for symbol, value in (net_exit_values / buy_values - 1.0).items()
        }
        all_stock_contributions = {
            str(symbol): float(value)
            for symbol, value in ((net_exit_values - buy_values) / spec.initial_cash).items()
        }
        holding_win_rate = float(np.mean([value > 0 for value in all_stock_returns.values()]))
        # 成本前收益只反映收盘价相对信号后首个开盘价的变动，不把建仓
        # 佣金/滑点混入该指标。``equity`` 仍然保留真实建仓后的净值路径，
        # 因此可以同时看到成本前价格表现和成本后可实现收益。
        target_weights = buy_values / spec.initial_cash
        gross_stock_returns = exit_prices / raw_entry_prices - 1.0
        gross_return = float(gross_stock_returns.mul(target_weights).sum())
        total_return = float((net_exit_values.sum() + cash_residual) / spec.initial_cash - 1.0)
        peak = equity.cummax()
        drawdown = equity / peak - 1.0
        max_drawdown = float(drawdown.min())
        previous = equity.shift(1)
        daily_return = equity / previous - 1.0
        daily_return.iloc[0] = equity.iloc[0] / spec.initial_cash - 1.0
        ending_equity = float(net_exit_values.sum() + cash_residual)
        profit_loss = ending_equity - float(spec.initial_cash)
        max_drawdown_amount = float((equity - peak).min())
        daily_volatility = float(daily_return.std(ddof=1)) if len(daily_return) > 1 else None
        annualized_volatility = daily_volatility * np.sqrt(252.0) if daily_volatility is not None else None
        annualized_return = (
            float((ending_equity / spec.initial_cash) ** (252.0 / horizon) - 1.0)
            if ending_equity > 0
            else -1.0
        )
        sharpe = (
            float(daily_return.mean() / daily_volatility * np.sqrt(252.0))
            if daily_volatility is not None and daily_volatility > 0
            else None
        )
        buy_notional = raw_entry_prices * shares
        exit_notional = exit_prices * shares * (1.0 - spec.slippage_rate)
        commission_paid = float(
            buy_notional.mul(spec.commission_rate).sum() + exit_notional.mul(spec.commission_rate).sum()
        )
        slippage_paid = float(
            (entry_prices - raw_entry_prices).mul(shares).sum()
            + (exit_prices * spec.slippage_rate).mul(shares).sum()
        )
        for current_date, current_equity, current_return, current_drawdown in zip(
            equity.index, equity, daily_return, drawdown, strict=True
        ):
            rows.append(
                {
                    "date": current_date.date(),
                    "horizon": horizon,
                    "equity": float(current_equity),
                    "daily_return": float(current_return),
                    "drawdown": float(current_drawdown),
                }
            )
        performance[horizon] = HorizonPerformance(
            horizon=horizon,
            status="COMPLETE",
            total_return=total_return,
            max_drawdown=max_drawdown,
            gross_return=gross_return,
            evaluated_date=window_dates[-1].date(),
            holding_win_rate=holding_win_rate,
            stock_returns=all_stock_returns if include_stock_details else None,
            stock_contributions=all_stock_contributions if include_stock_details else None,
            initial_cash=float(spec.initial_cash),
            ending_equity=ending_equity,
            profit_loss=profit_loss,
            max_drawdown_amount=max_drawdown_amount,
            daily_volatility=daily_volatility,
            annualized_return=annualized_return,
            annualized_volatility=annualized_volatility,
            sharpe=sharpe,
            commission_paid=commission_paid,
            slippage_paid=slippage_paid,
            cash_residual=cash_residual,
        )
    return performance, pd.DataFrame(rows, columns=["date", "horizon", "equity", "daily_return", "drawdown"])


def _quality_summary(data: pd.DataFrame, signal_date: date) -> dict[str, Any]:
    prices = data[["open", "high", "low", "close"]]
    selection = data[data["date"] <= pd.Timestamp(signal_date)]
    selection_prices = selection[["open", "high", "low", "close"]]
    invalid_ohlc = int(
        ((prices["high"] < prices[["open", "close"]].max(axis=1)) | (prices["low"] > prices[["open", "close"]].min(axis=1))).fillna(False).sum()
    )
    selection_invalid_ohlc = int(
        ((selection_prices["high"] < selection_prices[["open", "close"]].max(axis=1)) | (selection_prices["low"] > selection_prices[["open", "close"]].min(axis=1))).fillna(False).sum()
    )
    adjustment_by_symbol = selection.groupby("symbol")["adjustment"].nunique()
    mixed_adjustment = int((adjustment_by_symbol > 1).sum())
    unknown_adjustment = int(selection["adjustment"].isin({"unknown", "none", "nan"}).sum())
    duplicate_groups = int(data.groupby(["symbol", "date"], dropna=False).size().gt(1).sum())
    non_positive_rows = int((prices <= 0).any(axis=1).sum())
    selection_non_positive_rows = int((selection_prices <= 0).any(axis=1).sum())
    incomplete_ohlc_rows = int(prices.isna().any(axis=1).sum())
    selection_incomplete_ohlc_rows = int(selection_prices.isna().any(axis=1).sum())
    missing_volume_amount_rows = int(selection[["volume", "amount"]].isna().all(axis=1).sum())
    return {
        "rows": int(len(data)),
        "symbols": int(data["symbol"].nunique()),
        "invalid_ohlc_rows": invalid_ohlc,
        "selection_invalid_ohlc_rows": selection_invalid_ohlc,
        "duplicate_groups": duplicate_groups,
        "non_positive_ohlc_rows": non_positive_rows,
        "selection_non_positive_ohlc_rows": selection_non_positive_rows,
        "incomplete_ohlc_rows": incomplete_ohlc_rows,
        "selection_incomplete_ohlc_rows": selection_incomplete_ohlc_rows,
        "missing_volume_amount_rows": missing_volume_amount_rows,
        "mixed_adjustment_symbols": mixed_adjustment,
        "unknown_adjustment_rows": unknown_adjustment,
    }


def _validate_quality_mode(summary: dict[str, Any], mode: str) -> None:
    normalized = str(mode).strip().lower() or "exploratory"
    if normalized not in {"exploratory", "strict"}:
        raise ValueError("data_quality_mode 必须是 exploratory 或 strict")
    summary["mode"] = normalized
    reasons: list[str] = []
    if normalized == "strict":
        checks = (
            (summary["duplicate_groups"], "存在重复日线"),
            (summary["selection_invalid_ohlc_rows"], "存在非法 OHLC"),
            (summary["selection_non_positive_ohlc_rows"], "存在非正 OHLC"),
            (summary["selection_incomplete_ohlc_rows"], "存在不完整 OHLC"),
            (summary["missing_volume_amount_rows"], "缺少成交量和成交额"),
            (summary["mixed_adjustment_symbols"], "存在混合复权标的"),
            (summary["unknown_adjustment_rows"], "存在未知或未复权行情"),
        )
        reasons = [message for count, message in checks if count]
    summary["status"] = "PASS" if not reasons else "FAILED"
    summary["reasons"] = reasons
    if reasons:
        raise ValueError(f"数据质量校验失败（{normalized}）: {'；'.join(reasons)}")


def _validate_universe_quality(summary: dict[str, Any], mode: str) -> None:
    """严格模式禁止把仅有上市窗口的 universe 当作正式 PIT 数据。"""

    normalized = str(mode).strip().lower() or "exploratory"
    if normalized == "strict" and summary.get("mode") == "point-in-time":
        if summary.get("point_in_time_quality") != "complete":
            raise ValueError(
                "严格模式要求完整 point-in-time universe；当前缺少历史行业分类生效区间"
            )


def _evidence_label(
    values: np.ndarray,
    ci_low: float | None,
    reports: Sequence[ResearchReport],
    horizon: int,
    portfolio_id: str | None = None,
) -> str:
    """只在正式 point-in-time 且样本足够、窗口独立时允许正向证据标签。"""
    if values.size < 20 or ci_low is None or ci_low <= 0:
        return "DESCRIPTIVE_ONLY"
    if any(report.diagnostics.get("universe_mode") != "point-in-time" for report in reports):
        return "DESCRIPTIVE_ONLY"
    if any(report.diagnostics.get("universe", {}).get("point_in_time_quality") != "complete" for report in reports):
        return "DESCRIPTIVE_ONLY"
    if _overlap_pairs(reports, horizon, portfolio_id=portfolio_id):
        return "DESCRIPTIVE_ONLY"
    return "POSITIVE_EVIDENCE"


def _overlap_pairs(
    reports: Sequence[ResearchReport],
    horizon: int,
    *,
    portfolio_id: str | None = None,
) -> list[list[str]]:
    windows: list[tuple[str, date, date]] = []
    for report in reports:
        performance = (
            report.portfolio_performance.get(portfolio_id, {}).get(horizon)
            if portfolio_id is not None
            else report.performance.get(horizon)
        )
        if not performance or performance.status != "COMPLETE" or performance.evaluated_date is None:
            continue
        start_value = report.diagnostics.get("entry_date") or report.signal_date
        start = parse_date(start_value)
        windows.append((report.run_id, start, performance.evaluated_date))
    overlaps: list[list[str]] = []
    for index, (left_id, left_start, left_end) in enumerate(windows):
        for right_id, right_start, right_end in windows[index + 1 :]:
            if max(left_start, right_start) <= min(left_end, right_end):
                overlaps.append([left_id, right_id])
    return overlaps


def _study_summary_row(
    reports: Sequence[ResearchReport],
    horizon: int,
    portfolio_id: str,
    *,
    bootstrap_seed: int,
    bootstrap_samples: int,
) -> dict[str, Any]:
    results = [report.portfolio_performance.get(portfolio_id, {}).get(horizon) for report in reports]
    benchmark_results = [
        report.benchmark_performance_by_portfolio.get(portfolio_id, {}).get(horizon) for report in reports
    ]
    values = np.asarray(
        [
            result.total_return
            for result in results
            if result and result.status == "COMPLETE" and result.total_return is not None
        ],
        dtype=float,
    )
    excess_values = np.asarray(
        [
            result.total_return - benchmark.total_return
            for result, benchmark in zip(results, benchmark_results, strict=True)
            if result
            and benchmark
            and result.status == "COMPLETE"
            and benchmark.status == "COMPLETE"
            and result.total_return is not None
            and benchmark.total_return is not None
        ],
        dtype=float,
    )
    if values.size:
        rng = np.random.default_rng(bootstrap_seed + int(horizon))
        bootstrap_means = rng.choice(values, size=(bootstrap_samples, values.size), replace=True).mean(axis=1)
        ci_low, ci_high = np.percentile(bootstrap_means, [2.5, 97.5]).tolist()
        mean_return = float(values.mean())
        median_return = float(np.median(values))
        win_rate = float(np.mean(values > 0))
        p25, p75 = np.percentile(values, [25, 75]).tolist()
    else:
        ci_low = ci_high = mean_return = median_return = win_rate = p25 = p75 = None
    if excess_values.size:
        rng = np.random.default_rng(bootstrap_seed + int(horizon) + 10_000)
        excess_bootstrap = rng.choice(
            excess_values,
            size=(bootstrap_samples, excess_values.size),
            replace=True,
        ).mean(axis=1)
        excess_ci_low, excess_ci_high = np.percentile(excess_bootstrap, [2.5, 97.5]).tolist()
        mean_excess = float(excess_values.mean())
        median_excess = float(np.median(excess_values))
        excess_win_rate = float(np.mean(excess_values > 0))
    else:
        excess_ci_low = excess_ci_high = mean_excess = median_excess = excess_win_rate = None
    return {
        "horizon": horizon,
        "sample_count": int(values.size),
        "requested_count": len(reports),
        "mean_return": mean_return,
        "median_return": median_return,
        "win_rate": win_rate,
        "p25": None if p25 is None else float(p25),
        "p75": None if p75 is None else float(p75),
        "ci95_low": None if ci_low is None else float(ci_low),
        "ci95_high": None if ci_high is None else float(ci_high),
        "mean_excess": mean_excess,
        "median_excess": median_excess,
        "excess_win_rate": excess_win_rate,
        "excess_sample_count": int(excess_values.size),
        "excess_ci95_low": None if excess_ci_low is None else float(excess_ci_low),
        "excess_ci95_high": None if excess_ci_high is None else float(excess_ci_high),
        "evidence_label": _evidence_label(
            excess_values,
            float(excess_ci_low) if excess_ci_low is not None else None,
            reports,
            horizon,
            portfolio_id=portfolio_id,
        ),
    }


def _performance_payload(performance: HorizonPerformance) -> dict[str, Any]:
    return {
        "horizon": performance.horizon,
        "status": performance.status,
        "total_return": performance.total_return,
        "max_drawdown": performance.max_drawdown,
        "gross_return": performance.gross_return,
        "evaluated_date": performance.evaluated_date.isoformat() if performance.evaluated_date else None,
        "message": performance.message,
        "holding_win_rate": performance.holding_win_rate,
        "stock_returns": performance.stock_returns,
        "stock_contributions": performance.stock_contributions,
        "initial_cash": performance.initial_cash,
        "ending_equity": performance.ending_equity,
        "profit_loss": performance.profit_loss,
        "max_drawdown_amount": performance.max_drawdown_amount,
        "daily_volatility": performance.daily_volatility,
        "annualized_return": performance.annualized_return,
        "annualized_volatility": performance.annualized_volatility,
        "sharpe": performance.sharpe,
        "commission_paid": performance.commission_paid,
        "slippage_paid": performance.slippage_paid,
        "cash_residual": performance.cash_residual,
    }


def _performance_json(performance: dict[int, HorizonPerformance]) -> dict[str, dict[str, Any]]:
    return {str(horizon): _performance_payload(result) for horizon, result in performance.items()}


def _concat_portfolio_frames(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()
    return pd.concat(list(frames.values()), ignore_index=True, sort=False)


def _concat_nav_frames(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for portfolio_id, frame in frames.items():
        data = frame.copy()
        if "portfolio_id" not in data.columns:
            data.insert(0, "portfolio_id", portfolio_id)
        rows.append(data)
    return pd.concat(rows, ignore_index=True, sort=False) if rows else pd.DataFrame()


def _new_run_id(
    spec: ResearchSpec,
    signal_date: date,
    candidates: pd.DataFrame,
    portfolios: dict[str, pd.DataFrame],
) -> str:
    digest = json_hash(
        {
            "requested_date": parse_date(spec.requested_date).isoformat(),
            "signal_date": signal_date.isoformat(),
            "spec": _jsonable(spec.__dict__),
            "candidates": candidates.to_dict("records"),
            "portfolios": {portfolio_id: frame.to_dict("records") for portfolio_id, frame in portfolios.items()},
        }
    )[:10]
    return f"research-{pd.Timestamp.now(tz='UTC').strftime('%Y%m%dT%H%M%S%fZ')}-{digest}"


def _fixed_rule_hash() -> str:
    source = "amount_20d >= 30000000; close > ma60; score=0.6*pct(return_60d)+0.3*pct(return_20d)+0.1*pct(amount_20d)"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _source_hash() -> str:
    """返回研究引擎源码哈希，便于定位未提交代码运行的实验。"""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _artifact_hashes(artifact_dir: Path, names: Sequence[str]) -> dict[str, str]:
    """计算已写入的非 manifest 产物内容哈希；manifest 自身不能自包含哈希。"""

    return {
        name: hashlib.sha256((artifact_dir / name).read_bytes()).hexdigest()
        for name in names
        if (artifact_dir / name).is_file()
    }


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (pd.Timestamp, date)):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _data_binding_metadata(binding: Any) -> dict[str, Any]:
    if hasattr(binding, "to_dict"):
        value = binding.to_dict()
        if isinstance(value, dict):
            return value
    return {"db_path": str(getattr(binding, "db_path", binding)), "source": "local-duckdb"}
