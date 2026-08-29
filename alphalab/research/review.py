"""固定历史研究产物的只读审阅服务。

审阅服务与研究运行解耦：它只读取一次实验产生的 CSV/JSON 快照和行情
DuckDB，不会重跑因子、不修改产物，也不会写入模拟交易账本。
"""

from __future__ import annotations

import json
import mimetypes
from dataclasses import dataclass, field
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd

from .engine import DuckDBMarketDataAdapter

STATIC_ROOT = Path(__file__).resolve().parent / "static"
REQUIRED_ARTIFACTS = (
    "manifest.json",
    "candidates.csv",
    "portfolio.csv",
    "nav.csv",
    "portfolio_returns.csv",
)


@dataclass(frozen=True)
class ReviewRun:
    run_dir: Path
    manifest: dict[str, Any]
    candidates_frame: pd.DataFrame
    portfolio_frame: pd.DataFrame
    nav_frame: pd.DataFrame
    portfolio_returns_frame: pd.DataFrame
    benchmark_nav_frame: pd.DataFrame = field(default_factory=pd.DataFrame)


def load_review_run(runs_dir: str | Path, run_id: str) -> ReviewRun:
    """加载并校验一个冻结研究运行，拒绝路径穿越和不完整产物。"""
    base = Path(runs_dir).expanduser().resolve()
    run_id = str(run_id).strip()
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("运行 ID 无效")
    run_dir = (base / run_id).resolve()
    if run_dir.parent != base:
        raise ValueError("运行 ID 无效")
    if not run_dir.is_dir():
        raise FileNotFoundError(f"研究运行不存在: {run_id}")
    missing = [name for name in REQUIRED_ARTIFACTS if not (run_dir / name).is_file()]
    if missing:
        raise ValueError(f"研究运行产物不完整，缺少: {', '.join(missing)}")
    try:
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("manifest.json 无法读取") from exc
    if manifest.get("run_id") != run_id:
        raise ValueError("manifest.json 中的运行 ID 与目录不一致")

    candidates = pd.read_csv(run_dir / "candidates.csv", dtype={"symbol": "string"})
    if "symbol" not in candidates.columns:
        raise ValueError("候选产物缺少 symbol 列")
    if "selected" not in candidates.columns:
        candidates["selected"] = False
    candidates["selected"] = candidates["selected"].map(_as_bool).fillna(False)
    portfolio = pd.read_csv(run_dir / "portfolio.csv", dtype={"symbol": "string"})
    if "target_weight" not in candidates.columns:
        weights = portfolio.set_index("symbol").get("target_weight", pd.Series(dtype=float))
        candidates["target_weight"] = candidates["symbol"].map(weights)
    nav = pd.read_csv(run_dir / "nav.csv")
    portfolio_returns = pd.read_csv(run_dir / "portfolio_returns.csv", dtype={"symbol": "string"})
    benchmark_path = run_dir / "benchmark_nav.csv"
    benchmark_nav = pd.read_csv(benchmark_path) if benchmark_path.is_file() else pd.DataFrame()
    return ReviewRun(run_dir, manifest, candidates, portfolio, nav, portfolio_returns, benchmark_nav)


class ReviewState:
    """为页面提供只读的候选、行情和绩效查询。"""

    def __init__(self, run: ReviewRun, db_path: str | Path):
        self.run = run
        self.db_path = Path(db_path).expanduser()
        self.market = str(run.manifest.get("spec", {}).get("market", "a_share"))
        self.signal_date = date.fromisoformat(str(run.manifest["signal_date"]))
        horizons = run.manifest.get("spec", {}).get("horizons", [21, 42])
        self.horizons = tuple(sorted({int(h) for h in horizons})) or (21, 42)

    def summary(self) -> dict[str, Any]:
        candidates = self.run.candidates_frame
        diagnostics = self.run.manifest.get("diagnostics", {})
        funnel = diagnostics.get("funnel", {})
        return {
            "run_id": self.run.manifest.get("run_id"),
            "market": self.market,
            "requested_date": self.run.manifest.get("requested_date"),
            "signal_date": self.run.manifest.get("signal_date"),
            "universe_mode": diagnostics.get("universe_mode", "observed-history"),
            "universe": diagnostics.get("universe", {}),
            "rule_version": self.run.manifest.get("rule_version"),
            "rule_source_hash": self.run.manifest.get("rule_source_hash"),
            "source_hash": self.run.manifest.get("source_hash"),
            "spec": self.run.manifest.get("spec", {}),
            "funnel": funnel,
            "candidate_count": int(len(candidates)),
            "eligible_count": int(candidates.get("eligible", pd.Series(dtype=bool)).fillna(False).sum()),
            "selected_count": int(candidates["selected"].sum()),
            "top_symbols": self.run.portfolio_frame.get("symbol", pd.Series(dtype=str)).astype(str).tolist(),
            "data_range": diagnostics.get("data_range"),
            "data_quality": diagnostics.get("data_quality", {}),
            "industries": self.industries(),
        }

    def industries(self) -> list[str]:
        if "industry" not in self.run.candidates_frame.columns:
            return []
        values = self.run.candidates_frame["industry"].dropna().astype(str).str.strip()
        return sorted({value for value in values if value and value.lower() != "nan"})

    def reasons(self) -> list[str]:
        if "reason" not in self.run.candidates_frame.columns:
            return []
        values: set[str] = set()
        for reason in self.run.candidates_frame["reason"].dropna().astype(str):
            values.update(part.strip() for part in reason.split("；") if part.strip())
        return sorted(values)

    def portfolio_detail(self) -> dict[str, Any]:
        """返回冻结运行的组合路径和前瞻指标，不重新计算研究结果。"""
        performance = self.run.manifest.get("performance", {})
        benchmark_performance = self.run.manifest.get("benchmark", {})
        comparison = {
            str(horizon): {
                "total_return_delta": _delta(
                    result.get("total_return"),
                    benchmark_performance.get(str(horizon), {}).get("total_return"),
                ),
                "max_drawdown_delta": _delta(
                    result.get("max_drawdown"),
                    benchmark_performance.get(str(horizon), {}).get("max_drawdown"),
                ),
            }
            for horizon, result in performance.items()
        }
        nav = self.run.nav_frame.copy()
        if not nav.empty and "horizon" in nav.columns:
            nav["horizon"] = pd.to_numeric(nav["horizon"], errors="coerce").astype("Int64")
        return {
            "status": "OK" if not self.run.portfolio_frame.empty else "EMPTY_PORTFOLIO",
            "signal_date": self.signal_date.isoformat(),
            "entry_date": self.run.manifest.get("diagnostics", {}).get("entry_date"),
            "horizons": sorted({int(key) for key in performance}) or list(self.horizons),
            "performance": _jsonable(performance),
            "holdings": _records(self.run.portfolio_frame),
            "nav": _records(nav),
            "benchmark_performance": _jsonable(benchmark_performance),
            "comparison": _jsonable(comparison),
            "benchmark_nav": _records(self.run.benchmark_nav_frame),
        }

    def candidates(
        self,
        *,
        search: str = "",
        status: str = "all",
        industry: str = "all",
        reason: str = "all",
    ) -> list[dict[str, Any]]:
        frame = self.run.candidates_frame.copy()
        selected = frame["selected"].fillna(False).astype(bool)
        eligible = frame.get("eligible", pd.Series(False, index=frame.index)).fillna(False).astype(bool)
        status = status.strip().lower() or "all"
        if status == "selected":
            frame = frame[selected]
        elif status == "eligible":
            frame = frame[eligible]
        elif status in {"excluded", "ineligible"}:
            frame = frame[~eligible]
        elif status != "all":
            raise ValueError("status 必须是 all、selected、eligible 或 excluded")
        if industry and industry.lower() != "all":
            industry_values = frame["industry"] if "industry" in frame.columns else pd.Series("UNKNOWN", index=frame.index)
            frame = frame[industry_values.astype(str) == industry]
        if reason and reason.lower() != "all":
            reason_values = frame["reason"] if "reason" in frame.columns else pd.Series("", index=frame.index)
            frame = frame[reason_values.fillna("").astype(str).map(lambda value: reason in value.split("；"))]
        search = search.strip().casefold()
        if search:
            searchable = [column for column in ["symbol", "name", "industry"] if column in frame.columns]
            mask = pd.Series(False, index=frame.index)
            for column in searchable:
                mask |= frame[column].fillna("").astype(str).str.casefold().str.contains(search, regex=False)
            frame = frame[mask]
        frame["_rank_sort"] = pd.to_numeric(frame.get("rank"), errors="coerce")
        frame = frame.sort_values(["_rank_sort", "symbol"], ascending=[True, True], na_position="last", kind="mergesort")
        frame = frame.drop(columns=["_rank_sort"])
        return _records(frame)

    def stock_detail(self, symbol: str, mode: str = "selection") -> dict[str, Any]:
        symbol = str(symbol).strip()
        mode = str(mode).strip().lower() or "selection"
        if mode not in {"selection", "evaluation"}:
            raise ValueError("mode 必须是 selection 或 evaluation")
        rows = self.run.candidates_frame[self.run.candidates_frame["symbol"].astype(str) == symbol]
        if rows.empty:
            raise KeyError(f"候选股票不存在: {symbol}")
        candidate = _records(rows.iloc[[0]])[0]
        bars = self._load_bars(symbol)
        if bars.empty:
            return {
                "status": "NO_CHART_DATA",
                "symbol": symbol,
                "mode": mode,
                "signal_date": self.signal_date.isoformat(),
                "candidate": candidate,
                "rows": [],
                "markers": {"signal_date": self.signal_date.isoformat()},
                "performance": {},
                "portfolio_performance": {},
            }

        bars = bars.sort_values("date").reset_index(drop=True)
        if mode == "selection":
            bars = bars[bars["date"] <= pd.Timestamp(self.signal_date)].copy()
        else:
            end_date = self.signal_date + timedelta(days=max(self.horizons) * 3 + 15)
            bars = bars[bars["date"] <= pd.Timestamp(end_date)].copy()
        if not bars.empty:
            bars["date"] = pd.to_datetime(bars["date"]).dt.date
        portfolio_rows = self.run.portfolio_frame[
            self.run.portfolio_frame.get("symbol", pd.Series(dtype=str)).astype(str) == symbol
        ]
        markers: dict[str, Any] = {"signal_date": self.signal_date.isoformat()}
        performance: dict[str, Any] = {}
        portfolio_performance: dict[str, Any] = {}
        portfolio_payload: dict[str, Any] | None = None
        if mode == "evaluation":
            entry_date = _date_text(portfolio_rows.iloc[0].get("entry_date")) if not portfolio_rows.empty else None
            markers["entry_date"] = entry_date
            for horizon, result in self.run.manifest.get("performance", {}).items():
                if result.get("status") == "COMPLETE":
                    markers[f"horizon_{horizon}_date"] = result.get("evaluated_date")
                stock_return = (result.get("stock_returns") or {}).get(symbol)
                contribution = (result.get("stock_contributions") or {}).get(symbol)
                performance[str(horizon)] = {
                    "horizon": result.get("horizon", int(horizon)),
                    "status": result.get("status"),
                    "stock_return": stock_return,
                    "contribution": contribution,
                    "evaluated_date": result.get("evaluated_date"),
                }
                portfolio_performance[str(horizon)] = {
                    "horizon": result.get("horizon", int(horizon)),
                    "status": result.get("status"),
                    "total_return": result.get("total_return"),
                    "gross_return": result.get("gross_return"),
                    "max_drawdown": result.get("max_drawdown"),
                    "holding_win_rate": result.get("holding_win_rate"),
                    "evaluated_date": result.get("evaluated_date"),
                }
            portfolio_payload = _records(portfolio_rows)[0] if not portfolio_rows.empty else None
        return {
            "status": "OK" if not bars.empty else "NO_SELECTION_DATA",
            "symbol": symbol,
            "mode": mode,
            "signal_date": self.signal_date.isoformat(),
            "candidate": candidate,
            "portfolio": portfolio_payload,
            "rows": _records(bars),
            "markers": markers,
            "performance": performance,
            "portfolio_performance": portfolio_performance,
        }

    def _load_bars(self, symbol: str) -> pd.DataFrame:
        adapter = DuckDBMarketDataAdapter(self.db_path)
        start = self.signal_date - timedelta(days=450)
        end = self.signal_date + timedelta(days=max(self.horizons) * 3 + 15)
        bars = adapter.load(start, end, market=self.market, symbols=[symbol])
        if bars.empty:
            return bars
        bars = bars[bars["symbol"].astype(str) == symbol].copy()
        columns = ["date", "open", "high", "low", "close", "volume", "amount"]
        for column in columns:
            if column not in bars.columns:
                bars[column] = np.nan
        return bars[columns]


class ReviewRequestHandler(BaseHTTPRequestHandler):
    review_state: ReviewState

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook.
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self._send_file(STATIC_ROOT / "index.html")
            elif parsed.path in {"/app.js", "/styles.css"}:
                self._send_file(STATIC_ROOT / parsed.path.removeprefix("/"))
            elif parsed.path == "/api/health":
                self._send_json({"ok": True})
            elif parsed.path == "/api/summary":
                self._send_json(self.review_state.summary())
            elif parsed.path == "/api/portfolio":
                self._send_json(self.review_state.portfolio_detail())
            elif parsed.path == "/api/candidates":
                params = parse_qs(parsed.query)
                self._send_json(
                    {
                        "rows": self.review_state.candidates(
                            search=_first(params, "search"),
                            status=_first(params, "status", "all"),
                            industry=_first(params, "industry", "all"),
                            reason=_first(params, "reason", "all"),
                        ),
                        "industries": self.review_state.industries(),
                        "reasons": self.review_state.reasons(),
                    }
                )
            elif parsed.path == "/api/stock":
                params = parse_qs(parsed.query)
                symbol = _first(params, "symbol")
                if not symbol:
                    raise ValueError("symbol 参数必填")
                self._send_json(self.review_state.stock_detail(symbol, _first(params, "mode", "selection")))
            else:
                self._send_json({"error": "Not found"}, status=404)
        except KeyError as exc:
            self._send_json({"error": str(exc)}, status=404)
        except (FileNotFoundError, ValueError) as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:  # noqa: BLE001 - UI receives an actionable error.
            self._send_json({"error": str(exc)}, status=500)

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._send_json({"error": "静态资源不存在"}, status=404)
            return
        body = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{mime}; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(_jsonable(payload), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def create_review_server(
    state: ReviewState,
    host: str = "127.0.0.1",
    port: int = 0,
) -> ThreadingHTTPServer:
    """创建审阅 HTTP server；默认端口 0 便于测试。"""
    class Handler(ReviewRequestHandler):
        review_state = state

    return ThreadingHTTPServer((host, port), Handler)


def serve_review(
    run_id: str,
    runs_dir: str | Path,
    db_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 8787,
) -> None:
    state = ReviewState(load_review_run(runs_dir, run_id), db_path)
    server = create_review_server(state, host, port)
    print(f"[research-review] http://{host}:{server.server_address[1]} | run {run_id}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _first(params: dict[str, list[str]], key: str, default: str = "") -> str:
    values = params.get(key, [default])
    return values[0] if values else default


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "是"}


def _date_text(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date().isoformat()


def _delta(left: Any, right: Any) -> float | None:
    if left is None or right is None:
        return None
    try:
        return float(left) - float(right)
    except (TypeError, ValueError):
        return None


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [_jsonable(row) for row in frame.to_dict("records")]


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (pd.Timestamp, date)):
        return value.isoformat()
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value
