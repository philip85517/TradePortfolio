"""自动发现并选择历史研究可用的本地行情数据库。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Callable, Iterable, Sequence


class DataBindingError(RuntimeError):
    """Raised when no compatible research data source can be bound."""


@dataclass(frozen=True)
class ResearchDataBinding:
    """描述一次自动绑定结果，不持有数据库连接。"""

    db_path: Path
    rows: int
    symbols: int
    min_date: date | None
    max_date: date | None
    adjustment_counts: dict[str, int]
    source_counts: dict[str, int]
    source_dataset_counts: dict[str, int]
    data_fingerprint: str
    coverage_status: str
    point_in_time_universe: bool
    point_in_time_industry: bool = False
    universe_db_path: Path | None = None
    universe_source: str | None = None
    universe_snapshot_id: str | None = None
    universe_provisioning: str | None = None
    provisioning: str | None = None

    def to_dict(self) -> dict[str, object]:
        """返回可直接写入研究 manifest 的来源摘要。"""

        return {
            "db_path": str(self.db_path),
            "source": "local-duckdb",
            "rows": self.rows,
            "symbols": self.symbols,
            "min_date": self.min_date.isoformat() if self.min_date else None,
            "max_date": self.max_date.isoformat() if self.max_date else None,
            "adjustment_counts": dict(self.adjustment_counts),
            "source_counts": dict(self.source_counts),
            "source_dataset_counts": dict(self.source_dataset_counts),
            "data_fingerprint": self.data_fingerprint,
            "coverage_status": self.coverage_status,
            "point_in_time_universe": self.point_in_time_universe,
            "point_in_time_industry": self.point_in_time_industry,
            "point_in_time_quality": (
                "complete"
                if self.point_in_time_universe and self.point_in_time_industry
                else "listing-only"
                if self.point_in_time_universe
                else "none"
            ),
            "universe_db_path": str(self.universe_db_path) if self.universe_db_path else None,
            "universe_source": self.universe_source,
            "universe_snapshot_id": self.universe_snapshot_id,
            "universe_provisioning": self.universe_provisioning,
            "provisioning": self.provisioning,
        }


_REQUIRED_COLUMNS = {
    "market",
    "symbol",
    "timeframe",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
}
_DATABASE_NAMES = ("market_data.duckdb", "stock_market_data_2021.duckdb")


def auto_bind_research_db(
    db_path: str | Path | None = None,
    *,
    candidate_paths: Sequence[str | Path] | None = None,
    market: str = "a_share",
    start_date: date | None = None,
    end_date: date | None = None,
) -> ResearchDataBinding:
    """自动绑定一个已有的、只读的研究行情库。

    ``db_path=None`` 或 ``db_path='auto'`` 会按候选路径和覆盖范围选择数据库；
    传入具体路径则只校验并返回该路径。此函数不会创建、修改或导入数据库。
    """

    if db_path is not None and str(db_path).strip().lower() not in {"", "auto"}:
        path = Path(db_path).expanduser().resolve()
        binding = _inspect_database(path, market, start_date, end_date)
        if binding is None:
            raise DataBindingError(f"行情数据库不兼容或缺少 market_ohlcv: {path}")
        return binding

    paths = _candidate_paths(candidate_paths)
    inspected: list[tuple[int, ResearchDataBinding]] = []
    failures: list[str] = []
    for index, path in enumerate(paths):
        binding = _inspect_database(path, market, start_date, end_date)
        if binding is not None:
            inspected.append((index, binding))
        else:
            failures.append(str(path))
    if not inspected:
        searched = ", ".join(failures) if failures else "无候选路径"
        raise DataBindingError(f"未发现可用的 market_ohlcv 研究数据库；已检查: {searched}")

    _, selected = max(inspected, key=lambda item: _ranking_key(item[0], item[1], market))
    return selected


def ensure_research_data(
    db_path: str | Path | None = None,
    *,
    candidate_paths: Sequence[str | Path] | None = None,
    cache_path: str | Path | None = None,
    market: str = "a_share",
    start_date: date,
    end_date: date,
    updater: Callable[[Path, str, date, date], None] | None = None,
    require_point_in_time: bool = False,
    universe_cache_path: str | Path | None = None,
    universe_updater: Callable[[Path], None] | None = None,
) -> ResearchDataBinding:
    """确保研究窗口有行情；缺覆盖时自动调用既有 updater。

    自动模式下，完整覆盖的现有数据库只读复用；否则将数据补到当前项目的
    `etf_strategy/data/processed/market_data.duckdb`（或显式 ``cache_path``）。
    显式传入不完整数据库时不会悄悄改写该数据库，而是直接报告覆盖不足。
    """

    is_auto = db_path is None or str(db_path).strip().lower() in {"", "auto"}
    try:
        binding = auto_bind_research_db(
            db_path=db_path,
            candidate_paths=candidate_paths,
            market=market,
            start_date=start_date,
            end_date=end_date,
        )
    except DataBindingError:
        if not is_auto:
            raise
        binding = None
    if binding is not None and binding.coverage_status == "complete":
        return _ensure_point_in_time_universe(
            binding,
            market=market,
            as_of=start_date,
            required=require_point_in_time,
            universe_cache_path=universe_cache_path,
            universe_updater=universe_updater,
        )
    if not is_auto:
        path = binding.db_path if binding is not None else Path(db_path).expanduser()
        raise DataBindingError(
            f"指定行情数据库覆盖不足: {path} "
            f"（需要 {start_date.isoformat()} → {end_date.isoformat()}）"
        )

    target = Path(cache_path).expanduser().resolve() if cache_path else default_research_cache_path()
    try:
        if updater is None:
            _run_default_updater(target, market, start_date, end_date)
        else:
            updater(target, market, start_date, end_date)
    except Exception as exc:  # noqa: BLE001 - preserve provider/updater failure context.
        raise DataBindingError(f"自动补数失败: {exc}") from exc

    rebound = auto_bind_research_db(
        candidate_paths=[target, *(_candidate_paths(candidate_paths) if candidate_paths is not None else default_research_db_candidates())],
        market=market,
        start_date=start_date,
        end_date=end_date,
    )
    if rebound.coverage_status != "complete":
        raise DataBindingError(
            f"自动补数后仍未覆盖研究窗口: {rebound.db_path} "
            f"（实际 {rebound.min_date} → {rebound.max_date}，需要 {start_date} → {end_date}）"
        )
    return _ensure_point_in_time_universe(
        replace(rebound, provisioning="etf_strategy.update_stock_data"),
        market=market,
        as_of=start_date,
        required=require_point_in_time,
        universe_cache_path=universe_cache_path,
        universe_updater=universe_updater,
    )


def default_research_db_candidates() -> tuple[Path, ...]:
    """返回本项目和同机 AlphaLab 旧项目的默认数据库候选。"""

    repository_root = Path(__file__).resolve().parents[2]
    roots = (
        repository_root / "etf_strategy" / "data" / "processed",
        Path.home() / "Documents" / "alphaLab" / "etf_strategy" / "data" / "processed",
    )
    return tuple(root / name for root in roots for name in _DATABASE_NAMES)


def default_research_cache_path() -> Path:
    """返回当前项目用于自动补数的 DuckDB 路径。"""

    repository_root = Path(__file__).resolve().parents[2]
    return repository_root / "etf_strategy" / "data" / "processed" / "market_data.duckdb"


def default_research_universe_cache_path() -> Path:
    """返回当前项目用于自动补齐 PIT universe 的 sidecar 路径。"""

    return default_research_cache_path().with_name("market_universe_history.duckdb")


def _candidate_paths(candidate_paths: Sequence[str | Path] | None) -> tuple[Path, ...]:
    paths = candidate_paths if candidate_paths is not None else default_research_db_candidates()
    unique: dict[str, Path] = {}
    for value in paths:
        path = Path(value).expanduser().resolve()
        unique[str(path)] = path
    return tuple(unique.values())


def _inspect_database(
    path: Path,
    market: str,
    start_date: date | None,
    end_date: date | None,
) -> ResearchDataBinding | None:
    if not path.is_file():
        return None
    try:
        import duckdb

        with duckdb.connect(str(path), read_only=True) as con:
            tables = {str(row[0]) for row in con.execute("SHOW TABLES").fetchall()}
            if "market_ohlcv" not in tables:
                return None
            columns = {str(row[0]) for row in con.execute("DESCRIBE market_ohlcv").fetchall()}
            if not _REQUIRED_COLUMNS.issubset(columns):
                return None
            summary = con.execute(
                """
                SELECT count(*), count(DISTINCT symbol), min(trade_date), max(trade_date)
                FROM market_ohlcv
                WHERE market = ? AND timeframe = '1d'
                """,
                [market],
            ).fetchone()
            rows = int(summary[0] or 0)
            if rows == 0:
                return None
            adjustment_counts = {
                str(adjustment or "unknown"): int(count)
                for adjustment, count in con.execute(
                    """
                    SELECT adjustment, count(*)
                    FROM market_ohlcv
                    WHERE market = ? AND timeframe = '1d'
                    GROUP BY adjustment
                    """,
                    [market],
                ).fetchall()
            }
            source_counts = _grouped_counts(con, columns, "source", market)
            source_dataset_counts = _grouped_counts(con, columns, "source_dataset_id", market)
            pit = _has_point_in_time_universe(con, tables)
            pit_industry = _has_point_in_time_industry(con, tables)
    except Exception:
        return None

    min_value = _as_date(summary[2])
    max_value = _as_date(summary[3])
    data_fingerprint = _data_fingerprint(
        path,
        rows=rows,
        symbols=int(summary[1] or 0),
        min_date=min_value,
        max_date=max_value,
        adjustment_counts=adjustment_counts,
        source_counts=source_counts,
        source_dataset_counts=source_dataset_counts,
    )
    if start_date is None or end_date is None:
        coverage_status = "complete"
    elif min_value is None or max_value is None:
        coverage_status = "none"
    elif min_value <= start_date and max_value >= end_date:
        coverage_status = "complete"
    else:
        coverage_status = "partial"
    return ResearchDataBinding(
        db_path=path,
        rows=rows,
        symbols=int(summary[1] or 0),
        min_date=min_value,
        max_date=max_value,
        adjustment_counts=adjustment_counts,
        source_counts=source_counts,
        source_dataset_counts=source_dataset_counts,
        data_fingerprint=data_fingerprint,
        coverage_status=coverage_status,
        point_in_time_universe=pit,
        point_in_time_industry=pit_industry,
        universe_db_path=path if pit else None,
    )


def _has_point_in_time_universe(con, tables: Iterable[str]) -> bool:
    if "market_universe_history" in tables:
        return True
    if "market_universe" not in tables:
        return False
    columns = {str(row[0]) for row in con.execute("DESCRIBE market_universe").fetchall()}
    return {"effective_from", "effective_to"}.issubset(columns)


def _has_point_in_time_industry(con, tables: Iterable[str]) -> bool:
    table = "market_universe_history" if "market_universe_history" in tables else "market_universe"
    if table not in tables:
        return False
    columns = {str(row[0]) for row in con.execute(f"DESCRIBE {table}").fetchall()}
    required = {"industry_level1", "industry_level2", "industry_level3"}
    if not required.issubset(columns):
        return False
    return bool(
        con.execute(
            f"""
            SELECT count(*)
            FROM {table}
            WHERE industry_level1 IS NOT NULL
              AND industry_level2 IS NOT NULL
              AND industry_level3 IS NOT NULL
            """
        ).fetchone()[0]
    )


def _ensure_point_in_time_universe(
    binding: ResearchDataBinding,
    *,
    market: str,
    as_of: date,
    required: bool,
    universe_cache_path: str | Path | None,
    universe_updater: Callable[[Path], None] | None,
) -> ResearchDataBinding:
    if not required or binding.point_in_time_universe:
        return binding
    target = (
        Path(universe_cache_path).expanduser().resolve()
        if universe_cache_path
        else default_research_universe_cache_path()
    )
    try:
        if universe_updater is None:
            _run_default_universe_updater(target)
        else:
            universe_updater(target)
        from .universe_history import load_universe_as_of

        history = load_universe_as_of(target, as_of, market)
        if history.empty:
            raise DataBindingError(f"自动生成的 PIT universe 没有 {market} 在 {as_of} 生效的记录")
        snapshots = sorted({str(value) for value in history["snapshot_id"].dropna()})
        has_industry = bool(
            {"industry_level1", "industry_level2", "industry_level3"}.issubset(history.columns)
            and history[["industry_level1", "industry_level2", "industry_level3"]].notna().all(axis=1).all()
        )
    except Exception as exc:  # noqa: BLE001 - preserve source/provider context.
        if isinstance(exc, DataBindingError):
            raise
        raise DataBindingError(f"自动生成 PIT universe 失败: {exc}") from exc
    return replace(
        binding,
        point_in_time_universe=True,
        point_in_time_industry=has_industry,
        universe_db_path=target,
        universe_source="baostock",
        universe_snapshot_id=snapshots[0] if len(snapshots) == 1 else "mixed",
        universe_provisioning="baostock.query_stock_basic",
    )


def _grouped_counts(con, columns: set[str], column: str, market: str) -> dict[str, int]:
    if column not in columns:
        return {}
    quoted = '"' + column.replace('"', '""') + '"'
    return {
        str(value or "unknown"): int(count)
        for value, count in con.execute(
            f"""
            SELECT {quoted}, count(*)
            FROM market_ohlcv
            WHERE market = ? AND timeframe = '1d'
            GROUP BY {quoted}
            """,
            [market],
        ).fetchall()
    }


def _data_fingerprint(
    path: Path,
    *,
    rows: int,
    symbols: int,
    min_date: date | None,
    max_date: date | None,
    adjustment_counts: dict[str, int],
    source_counts: dict[str, int],
    source_dataset_counts: dict[str, int],
) -> str:
    stat = path.stat()
    payload = {
        "path": str(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "rows": rows,
        "symbols": symbols,
        "min_date": min_date.isoformat() if min_date else None,
        "max_date": max_date.isoformat() if max_date else None,
        "adjustment_counts": adjustment_counts,
        "source_counts": source_counts,
        "source_dataset_counts": source_dataset_counts,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _run_default_updater(target: Path, market: str, start_date: date, end_date: date) -> None:
    if market != "a_share":
        raise DataBindingError(f"当前 V1 自动补数仅支持 a_share，收到: {market}")
    repository_root = Path(__file__).resolve().parents[2]
    script = repository_root / "etf_strategy" / "scripts" / "update_stock_data.py"
    if not script.is_file():
        raise DataBindingError(f"找不到既有行情 updater: {script}")
    command = [
        sys.executable,
        str(script),
        "--db",
        str(target),
        "--markets",
        market,
        "--timeframes",
        "1d",
        "--start-date",
        start_date.isoformat(),
        "--end-date",
        end_date.isoformat(),
        "--adjust",
        "qfq",
        "--all",
    ]
    try:
        completed = subprocess.run(command, check=True, text=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "provider 无输出").strip()
        raise RuntimeError(detail[-2000:]) from exc
    if completed.returncode != 0:  # pragma: no cover - check=True handles this branch.
        raise RuntimeError((completed.stderr or "updater 返回非零状态").strip())


def _run_default_universe_updater(target: Path) -> None:
    from .universe_history import fetch_baostock_universe_history

    fetch_baostock_universe_history(db_path=target)


def _ranking_key(index: int, binding: ResearchDataBinding, market: str) -> tuple:
    qfq_rows = binding.adjustment_counts.get("qfq", 0)
    qfq_ratio = qfq_rows / binding.rows if binding.rows else 0.0
    max_date = binding.max_date or date.min
    return (
        binding.coverage_status == "complete",
        qfq_ratio if market == "a_share" else 0.0,
        max_date,
        binding.rows,
        -index,
    )


def _as_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


__all__ = [
    "DataBindingError",
    "ResearchDataBinding",
    "auto_bind_research_db",
    "default_research_cache_path",
    "default_research_universe_cache_path",
    "default_research_db_candidates",
    "ensure_research_data",
]
