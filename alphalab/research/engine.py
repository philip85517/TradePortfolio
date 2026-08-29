"""固定 V0 历史截面研究引擎。

研究模块刻意与模拟交易账本分离：它只读取行情、生成冻结的研究产物，
不会创建订单或写入账户状态。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Protocol, Sequence

import numpy as np
import pandas as pd

from ..utils import code_commit, json_hash, parse_date
from .plugins import ResearchFactorPlugin, resolve_plugin


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


@dataclass
class ResearchStudyReport:
    """多个历史截面的描述性汇总。"""

    study_id: str
    reports: tuple[ResearchReport, ...]
    summary: pd.DataFrame
    diagnostics: dict[str, Any]
    artifact_dir: Path


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

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser()

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


class HistoricalResearchLab:
    def __init__(
        self,
        adapter: ResearchDataAdapter,
        runs_dir: str | Path,
        plugins: dict[str, ResearchFactorPlugin] | None = None,
    ):
        self.adapter = adapter
        self.runs_dir = Path(runs_dir)
        self.plugins = plugins

    def run(self, spec: ResearchSpec) -> ResearchReport:
        requested = parse_date(spec.requested_date)
        if spec.top_n <= 0:
            raise ValueError("top_n 必须为正数")
        horizons = tuple(sorted({int(h) for h in spec.horizons}))
        if not horizons or any(h <= 0 for h in horizons):
            raise ValueError("horizons 必须包含正整数")
        plugin = resolve_plugin(spec.rule_version, self.plugins)

        start = requested - timedelta(days=450)
        end = requested + timedelta(days=max(horizons) * 3 + 15)
        raw = self.adapter.load(start, end, spec.market)
        data = _normalise_dates(raw)
        if data.empty:
            raise ValueError("指定市场和日期区间内没有日线行情")
        _validate_columns(data)

        signal_date = _resolve_signal_date(data, requested)
        data, universe_diagnostics = _apply_universe_mode(data, signal_date, spec.universe_mode)
        if data.empty:
            raise ValueError("指定 universe 模式下没有可用股票")
        before = data[data["date"] <= pd.Timestamp(signal_date)].copy()
        after = data[data["date"] > pd.Timestamp(signal_date)].copy()
        candidates, funnel = plugin.score(before)
        selected = candidates[candidates["eligible"]].sort_values(
            ["total_score", "symbol"], ascending=[False, True], kind="mergesort"
        ).head(spec.top_n)
        candidates["selected"] = False
        if not selected.empty:
            candidates.loc[candidates["symbol"].isin(selected["symbol"]), "selected"] = True

        entry_date = _next_market_date(after)
        portfolio, portfolio_status, portfolio_reasons = _build_portfolio(selected, after, entry_date, spec)
        candidates["portfolio_selected"] = candidates["symbol"].isin(set(portfolio.get("symbol", [])))
        candidates["portfolio_reason"] = candidates["symbol"].map(portfolio_reasons).fillna("")
        performance, nav = _evaluate_forward(portfolio, after, entry_date, horizons, spec)
        benchmark = _build_benchmark(candidates, after, entry_date, spec)
        benchmark_performance, benchmark_nav = _evaluate_forward(
            benchmark,
            after,
            entry_date,
            horizons,
            replace(spec, portfolio_weighting="equal", max_single_weight=None, max_industry_weight=None),
            include_stock_details=False,
            allow_partial=True,
        )

        diagnostics = {
            "market": spec.market,
            "universe_mode": spec.universe_mode,
            "selection_rule": "amount_20d >= 30000000 and close > ma60; score = pct(return_60d)*0.6 + pct(return_20d)*0.3 + pct(amount_20d)*0.1",
            "funnel": funnel,
            "entry_date": entry_date,
            "portfolio_status": portfolio_status,
            "benchmark_status": "OK" if not benchmark.empty else "EMPTY_UNIVERSE",
            "universe": universe_diagnostics,
            "data_range": [data["date"].min().date(), data["date"].max().date()],
            "data_quality": _quality_summary(data, signal_date),
        }
        run_id = _new_run_id(spec, signal_date, candidates, portfolio)
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
            benchmark_performance=benchmark_performance,
            benchmark_nav=benchmark_nav,
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
        overlap_pairs = _overlap_pairs(reports, max(horizons) if horizons else 0)
        diagnostics = {
            "universe_modes": sorted({str(report.diagnostics.get("universe_mode", "unknown")) for report in reports}),
            "overlap_pairs": overlap_pairs,
            "bootstrap_seed": bootstrap_seed,
            "bootstrap_samples": bootstrap_samples,
            "note": "当前 observed-history 结果仅作描述性研究，不输出统计显著优势结论。",
        }
        study_id = f"study-{pd.Timestamp.now(tz='UTC').strftime('%Y%m%dT%H%M%S%fZ')}-{json_hash({'runs': [report.run_id for report in reports]})[:10]}"
        artifact_dir = self.runs_dir / "studies" / study_id
        artifact_dir.mkdir(parents=True, exist_ok=False)
        summary.to_csv(artifact_dir / "summary.csv", index=False)
        manifest = {
            "study_id": study_id,
            "run_ids": [report.run_id for report in reports],
            "specs": [_jsonable(spec.__dict__) for spec in requested_specs],
            "summary": _jsonable(summary.to_dict("records")),
            "diagnostics": _jsonable(diagnostics),
            "artifacts": ["summary.csv", "manifest.json"],
        }
        (artifact_dir / "manifest.json").write_text(
            json.dumps(_jsonable(manifest), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return ResearchStudyReport(study_id, reports, summary, diagnostics, artifact_dir)

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
        benchmark_performance: dict[int, HorizonPerformance] | None = None,
        benchmark_nav: pd.DataFrame | None = None,
    ) -> Path:
        artifact_dir = self.runs_dir / run_id
        artifact_dir.mkdir(parents=True, exist_ok=False)
        candidates.to_csv(artifact_dir / "candidates.csv", index=False)
        portfolio.to_csv(artifact_dir / "portfolio.csv", index=False)
        nav.to_csv(artifact_dir / "nav.csv", index=False)
        (benchmark_nav if benchmark_nav is not None else pd.DataFrame()).to_csv(
            artifact_dir / "benchmark_nav.csv", index=False
        )
        portfolio_returns = [
            {
                "horizon": horizon,
                "symbol": symbol,
                "return": stock_return,
                "contribution": (performance[horizon].stock_contributions or {}).get(symbol),
                "winning": stock_return > 0,
            }
            for horizon, result in performance.items()
            for symbol, stock_return in (result.stock_returns or {}).items()
        ]
        pd.DataFrame(
            portfolio_returns,
            columns=["horizon", "symbol", "return", "contribution", "winning"],
        ).to_csv(artifact_dir / "portfolio_returns.csv", index=False)
        performance_json = {
            str(h): {
                "horizon": p.horizon,
                "status": p.status,
                "total_return": p.total_return,
                "max_drawdown": p.max_drawdown,
                "gross_return": p.gross_return,
                "evaluated_date": p.evaluated_date.isoformat() if p.evaluated_date else None,
                "message": p.message,
                "holding_win_rate": p.holding_win_rate,
                "stock_returns": p.stock_returns,
                "stock_contributions": p.stock_contributions,
            }
            for h, p in performance.items()
        }
        benchmark_json = {
            str(h): {
                "horizon": p.horizon,
                "status": p.status,
                "total_return": p.total_return,
                "max_drawdown": p.max_drawdown,
                "gross_return": p.gross_return,
                "evaluated_date": p.evaluated_date.isoformat() if p.evaluated_date else None,
                "message": p.message,
                "holding_win_rate": p.holding_win_rate,
            }
            for h, p in (benchmark_performance or {}).items()
        }
        manifest = {
            "run_id": run_id,
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
            },
            "rule_version": rule_version,
            "rule_source_hash": rule_source_hash,
            "source_hash": _source_hash(),
            "code_commit": code_commit(),
            "diagnostics": _jsonable(diagnostics),
            "performance": performance_json,
            "benchmark": benchmark_json,
            "artifacts": [
                "candidates.csv",
                "portfolio.csv",
                "portfolio_returns.csv",
                "nav.csv",
                "benchmark_nav.csv",
                "manifest.json",
            ],
        }
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
    }


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
        shares = allocated / row["entry_price"] if row["entry_price"] > 0 else 0.0
        rows.append({**row, "target_weight": round(float(weight), 10), "shares": shares})
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
            h: HorizonPerformance(h, "INSUFFICIENT_FORWARD_DATA", None, None, None, None, "没有可建仓的股票")
            for h in horizons
        }, empty_nav
    available_dates = sorted(after.loc[after["close"].notna(), "date"].drop_duplicates())
    try:
        entry_index = available_dates.index(pd.Timestamp(entry_date))
    except ValueError:
        entry_index = -1
    if entry_index < 0:
        return {
            h: HorizonPerformance(h, "INSUFFICIENT_FORWARD_DATA", None, None, None, None, "没有完整建仓日行情")
            for h in horizons
        }, empty_nav

    rows: list[dict[str, Any]] = []
    performance: dict[int, HorizonPerformance] = {}
    for horizon in horizons:
        target_index = entry_index + horizon
        if target_index >= len(available_dates):
            performance[horizon] = HorizonPerformance(
                horizon, "INSUFFICIENT_FORWARD_DATA", None, None, None, None, "未来行情不足指定观察周期"
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
                )
                continue
            selected_prices = selected_prices.loc[:, complete_columns]
            entry_prices = entry_prices.reindex(complete_columns).astype(float)
            equal_weight = 1.0 / len(complete_columns)
            shares = spec.initial_cash * equal_weight / (1.0 + spec.commission_rate) / entry_prices
        values = selected_prices.mul(shares, axis="columns")
        equity = values.sum(axis=1, min_count=1)
        equity = equity.dropna()
        if equity.empty or len(equity) <= horizon:
            performance[horizon] = HorizonPerformance(
                horizon, "INSUFFICIENT_FORWARD_DATA", None, None, None, None, "选中股票缺少完整未来行情"
            )
            continue
        # 终点卖出成本单独计入净收益；净值路径按收盘市值计算。
        exit_cost_factor = (1.0 - spec.slippage_rate) * (1.0 - spec.commission_rate)
        exit_prices = selected_prices.iloc[-1].astype(float)
        buy_values = entry_prices * shares * (1.0 + spec.commission_rate)
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
        raw_entry_prices = entry_prices / (1.0 + spec.slippage_rate)
        target_weights = portfolio.set_index("symbol")["target_weight"].reindex(selected_prices.columns)
        if allow_partial:
            target_weights = pd.Series(
                1.0 / len(selected_prices.columns), index=selected_prices.columns, dtype=float
            )
        gross_stock_returns = exit_prices / raw_entry_prices - 1.0
        gross_return = float(gross_stock_returns.mul(target_weights).sum())
        total_return = float((equity.iloc[-1] * exit_cost_factor) / spec.initial_cash - 1.0)
        peak = equity.cummax()
        drawdown = equity / peak - 1.0
        max_drawdown = float(drawdown.min())
        previous = equity.shift(1)
        daily_return = equity / previous - 1.0
        daily_return.iloc[0] = equity.iloc[0] / spec.initial_cash - 1.0
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
            horizon,
            "COMPLETE",
            total_return,
            max_drawdown,
            gross_return,
            window_dates[-1].date(),
            None,
            holding_win_rate,
            all_stock_returns if include_stock_details else None,
            all_stock_contributions if include_stock_details else None,
        )
    return performance, pd.DataFrame(rows, columns=["date", "horizon", "equity", "daily_return", "drawdown"])


def _quality_summary(data: pd.DataFrame, signal_date: date) -> dict[str, Any]:
    prices = data[["open", "high", "low", "close"]]
    invalid_ohlc = int(
        ((prices["high"] < prices[["open", "close"]].max(axis=1)) | (prices["low"] > prices[["open", "close"]].min(axis=1))).fillna(False).sum()
    )
    adjustment_by_symbol = data[data["date"] <= pd.Timestamp(signal_date)].groupby("symbol")["adjustment"].nunique()
    mixed_adjustment = int((adjustment_by_symbol > 1).sum())
    unknown_adjustment = int(data.loc[data["date"] <= pd.Timestamp(signal_date), "adjustment"].isin({"unknown", "none", "nan"}).sum())
    duplicate_groups = int(data.groupby(["symbol", "date"], dropna=False).size().gt(1).sum())
    non_positive_rows = int((prices <= 0).any(axis=1).sum())
    return {
        "rows": int(len(data)),
        "symbols": int(data["symbol"].nunique()),
        "invalid_ohlc_rows": invalid_ohlc,
        "duplicate_groups": duplicate_groups,
        "non_positive_ohlc_rows": non_positive_rows,
        "mixed_adjustment_symbols": mixed_adjustment,
        "unknown_adjustment_rows": unknown_adjustment,
    }


def _evidence_label(
    values: np.ndarray,
    ci_low: float | None,
    reports: Sequence[ResearchReport],
    horizon: int,
) -> str:
    """只在正式 point-in-time 且样本足够、窗口独立时允许正向证据标签。"""
    if values.size < 20 or ci_low is None or ci_low <= 0:
        return "DESCRIPTIVE_ONLY"
    if any(report.diagnostics.get("universe_mode") != "point-in-time" for report in reports):
        return "DESCRIPTIVE_ONLY"
    if _overlap_pairs(reports, horizon):
        return "DESCRIPTIVE_ONLY"
    return "POSITIVE_EVIDENCE"


def _overlap_pairs(reports: Sequence[ResearchReport], horizon: int) -> list[list[str]]:
    windows: list[tuple[str, date, date]] = []
    for report in reports:
        performance = report.performance.get(horizon)
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


def _new_run_id(spec: ResearchSpec, signal_date: date, candidates: pd.DataFrame, portfolio: pd.DataFrame) -> str:
    digest = json_hash(
        {
            "requested_date": parse_date(spec.requested_date).isoformat(),
            "signal_date": signal_date.isoformat(),
            "spec": spec.__dict__,
            "candidates": candidates.to_dict("records"),
            "portfolio": portfolio.to_dict("records"),
        }
    )[:10]
    return f"research-{pd.Timestamp.now(tz='UTC').strftime('%Y%m%dT%H%M%S%fZ')}-{digest}"


def _fixed_rule_hash() -> str:
    source = "amount_20d >= 30000000; close > ma60; score=0.6*pct(return_60d)+0.3*pct(return_20d)+0.1*pct(amount_20d)"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _source_hash() -> str:
    """返回研究引擎源码哈希，便于定位未提交代码运行的实验。"""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _jsonable(value: Any) -> Any:
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
