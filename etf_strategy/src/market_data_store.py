from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd

from .utils import project_root

DEFAULT_MARKET_DB_PATH = project_root() / "data" / "processed" / "market_data.duckdb"

TIMEFRAMES = {
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
    "1w": timedelta(days=7),
    "1mo": timedelta(days=31),
}

REQUIRED_BAR_COLUMNS = {
    "market",
    "symbol",
    "timeframe",
    "ts",
    "open",
    "high",
    "low",
    "close",
    "volume",
}


def _duckdb():
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("DuckDB support requires `pip install duckdb`.") from exc
    return duckdb


def connect_market_db(db_path: str | Path = DEFAULT_MARKET_DB_PATH, read_only: bool = False):
    db_path = Path(db_path)
    if read_only:
        return _duckdb().connect(str(db_path), read_only=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = _duckdb().connect(str(db_path))
    initialize_market_database(con)
    return con


def initialize_market_database(con) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS market_ohlcv (
            market VARCHAR NOT NULL,
            symbol VARCHAR NOT NULL,
            timeframe VARCHAR NOT NULL,
            ts TIMESTAMP NOT NULL,
            trade_date DATE NOT NULL,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume DOUBLE,
            amount DOUBLE,
            source VARCHAR,
            adjusted BOOLEAN DEFAULT false,
            adjustment VARCHAR DEFAULT 'unknown',
            fetched_at TIMESTAMP DEFAULT current_timestamp,
            PRIMARY KEY (market, symbol, timeframe, ts)
        )
        """
    )
    con.execute("ALTER TABLE market_ohlcv ADD COLUMN IF NOT EXISTS adjustment VARCHAR DEFAULT 'unknown'")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS market_update_log (
            run_at TIMESTAMP DEFAULT current_timestamp,
            market VARCHAR,
            symbol VARCHAR,
            timeframe VARCHAR,
            source VARCHAR,
            start_ts TIMESTAMP,
            end_ts TIMESTAMP,
            rows_written BIGINT,
            note VARCHAR
        )
        """
    )
    con.execute("ALTER TABLE market_update_log ADD COLUMN IF NOT EXISTS status VARCHAR DEFAULT 'success'")
    con.execute("ALTER TABLE market_update_log ADD COLUMN IF NOT EXISTS attempts INTEGER DEFAULT 1")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS market_universe (
            market VARCHAR NOT NULL,
            symbol VARCHAR NOT NULL,
            name VARCHAR,
            source_symbol VARCHAR,
            provider VARCHAR,
            quote_ccy VARCHAR,
            status VARCHAR,
            industry_level1 VARCHAR,
            industry_level2 VARCHAR,
            industry_level3 VARCHAR,
            discovered_at TIMESTAMP DEFAULT current_timestamp,
            PRIMARY KEY (market, symbol)
        )
        """
    )
    con.execute("ALTER TABLE market_universe ADD COLUMN IF NOT EXISTS industry_level1 VARCHAR")
    con.execute("ALTER TABLE market_universe ADD COLUMN IF NOT EXISTS industry_level2 VARCHAR")
    con.execute("ALTER TABLE market_universe ADD COLUMN IF NOT EXISTS industry_level3 VARCHAR")
    con.execute("CREATE INDEX IF NOT EXISTS idx_market_ohlcv_lookup ON market_ohlcv(market, symbol, timeframe, ts)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_market_ohlcv_date ON market_ohlcv(trade_date, market, timeframe)")


def upsert_universe(df: pd.DataFrame, db_path: str | Path = DEFAULT_MARKET_DB_PATH) -> dict:
    required = {"market", "symbol"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required universe columns: {sorted(missing)}")
    data = df.copy()
    for column in ["name", "source_symbol", "provider", "quote_ccy", "status", "industry_level1", "industry_level2", "industry_level3"]:
        if column not in data.columns:
            data[column] = pd.NA
    columns = [
        "market",
        "symbol",
        "name",
        "source_symbol",
        "provider",
        "quote_ccy",
        "status",
        "industry_level1",
        "industry_level2",
        "industry_level3",
    ]
    data = data[columns].drop_duplicates(["market", "symbol"], keep="last")
    with connect_market_db(db_path) as con:
        existing_industries = con.execute(
            """
            SELECT market, symbol, industry_level1, industry_level2, industry_level3
            FROM market_universe
            """
        ).fetch_df()
        if not existing_industries.empty:
            data = data.merge(
                existing_industries,
                how="left",
                on=["market", "symbol"],
                suffixes=("", "_existing"),
            )
            for column in ["industry_level1", "industry_level2", "industry_level3"]:
                existing_column = f"{column}_existing"
                incoming = data[column].astype("string")
                data[column] = incoming.where(incoming.notna() & incoming.str.strip().ne(""), data[existing_column])
                data = data.drop(columns=[existing_column])
        con.register("incoming_universe", data)
        markets = data["market"].drop_duplicates().tolist()
        if markets:
            con.execute(
                f"DELETE FROM market_universe WHERE market IN ({','.join(['?'] * len(markets))})",
                markets,
            )
        con.execute(
            """
            INSERT INTO market_universe (
                market, symbol, name, source_symbol, provider, quote_ccy, status,
                industry_level1, industry_level2, industry_level3
            )
            SELECT market, symbol, name, source_symbol, provider, quote_ccy, status,
                   industry_level1, industry_level2, industry_level3
            FROM incoming_universe
            """
        )
        con.unregister("incoming_universe")
    return {"rows": int(len(data)), "markets": int(data["market"].nunique())}


def load_universe_from_db(
    db_path: str | Path = DEFAULT_MARKET_DB_PATH,
    markets: Iterable[str] | None = None,
) -> pd.DataFrame:
    clauses = []
    params: list = []
    if markets:
        values = list(markets)
        clauses.append(f"market IN ({','.join(['?'] * len(values))})")
        params.extend(values)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect_market_db(db_path) as con:
        return con.execute(
            f"""
            SELECT market, symbol, name, source_symbol, provider, quote_ccy, status,
                   industry_level1, industry_level2, industry_level3, discovered_at
            FROM market_universe
            {where}
            ORDER BY market, symbol
            """,
            params,
        ).fetch_df()


def normalize_bars(df: pd.DataFrame) -> pd.DataFrame:
    missing = REQUIRED_BAR_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"Missing required bar columns: {sorted(missing)}")

    out = df.copy()
    if "amount" not in out.columns:
        out["amount"] = pd.NA
    if "source" not in out.columns:
        out["source"] = "unknown"
    if "adjusted" not in out.columns:
        out["adjusted"] = False
    if "adjustment" not in out.columns:
        out["adjustment"] = out["adjusted"].map(lambda value: "qfq" if bool(value) else "none")

    out["market"] = out["market"].astype(str)
    out["symbol"] = out["symbol"].astype(str)
    out["timeframe"] = out["timeframe"].astype(str)
    invalid_timeframes = sorted(set(out["timeframe"]) - set(TIMEFRAMES))
    if invalid_timeframes:
        raise ValueError(f"Unsupported timeframe(s): {invalid_timeframes}")

    out["ts"] = pd.to_datetime(out["ts"]).dt.tz_localize(None)
    out["trade_date"] = out["ts"].dt.date
    for column in ["open", "high", "low", "close", "volume", "amount"]:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    out["adjusted"] = out["adjusted"].fillna(False).astype(bool)
    out["adjustment"] = out["adjustment"].fillna("unknown").astype(str)
    out = out.dropna(subset=["ts", "open", "high", "low", "close"])
    out = out.sort_values(["market", "symbol", "timeframe", "ts"])
    out = out.drop_duplicates(["market", "symbol", "timeframe", "ts"], keep="last")
    columns = [
        "market",
        "symbol",
        "timeframe",
        "ts",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "source",
        "adjusted",
        "adjustment",
    ]
    return out[columns].reset_index(drop=True)


def upsert_bars(
    df: pd.DataFrame,
    db_path: str | Path = DEFAULT_MARKET_DB_PATH,
    source: str | None = None,
    note: str | None = None,
) -> dict:
    data = normalize_bars(df)
    if data.empty:
        return {"rows": 0, "symbols": 0, "start_ts": None, "end_ts": None, "db_path": str(Path(db_path))}
    if source is not None:
        data["source"] = source

    with connect_market_db(db_path) as con:
        con.register("incoming_ohlcv", data)
        con.execute(
            """
            DELETE FROM market_ohlcv
            USING incoming_ohlcv
            WHERE market_ohlcv.market = incoming_ohlcv.market
              AND market_ohlcv.symbol = incoming_ohlcv.symbol
              AND market_ohlcv.timeframe = incoming_ohlcv.timeframe
              AND market_ohlcv.ts = incoming_ohlcv.ts
            """
        )
        con.execute(
            """
            INSERT INTO market_ohlcv (
                market, symbol, timeframe, ts, trade_date, open, high, low,
                close, volume, amount, source, adjusted, adjustment
            )
            SELECT market, symbol, timeframe, ts, trade_date, open, high, low,
                   close, volume, amount, source, adjusted, adjustment
            FROM incoming_ohlcv
            """
        )
        for (market, symbol, timeframe), group in data.groupby(["market", "symbol", "timeframe"]):
            con.execute(
                """
                INSERT INTO market_update_log (
                    market, symbol, timeframe, source, start_ts, end_ts, rows_written, note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    market,
                    symbol,
                    timeframe,
                    source or str(group["source"].iloc[-1]),
                    group["ts"].min().to_pydatetime(),
                    group["ts"].max().to_pydatetime(),
                    len(group),
                    note,
                ],
            )
        con.unregister("incoming_ohlcv")

    return {
        "rows": int(len(data)),
        "symbols": int(data[["market", "symbol"]].drop_duplicates().shape[0]),
        "start_ts": data["ts"].min(),
        "end_ts": data["ts"].max(),
        "db_path": str(Path(db_path)),
    }


def record_update_attempt(
    db_path: str | Path,
    market: str,
    symbol: str,
    timeframe: str,
    source: str,
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
    *,
    rows_written: int = 0,
    note: str | None = None,
    status: str = "success",
    attempts: int = 1,
) -> None:
    """Persist an update attempt, including empty or failed provider responses."""

    with connect_market_db(db_path) as con:
        _record_update_attempt(
            con,
            market,
            symbol,
            timeframe,
            source,
            start_ts,
            end_ts,
            rows_written=rows_written,
            note=note,
            status=status,
            attempts=attempts,
        )


def _record_update_attempt(
    con,
    market: str,
    symbol: str,
    timeframe: str,
    source: str,
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
    *,
    rows_written: int,
    note: str | None,
    status: str,
    attempts: int,
) -> None:
    con.execute(
        """
        INSERT INTO market_update_log (
            market, symbol, timeframe, source, start_ts, end_ts, rows_written, note, status, attempts
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            market,
            symbol,
            timeframe,
            source,
            pd.Timestamp(start_ts).to_pydatetime(),
            pd.Timestamp(end_ts).to_pydatetime(),
            rows_written,
            note,
            status,
            attempts,
        ],
    )


def load_bars(
    db_path: str | Path = DEFAULT_MARKET_DB_PATH,
    markets: Iterable[str] | None = None,
    symbols: Iterable[str] | None = None,
    timeframes: Iterable[str] | None = None,
    start_ts: str | pd.Timestamp | None = None,
    end_ts: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    clauses = []
    params: list = []
    if markets:
        values = list(markets)
        clauses.append(f"market IN ({','.join(['?'] * len(values))})")
        params.extend(values)
    if symbols:
        values = list(symbols)
        clauses.append(f"symbol IN ({','.join(['?'] * len(values))})")
        params.extend(values)
    if timeframes:
        values = list(timeframes)
        clauses.append(f"timeframe IN ({','.join(['?'] * len(values))})")
        params.extend(values)
    if start_ts is not None:
        clauses.append("ts >= ?")
        params.append(pd.to_datetime(start_ts).to_pydatetime())
    if end_ts is not None:
        clauses.append("ts <= ?")
        params.append(pd.to_datetime(end_ts).to_pydatetime())

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"""
        SELECT market, symbol, timeframe, ts, trade_date, open, high, low,
               close, volume, amount, source, adjusted, adjustment
        FROM market_ohlcv
        {where}
        ORDER BY market, symbol, timeframe, ts
    """
    with connect_market_db(db_path) as con:
        return normalize_bars(con.execute(query, params).fetch_df())


def latest_bar_ts(
    db_path: str | Path,
    market: str,
    symbol: str,
    timeframe: str,
) -> pd.Timestamp | None:
    path = Path(db_path)
    if not path.exists():
        return None
    with connect_market_db(path) as con:
        value = con.execute(
            """
            SELECT max(ts)
            FROM market_ohlcv
            WHERE market = ? AND symbol = ? AND timeframe = ?
            """,
            [market, symbol, timeframe],
        ).fetchone()[0]
    return pd.to_datetime(value) if value is not None else None


def market_database_summary(db_path: str | Path = DEFAULT_MARKET_DB_PATH) -> dict:
    with connect_market_db(db_path) as con:
        totals = con.execute(
            """
            SELECT count(*) AS rows,
                   count(DISTINCT market || ':' || symbol) AS instruments,
                   min(ts) AS start_ts,
                   max(ts) AS end_ts
            FROM market_ohlcv
            """
        ).fetchone()
        by_market = con.execute(
            """
            SELECT market, timeframe, count(DISTINCT symbol) AS instruments, count(*) AS rows,
                   min(ts) AS start_ts, max(ts) AS end_ts
            FROM market_ohlcv
            GROUP BY market, timeframe
            ORDER BY market, timeframe
            """
        ).fetch_df()
    return {
        "rows": int(totals[0] or 0),
        "instruments": int(totals[1] or 0),
        "start_ts": totals[2],
        "end_ts": totals[3],
        "by_market": by_market,
    }
