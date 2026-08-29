"""可审计的历史 universe 区间及按日期读取。"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd


class UniverseHistoryError(ValueError):
    """Raised when historical universe data violates its contract."""


HISTORY_COLUMNS = (
    "market",
    "symbol",
    "effective_from",
    "effective_to",
    "status",
    "name",
    "industry_level1",
    "industry_level2",
    "industry_level3",
    "source",
    "snapshot_id",
    "source_recorded_at",
)
INDUSTRY_SNAPSHOT_COLUMNS = (
    "market",
    "symbol",
    "industry_level1",
    "industry_level2",
    "industry_level3",
    "source",
    "snapshot_id",
    "source_recorded_at",
)
REQUIRED_COLUMNS = {
    "market",
    "symbol",
    "effective_from",
    "effective_to",
    "status",
    "source",
    "snapshot_id",
}
INDUSTRY_REQUIRED_COLUMNS = {"market", "symbol", "source", "snapshot_id"}


def normalize_universe_history(
    frame: pd.DataFrame,
    *,
    source: str | None,
    snapshot_id: str | None,
) -> pd.DataFrame:
    """标准化历史 universe，不执行写库。"""

    data = frame.copy()
    missing = sorted(REQUIRED_COLUMNS - set(data.columns))
    if source is not None:
        data["source"] = source
    if snapshot_id is not None:
        data["snapshot_id"] = snapshot_id
    missing = sorted(REQUIRED_COLUMNS - set(data.columns))
    if missing:
        raise UniverseHistoryError(f"历史 universe 缺少必填列: {missing}")

    for column in HISTORY_COLUMNS:
        if column not in data.columns:
            data[column] = pd.NA
    data = data[list(HISTORY_COLUMNS)].copy()
    for column in ["market", "symbol", "status", "source", "snapshot_id"]:
        data[column] = data[column].astype("string").str.strip()
    data["effective_from"] = (
        pd.to_datetime(data["effective_from"], errors="coerce").dt.normalize().astype("datetime64[ns]")
    )
    data["effective_to"] = (
        pd.to_datetime(data["effective_to"], errors="coerce").dt.normalize().astype("datetime64[ns]")
    )
    data["source_recorded_at"] = (
        pd.to_datetime(data["source_recorded_at"], errors="coerce")
        .dt.tz_localize(None)
        .fillna(pd.Timestamp.now(tz="UTC").tz_localize(None))
        .astype("datetime64[ns]")
    )
    return data.reset_index(drop=True)


def validate_universe_history(frame: pd.DataFrame) -> dict[str, Any]:
    """返回历史区间质量报告；所有硬错误都保留在报告中。"""

    data = frame.copy()
    missing_required = sorted(REQUIRED_COLUMNS - set(data.columns))
    invalid_rows: list[dict[str, Any]] = []
    if missing_required:
        return {
            "rows": int(len(data)),
            "symbols": 0,
            "markets": 0,
            "interval_conflicts": [],
            "missing_required": missing_required,
            "invalid_rows": invalid_rows,
        }

    normalized = normalize_universe_history(data, source=None, snapshot_id=None)
    for index, row in normalized.iterrows():
        problems: list[str] = []
        for column in ["market", "symbol", "status", "source", "snapshot_id"]:
            if pd.isna(row[column]) or not str(row[column]).strip():
                problems.append(f"{column} 为空")
        if pd.isna(row["effective_from"]):
            problems.append("effective_from 无效")
        if pd.notna(row["effective_to"]) and (
            pd.isna(row["effective_from"]) or row["effective_to"] <= row["effective_from"]
        ):
            problems.append("effective_to 必须晚于 effective_from")
        if problems:
            invalid_rows.append({"row": int(index), "reasons": problems})

    conflicts: list[dict[str, Any]] = []
    valid = normalized.drop(index=[item["row"] for item in invalid_rows], errors="ignore")
    group_columns = ["market", "symbol", "source", "snapshot_id"]
    for group_key, group in valid.groupby(group_columns, dropna=False, sort=True):
        previous: pd.Series | None = None
        for _, current in group.sort_values(["effective_from", "effective_to"], na_position="last").iterrows():
            if previous is not None:
                previous_end = previous["effective_to"]
                if pd.isna(previous_end) or current["effective_from"] < previous_end:
                    conflicts.append(
                        {
                            "market": str(group_key[0]),
                            "symbol": str(group_key[1]),
                            "source": str(group_key[2]),
                            "snapshot_id": str(group_key[3]),
                            "previous_from": _iso(previous["effective_from"]),
                            "previous_to": _iso(previous_end),
                            "current_from": _iso(current["effective_from"]),
                            "current_to": _iso(current["effective_to"]),
                        }
                    )
            previous = current
    return {
        "rows": int(len(normalized)),
        "symbols": int(normalized["symbol"].dropna().nunique()),
        "markets": int(normalized["market"].dropna().nunique()),
        "interval_conflicts": conflicts,
        "missing_required": missing_required,
        "invalid_rows": invalid_rows,
    }


def upsert_universe_history(
    frame: pd.DataFrame,
    db_path: str | Path,
    *,
    source: str | None = None,
    snapshot_id: str | None = None,
    replace_snapshot: bool = False,
) -> dict[str, Any]:
    """写入历史 universe；仅影响目标数据库的精确 snapshot。"""

    normalized = normalize_universe_history(frame, source=source, snapshot_id=snapshot_id)
    report = validate_universe_history(normalized)
    if report["missing_required"] or report["invalid_rows"] or report["interval_conflicts"]:
        raise UniverseHistoryError(f"历史 universe 校验失败: {report}")

    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - project dependency is installed in CI.
        raise UniverseHistoryError("写入历史 universe 需要安装 duckdb") from exc
    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(path)) as con:
        _initialize_history_table(con)
        if replace_snapshot:
            pairs = normalized[["source", "snapshot_id"]].drop_duplicates().itertuples(index=False)
            for pair_source, pair_snapshot in pairs:
                con.execute(
                    "DELETE FROM market_universe_history WHERE source = ? AND snapshot_id = ?",
                    [str(pair_source), str(pair_snapshot)],
                )
        con.register("incoming_universe_history", normalized)
        con.execute(
            """
            INSERT INTO market_universe_history
            SELECT market, symbol, effective_from, effective_to, status, name,
                   industry_level1, industry_level2, industry_level3, source,
                   snapshot_id, source_recorded_at
            FROM incoming_universe_history
            ON CONFLICT (market, symbol, effective_from, source, snapshot_id) DO NOTHING
            """
        )
        con.unregister("incoming_universe_history")
    return {
        "rows": int(len(normalized)),
        "symbols": int(normalized["symbol"].nunique()),
        "markets": int(normalized["market"].nunique()),
        "source": sorted(normalized["source"].dropna().unique().tolist()),
        "snapshot_id": sorted(normalized["snapshot_id"].dropna().unique().tolist()),
    }


def load_universe_as_of(
    db_path: str | Path,
    as_of: date,
    market: str,
    symbols: list[str] | tuple[str, ...] | None = None,
) -> pd.DataFrame:
    """读取指定日期生效的历史 universe，每个标的一行。"""

    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover
        raise UniverseHistoryError("读取历史 universe 需要安装 duckdb") from exc
    path = Path(db_path).expanduser()
    if not path.is_file():
        raise UniverseHistoryError(f"历史 universe 数据库不存在: {path}")
    symbol_clause = ""
    params: list[Any] = [market, as_of, as_of]
    if symbols:
        values = [str(value) for value in symbols]
        symbol_clause = f" AND symbol IN ({', '.join('?' for _ in values)})"
        params.extend(values)
    with duckdb.connect(str(path), read_only=True) as con:
        tables = {str(row[0]) for row in con.execute("SHOW TABLES").fetchall()}
        if "market_universe_history" not in tables:
            return pd.DataFrame(columns=list(HISTORY_COLUMNS))
        return con.execute(
            f"""
            SELECT market, symbol, effective_from, effective_to, status, name,
                   industry_level1, industry_level2, industry_level3, source,
                   snapshot_id, source_recorded_at
            FROM (
                SELECT *, row_number() OVER (
                    PARTITION BY market, symbol
                    ORDER BY effective_from DESC, source_recorded_at DESC, snapshot_id DESC
                ) AS row_number
                FROM market_universe_history
                WHERE market = ?
                  AND effective_from <= ?
                  AND (effective_to IS NULL OR effective_to > ?)
                  AND lower(status) IN ('active', 'live', '1')
                  {symbol_clause}
            )
            WHERE row_number = 1
            ORDER BY symbol
            """,
            params,
        ).fetch_df()


def normalize_industry_snapshot(
    frame: pd.DataFrame,
    *,
    source: str | None,
    snapshot_id: str | None,
) -> pd.DataFrame:
    """标准化当前行业快照，不把它伪装成 PIT 历史区间。"""

    data = frame.copy()
    if source is not None:
        data["source"] = source
    if snapshot_id is not None:
        data["snapshot_id"] = snapshot_id
    missing = sorted(INDUSTRY_REQUIRED_COLUMNS - set(data.columns))
    if missing:
        raise UniverseHistoryError(f"行业快照缺少必填列: {missing}")
    for column in INDUSTRY_SNAPSHOT_COLUMNS:
        if column not in data.columns:
            data[column] = pd.NA
    data = data[list(INDUSTRY_SNAPSHOT_COLUMNS)].copy()
    for column in ["market", "symbol", "industry_level1", "industry_level2", "industry_level3", "source", "snapshot_id"]:
        data[column] = data[column].astype("string").str.strip()
    data["source_recorded_at"] = (
        pd.to_datetime(data["source_recorded_at"], errors="coerce")
        .dt.tz_localize(None)
        .fillna(pd.Timestamp.now(tz="UTC").tz_localize(None))
        .astype("datetime64[ns]")
    )
    data = data.dropna(subset=["market", "symbol", "source", "snapshot_id"])
    data = data[data["symbol"].astype(str).str.strip().ne("")]
    return data.drop_duplicates(["market", "symbol", "source", "snapshot_id"], keep="last").reset_index(drop=True)


def build_baostock_industry_snapshot(
    raw: pd.DataFrame,
    *,
    snapshot_id: str,
    recorded_at: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """标准化既有 BaoStock 行业 updater 的输出。"""

    required = {"market", "symbol", "industry_level1", "industry_level2", "industry_level3"}
    missing = sorted(required - set(raw.columns))
    if missing:
        raise UniverseHistoryError(f"BaoStock 行业资料缺少列: {missing}")
    data = raw.copy()
    data["source_recorded_at"] = recorded_at or pd.Timestamp.now(tz="UTC").tz_localize(None)
    return normalize_industry_snapshot(data, source="baostock", snapshot_id=snapshot_id)


def upsert_industry_snapshot(
    frame: pd.DataFrame,
    db_path: str | Path,
    *,
    source: str | None = None,
    snapshot_id: str | None = None,
    replace_snapshot: bool = False,
) -> dict[str, Any]:
    """写入当前行业快照；只影响目标数据库中的精确 snapshot。"""

    normalized = normalize_industry_snapshot(frame, source=source, snapshot_id=snapshot_id)
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover
        raise UniverseHistoryError("写入行业快照需要安装 duckdb") from exc
    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(path)) as con:
        _initialize_industry_snapshot_table(con)
        if replace_snapshot:
            pairs = normalized[["source", "snapshot_id"]].drop_duplicates().itertuples(index=False)
            for pair_source, pair_snapshot in pairs:
                con.execute(
                    "DELETE FROM market_industry_snapshot WHERE source = ? AND snapshot_id = ?",
                    [str(pair_source), str(pair_snapshot)],
                )
        con.register("incoming_industry_snapshot", normalized)
        con.execute(
            """
            INSERT INTO market_industry_snapshot
            SELECT market, symbol, industry_level1, industry_level2, industry_level3,
                   source, snapshot_id, source_recorded_at
            FROM incoming_industry_snapshot
            ON CONFLICT (market, symbol, source, snapshot_id) DO UPDATE SET
                industry_level1 = EXCLUDED.industry_level1,
                industry_level2 = EXCLUDED.industry_level2,
                industry_level3 = EXCLUDED.industry_level3,
                source_recorded_at = EXCLUDED.source_recorded_at
            """
        )
        con.unregister("incoming_industry_snapshot")
    return {
        "rows": int(len(normalized)),
        "symbols": int(normalized["symbol"].nunique()),
        "markets": int(normalized["market"].nunique()),
        "source": sorted(normalized["source"].dropna().unique().tolist()),
        "snapshot_id": sorted(normalized["snapshot_id"].dropna().unique().tolist()),
    }


def load_industry_snapshot(
    db_path: str | Path,
    market: str,
    symbols: list[str] | tuple[str, ...] | None = None,
) -> pd.DataFrame:
    """读取 sidecar 中最新的指定行业快照记录。"""

    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover
        raise UniverseHistoryError("读取行业快照需要安装 duckdb") from exc
    path = Path(db_path).expanduser()
    if not path.is_file():
        return pd.DataFrame(columns=list(INDUSTRY_SNAPSHOT_COLUMNS))
    symbol_clause = ""
    params: list[Any] = [market]
    if symbols:
        values = [str(value) for value in symbols]
        symbol_clause = f" AND symbol IN ({', '.join('?' for _ in values)})"
        params.extend(values)
    with duckdb.connect(str(path), read_only=True) as con:
        tables = {str(row[0]) for row in con.execute("SHOW TABLES").fetchall()}
        if "market_industry_snapshot" not in tables:
            return pd.DataFrame(columns=list(INDUSTRY_SNAPSHOT_COLUMNS))
        return con.execute(
            f"""
            SELECT market, symbol, industry_level1, industry_level2, industry_level3,
                   source, snapshot_id, source_recorded_at
            FROM (
                SELECT *, row_number() OVER (
                    PARTITION BY market, symbol
                    ORDER BY source_recorded_at DESC, snapshot_id DESC
                ) AS row_number
                FROM market_industry_snapshot
                WHERE market = ? {symbol_clause}
            )
            WHERE row_number = 1
            ORDER BY symbol
            """,
            params,
        ).fetch_df()


def build_baostock_universe_history(
    raw: pd.DataFrame,
    *,
    snapshot_id: str,
    recorded_at: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """把 BaoStock ``query_stock_basic`` 结果转换成上市区间。

    BaoStock 的基本资料同时返回指数等非股票证券；V1 只保留 ``type=1``
    的 A 股股票。``ipoDate``/``outDate`` 是证券本身的上市窗口，不能替代
    历史行业分类，因此行业字段刻意保持为空。
    """

    required = {"code", "code_name", "ipoDate", "outDate"}
    missing = sorted(required - set(raw.columns))
    if missing:
        raise UniverseHistoryError(f"BaoStock 基本资料缺少列: {missing}")
    data = raw.copy()
    if "type" in data.columns:
        data = data[data["type"].astype(str).eq("1")].copy()
    data["symbol"] = data["code"].astype(str).str.extract(r"(\d{6})", expand=False)
    data["effective_from"] = pd.to_datetime(data["ipoDate"], errors="coerce")
    data["effective_to"] = pd.to_datetime(data["outDate"].replace("", pd.NA), errors="coerce")
    data = data.dropna(subset=["symbol", "effective_from"])
    recorded = recorded_at or pd.Timestamp.now(tz="UTC").tz_localize(None)
    return normalize_universe_history(
        pd.DataFrame(
            {
                "market": "a_share",
                "symbol": data["symbol"],
                "effective_from": data["effective_from"],
                "effective_to": data["effective_to"],
                "status": "active",
                "name": data["code_name"].astype(str).str.strip(),
                "industry_level1": pd.NA,
                "industry_level2": pd.NA,
                "industry_level3": pd.NA,
                "source": "baostock",
                "snapshot_id": snapshot_id,
                "source_recorded_at": recorded,
            }
        ),
        source=None,
        snapshot_id=None,
    )


def fetch_baostock_universe_history(
    *,
    snapshot_id: str | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """自动从 BaoStock 获取并写入 A 股上市区间。"""

    try:
        import baostock as bs
    except ImportError as exc:  # pragma: no cover
        raise UniverseHistoryError("自动获取 PIT universe 需要安装 baostock") from exc
    login = bs.login()
    if login.error_code != "0":
        raise UniverseHistoryError(f"BaoStock 登录失败: {login.error_msg}")
    try:
        result = bs.query_stock_basic()
        if result.error_code != "0":
            raise UniverseHistoryError(f"BaoStock 基本资料查询失败: {result.error_msg}")
        rows: list[list[str]] = []
        while result.next():
            rows.append(result.get_row_data())
        raw = pd.DataFrame(rows, columns=result.fields)
    finally:
        bs.logout()
    snapshot = snapshot_id or f"baostock-stock-basic-{date.today().isoformat()}"
    history = build_baostock_universe_history(raw, snapshot_id=snapshot)
    if db_path is None:
        return {"history": history, "snapshot_id": snapshot}
    return {**upsert_universe_history(history, db_path), "snapshot_id": snapshot}


def _initialize_history_table(con) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS market_universe_history (
            market VARCHAR NOT NULL,
            symbol VARCHAR NOT NULL,
            effective_from DATE NOT NULL,
            effective_to DATE,
            status VARCHAR NOT NULL,
            name VARCHAR,
            industry_level1 VARCHAR,
            industry_level2 VARCHAR,
            industry_level3 VARCHAR,
            source VARCHAR NOT NULL,
            snapshot_id VARCHAR NOT NULL,
            source_recorded_at TIMESTAMP NOT NULL,
            PRIMARY KEY (market, symbol, effective_from, source, snapshot_id)
        )
        """
    )


def _initialize_industry_snapshot_table(con) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS market_industry_snapshot (
            market VARCHAR NOT NULL,
            symbol VARCHAR NOT NULL,
            industry_level1 VARCHAR,
            industry_level2 VARCHAR,
            industry_level3 VARCHAR,
            source VARCHAR NOT NULL,
            snapshot_id VARCHAR NOT NULL,
            source_recorded_at TIMESTAMP NOT NULL,
            PRIMARY KEY (market, symbol, source, snapshot_id)
        )
        """
    )


def _iso(value: object) -> str | None:
    return value.isoformat() if isinstance(value, (date, pd.Timestamp)) else None


__all__ = [
    "HISTORY_COLUMNS",
    "INDUSTRY_SNAPSHOT_COLUMNS",
    "UniverseHistoryError",
    "build_baostock_industry_snapshot",
    "build_baostock_universe_history",
    "fetch_baostock_universe_history",
    "load_industry_snapshot",
    "load_universe_as_of",
    "normalize_industry_snapshot",
    "normalize_universe_history",
    "upsert_industry_snapshot",
    "upsert_universe_history",
    "validate_universe_history",
]
