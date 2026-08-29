from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from .data_loader import normalize_market_data, split_meta_and_prices
from .utils import project_root

DEFAULT_DB_PATH = project_root() / "data" / "processed" / "etf_strategy.duckdb"


def _duckdb():
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("DuckDB support requires `pip install duckdb`.") from exc
    return duckdb


def connect(db_path: str | Path = DEFAULT_DB_PATH):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = _duckdb().connect(str(db_path))
    initialize_database(con)
    return con


def initialize_database(con) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS etf_daily (
            date DATE NOT NULL,
            symbol VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume DOUBLE,
            amount DOUBLE,
            fund_size DOUBLE,
            premium_discount_rate DOUBLE,
            asset_class VARCHAR,
            is_leverage BOOLEAN,
            is_inverse BOOLEAN,
            is_active BOOLEAN,
            is_single_stock BOOLEAN,
            listing_date DATE,
            updated_at TIMESTAMP DEFAULT current_timestamp,
            PRIMARY KEY (date, symbol)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS etf_meta (
            symbol VARCHAR PRIMARY KEY,
            name VARCHAR NOT NULL,
            asset_class VARCHAR,
            fund_size DOUBLE,
            is_leverage BOOLEAN,
            is_inverse BOOLEAN,
            is_active BOOLEAN,
            is_single_stock BOOLEAN,
            listing_date DATE,
            updated_at TIMESTAMP DEFAULT current_timestamp
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS update_log (
            run_at TIMESTAMP DEFAULT current_timestamp,
            source VARCHAR,
            start_date DATE,
            end_date DATE,
            rows_written BIGINT,
            symbols_written BIGINT,
            failures BIGINT,
            note VARCHAR
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_etf_daily_symbol_date ON etf_daily(symbol, date)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_etf_daily_date_asset ON etf_daily(date, asset_class)")


def upsert_market_data(df: pd.DataFrame, db_path: str | Path = DEFAULT_DB_PATH, source: str = "manual", failures: int = 0, note: str | None = None) -> dict:
    data = normalize_market_data(df)
    meta, _ = split_meta_and_prices(data)
    with connect(db_path) as con:
        _upsert_daily(con, data)
        _upsert_meta(con, meta)
        con.execute(
            """
            INSERT INTO update_log (source, start_date, end_date, rows_written, symbols_written, failures, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                source,
                data["date"].min().date(),
                data["date"].max().date(),
                len(data),
                data["symbol"].nunique(),
                failures,
                note,
            ],
        )
    return {
        "rows": int(len(data)),
        "symbols": int(data["symbol"].nunique()),
        "start_date": data["date"].min().date(),
        "end_date": data["date"].max().date(),
        "db_path": str(Path(db_path)),
    }


def upsert_meta(meta: pd.DataFrame, db_path: str | Path = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as con:
        _upsert_meta(con, meta)


def load_market_data_from_db(
    db_path: str | Path = DEFAULT_DB_PATH,
    start_date: str | None = None,
    end_date: str | None = None,
    symbols: Iterable[str] | None = None,
    asset_classes: Iterable[str] | None = None,
) -> pd.DataFrame:
    clauses = []
    params: list = []
    if start_date:
        clauses.append("date >= ?")
        params.append(pd.to_datetime(start_date).date())
    if end_date:
        clauses.append("date <= ?")
        params.append(pd.to_datetime(end_date).date())
    if symbols:
        symbol_list = list(symbols)
        clauses.append(f"symbol IN ({','.join(['?'] * len(symbol_list))})")
        params.extend(symbol_list)
    if asset_classes:
        class_list = list(asset_classes)
        clauses.append(f"asset_class IN ({','.join(['?'] * len(class_list))})")
        params.extend(class_list)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"""
        SELECT date, symbol, name, open, high, low, close, volume, amount,
               fund_size, premium_discount_rate, asset_class, is_leverage,
               is_inverse, is_active, is_single_stock, listing_date
        FROM etf_daily
        {where}
        ORDER BY date, symbol
    """
    with connect(db_path) as con:
        return normalize_market_data(con.execute(query, params).fetch_df())


def load_meta_from_db(db_path: str | Path = DEFAULT_DB_PATH) -> pd.DataFrame:
    with connect(db_path) as con:
        return con.execute(
            """
            SELECT symbol, name, asset_class, fund_size, is_leverage, is_inverse,
                   is_active, is_single_stock, listing_date
            FROM etf_meta
            ORDER BY symbol
            """
        ).fetch_df()


def latest_data_date(db_path: str | Path = DEFAULT_DB_PATH) -> pd.Timestamp | None:
    path = Path(db_path)
    if not path.exists():
        return None
    with connect(path) as con:
        value = con.execute("SELECT max(date) FROM etf_daily").fetchone()[0]
    return pd.to_datetime(value) if value is not None else None


def database_summary(db_path: str | Path = DEFAULT_DB_PATH) -> dict:
    with connect(db_path) as con:
        row = con.execute(
            """
            SELECT count(*) AS rows,
                   count(DISTINCT symbol) AS symbols,
                   min(date) AS start_date,
                   max(date) AS end_date
            FROM etf_daily
            """
        ).fetchone()
        by_class = con.execute(
            """
            SELECT asset_class, count(DISTINCT symbol) AS etf_count, count(*) AS rows
            FROM etf_daily
            GROUP BY asset_class
            ORDER BY etf_count DESC
            """
        ).fetch_df()
    return {
        "rows": int(row[0] or 0),
        "symbols": int(row[1] or 0),
        "start_date": row[2],
        "end_date": row[3],
        "by_class": by_class,
    }


def import_csv_to_db(csv_path: str | Path, db_path: str | Path = DEFAULT_DB_PATH) -> dict:
    df = pd.read_csv(csv_path, dtype={"symbol": str})
    return upsert_market_data(df, db_path, source=f"csv:{Path(csv_path).name}")


def _upsert_daily(con, data: pd.DataFrame) -> None:
    con.register("incoming_daily", data)
    columns = [
        "date",
        "symbol",
        "name",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "fund_size",
        "premium_discount_rate",
        "asset_class",
        "is_leverage",
        "is_inverse",
        "is_active",
        "is_single_stock",
        "listing_date",
    ]
    column_sql = ", ".join(columns)
    con.execute(
        """
        DELETE FROM etf_daily
        USING incoming_daily
        WHERE etf_daily.date = incoming_daily.date
          AND etf_daily.symbol = incoming_daily.symbol
        """
    )
    con.execute(
        f"""
        INSERT INTO etf_daily ({column_sql})
        SELECT {column_sql}
        FROM incoming_daily
        """
    )
    con.unregister("incoming_daily")


def _upsert_meta(con, meta: pd.DataFrame) -> None:
    meta = meta.copy()
    meta["listing_date"] = pd.to_datetime(meta["listing_date"])
    con.register("incoming_meta", meta)
    columns = [
        "symbol",
        "name",
        "asset_class",
        "fund_size",
        "is_leverage",
        "is_inverse",
        "is_active",
        "is_single_stock",
        "listing_date",
    ]
    column_sql = ", ".join(columns)
    con.execute(
        """
        DELETE FROM etf_meta
        USING incoming_meta
        WHERE etf_meta.symbol = incoming_meta.symbol
        """
    )
    con.execute(
        f"""
        INSERT INTO etf_meta ({column_sql})
        SELECT {column_sql}
        FROM incoming_meta
        """
    )
    con.unregister("incoming_meta")

