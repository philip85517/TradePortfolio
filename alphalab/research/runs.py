"""冻结研究运行的轻量管理与比较能力。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


class ResearchRunStore:
    """只读取研究产物目录，不修改既有运行。"""

    def __init__(self, runs_dir: str | Path):
        self.runs_dir = Path(runs_dir).expanduser().resolve()

    def list(self) -> list[dict[str, Any]]:
        """列出可读取的运行摘要，按运行 ID 稳定排序。"""
        if not self.runs_dir.exists():
            return []
        summaries: list[dict[str, Any]] = []
        for manifest_path in self.runs_dir.glob("*/manifest.json"):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            run_id = str(manifest.get("run_id", ""))
            if not run_id or manifest_path.parent.name != run_id:
                continue
            performance = manifest.get("performance", {})
            portfolio_performance = manifest.get("portfolio_performance", {"strategy": performance})
            summaries.append(
                {
                    "run_id": run_id,
                    "status": manifest.get("status", "COMPLETE"),
                    "error": manifest.get("error"),
                    "requested_date": manifest.get("requested_date"),
                    "signal_date": manifest.get("signal_date"),
                    "rule_version": manifest.get("rule_version"),
                    "top_n": manifest.get("spec", {}).get("top_n"),
                    "selected_count": len(self._portfolio_symbols(run_id)),
                    "performance": {
                        str(horizon): {
                            "status": result.get("status"),
                            "total_return": result.get("total_return"),
                            "max_drawdown": result.get("max_drawdown"),
                        }
                        for horizon, result in performance.items()
                    },
                    "portfolios": _portfolio_summaries(manifest, portfolio_performance),
                }
            )
        return sorted(summaries, key=lambda item: item["run_id"])

    def manifest(self, run_id: str) -> dict[str, Any]:
        run_dir = self._run_dir(run_id)
        path = run_dir / "manifest.json"
        if not path.is_file():
            raise FileNotFoundError(f"研究运行不存在: {run_id}")
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"运行 manifest 无法读取: {run_id}") from exc
        if manifest.get("run_id") != str(run_id):
            raise ValueError("manifest.json 中的运行 ID 与目录不一致")
        return manifest

    def compare(self, left_run_id: str, right_run_id: str) -> dict[str, Any]:
        left = self.manifest(left_run_id)
        right = self.manifest(right_run_id)
        left_symbols = self._portfolio_symbols(left_run_id)
        right_symbols = self._portfolio_symbols(right_run_id)
        horizons = sorted(
            {str(key) for key in left.get("performance", {})}
            | {str(key) for key in right.get("performance", {})},
            key=lambda value: int(value),
        )
        performance: dict[str, dict[str, Any]] = {}
        for horizon in horizons:
            left_result = left.get("performance", {}).get(horizon, {})
            right_result = right.get("performance", {}).get(horizon, {})
            performance[horizon] = {
                "left_status": left_result.get("status"),
                "right_status": right_result.get("status"),
                "left_total_return": left_result.get("total_return"),
                "right_total_return": right_result.get("total_return"),
                "total_return_delta": _delta(left_result.get("total_return"), right_result.get("total_return")),
                "left_max_drawdown": left_result.get("max_drawdown"),
                "right_max_drawdown": right_result.get("max_drawdown"),
                "max_drawdown_delta": _delta(left_result.get("max_drawdown"), right_result.get("max_drawdown")),
            }
        left_portfolios = left.get("portfolio_performance", {"strategy": left.get("performance", {})})
        right_portfolios = right.get("portfolio_performance", {"strategy": right.get("performance", {})})
        portfolio_ids = sorted(set(left_portfolios) | set(right_portfolios))
        left_portfolio_symbols = self._portfolio_symbols_by_id(left_run_id, left_portfolios)
        right_portfolio_symbols = self._portfolio_symbols_by_id(right_run_id, right_portfolios)
        portfolio_comparison: dict[str, dict[str, Any]] = {}
        for portfolio_id in portfolio_ids:
            left_results = left_portfolios.get(portfolio_id, {})
            right_results = right_portfolios.get(portfolio_id, {})
            portfolio_horizons = sorted(
                {str(key) for key in left_results} | {str(key) for key in right_results},
                key=lambda value: int(value),
            )
            portfolio_comparison[portfolio_id] = {
                horizon: {
                    "left_initial_cash": left_results.get(horizon, {}).get("initial_cash"),
                    "right_initial_cash": right_results.get(horizon, {}).get("initial_cash"),
                    "left_profit_loss": left_results.get(horizon, {}).get("profit_loss"),
                    "right_profit_loss": right_results.get(horizon, {}).get("profit_loss"),
                    "profit_loss_delta": _delta(
                        left_results.get(horizon, {}).get("profit_loss"),
                        right_results.get(horizon, {}).get("profit_loss"),
                    ),
                    "left_total_return": left_results.get(horizon, {}).get("total_return"),
                    "right_total_return": right_results.get(horizon, {}).get("total_return"),
                    "total_return_delta": _delta(
                        left_results.get(horizon, {}).get("total_return"),
                        right_results.get(horizon, {}).get("total_return"),
                    ),
                    "left_max_drawdown": left_results.get(horizon, {}).get("max_drawdown"),
                    "right_max_drawdown": right_results.get(horizon, {}).get("max_drawdown"),
                    "max_drawdown_delta": _delta(
                        left_results.get(horizon, {}).get("max_drawdown"),
                        right_results.get(horizon, {}).get("max_drawdown"),
                    ),
                }
                for horizon in portfolio_horizons
            }
        return {
            "left": _identity(left),
            "right": _identity(right),
            "portfolio": {
                "left": left_symbols,
                "right": right_symbols,
                "added": sorted(set(right_symbols) - set(left_symbols)),
                "removed": sorted(set(left_symbols) - set(right_symbols)),
            },
            "portfolio_symbols": {
                portfolio_id: {
                    "left": left_portfolio_symbols.get(portfolio_id, []),
                    "right": right_portfolio_symbols.get(portfolio_id, []),
                    "added": sorted(
                        set(right_portfolio_symbols.get(portfolio_id, []))
                        - set(left_portfolio_symbols.get(portfolio_id, []))
                    ),
                    "removed": sorted(
                        set(left_portfolio_symbols.get(portfolio_id, []))
                        - set(right_portfolio_symbols.get(portfolio_id, []))
                    ),
                }
                for portfolio_id in sorted(
                    set(left_portfolio_symbols) | set(right_portfolio_symbols) | set(portfolio_ids)
                )
            },
            "performance": performance,
            "portfolios": portfolio_comparison,
        }

    def _run_dir(self, run_id: str) -> Path:
        value = str(run_id).strip()
        if not value or Path(value).name != value or value in {".", ".."}:
            raise ValueError("运行 ID 无效")
        run_dir = (self.runs_dir / value).resolve()
        if run_dir.parent != self.runs_dir:
            raise ValueError("运行 ID 无效")
        return run_dir

    def _portfolio_symbols(self, run_id: str) -> list[str]:
        portfolio_path = self._run_dir(run_id) / "portfolio.csv"
        if not portfolio_path.is_file():
            return []
        frame = pd.read_csv(portfolio_path, dtype={"symbol": "string"})
        if "symbol" not in frame.columns:
            return []
        return frame["symbol"].dropna().astype(str).tolist()

    def _portfolio_symbols_by_id(
        self,
        run_id: str,
        performance: dict[str, Any],
    ) -> dict[str, list[str]]:
        """读取每个组合的冻结持仓；兼容早期只有 primary 产物的运行。"""
        aggregate_path = self._run_dir(run_id) / "portfolios.csv"
        if aggregate_path.is_file():
            frame = pd.read_csv(aggregate_path, dtype={"symbol": "string", "portfolio_id": "string"})
            if {"portfolio_id", "symbol"}.issubset(frame.columns):
                return {
                    str(portfolio_id): group["symbol"].dropna().astype(str).tolist()
                    for portfolio_id, group in frame.groupby("portfolio_id", sort=False)
                }
        return {str(portfolio_id): self._portfolio_symbols(run_id) for portfolio_id in performance}


def _portfolio_summaries(manifest: dict[str, Any], performance: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    configs = {str(item.get("portfolio_id")): item for item in manifest.get("portfolios", [])}
    portfolio_ids = list(dict.fromkeys(list(configs) + [str(portfolio_id) for portfolio_id in performance]))
    summaries = []
    for portfolio_id in portfolio_ids:
        results = performance.get(portfolio_id, {})
        config = configs.get(str(portfolio_id), {})
        summaries.append(
            {
                "portfolio_id": str(portfolio_id),
                "name": config.get("name", portfolio_id),
                "initial_cash": config.get("initial_cash"),
                "performance": {
                    str(horizon): {
                        "status": result.get("status"),
                        "initial_cash": result.get("initial_cash"),
                        "ending_equity": result.get("ending_equity"),
                        "profit_loss": result.get("profit_loss"),
                        "total_return": result.get("total_return"),
                        "max_drawdown": result.get("max_drawdown"),
                        "max_drawdown_amount": result.get("max_drawdown_amount"),
                        "daily_volatility": result.get("daily_volatility"),
                        "annualized_return": result.get("annualized_return"),
                        "annualized_volatility": result.get("annualized_volatility"),
                        "sharpe": result.get("sharpe"),
                        "commission_paid": result.get("commission_paid"),
                        "slippage_paid": result.get("slippage_paid"),
                        "cash_residual": result.get("cash_residual"),
                    }
                    for horizon, result in results.items()
                },
            }
        )
    return summaries


def _identity(manifest: dict[str, Any]) -> dict[str, Any]:
    spec = manifest.get("spec", {})
    return {
        "run_id": manifest.get("run_id"),
        "requested_date": manifest.get("requested_date"),
        "signal_date": manifest.get("signal_date"),
        "rule_version": manifest.get("rule_version"),
        "top_n": spec.get("top_n"),
    }


def _delta(left: Any, right: Any) -> float | None:
    if left is None or right is None:
        return None
    try:
        return float(right) - float(left)
    except (TypeError, ValueError):
        return None
