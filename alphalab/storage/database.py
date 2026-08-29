"""SQLite 账本实现：schema、幂等写入、账户状态查询。

设计要点（SPEC 第 16/17 节）：
- runs / signals / orders / fills / cash_ledger / positions / daily_nav / anomalies；
- 订单唯一键 = strategy_id + signal_date + execution_date + symbol + side + generation_version；
- 重复执行受“同日已成功执行”与唯一键双重保护；
- 历史账本不可静默修改，冲正必须新增 ADJUSTMENT 流水。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Iterator

from ..utils import money_round, now_iso

SCHEMA_VERSION = "1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id            TEXT PRIMARY KEY,
    run_type          TEXT NOT NULL,
    as_of_date        TEXT NOT NULL,
    strategy_id       TEXT,
    strategy_version  TEXT,
    config_hash       TEXT,
    code_commit       TEXT,
    data_snapshot_id  TEXT,
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    status            TEXT NOT NULL,
    error_message     TEXT
);

CREATE TABLE IF NOT EXISTS signals (
    signal_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL,
    signal_date   TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    signal_type   TEXT NOT NULL,
    score         REAL,
    rank          INTEGER,
    target_weight REAL NOT NULL,
    reason        TEXT,
    created_at    TEXT NOT NULL,
    UNIQUE (run_id, signal_date, symbol)
);

CREATE TABLE IF NOT EXISTS orders (
    order_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    order_key       TEXT NOT NULL UNIQUE,
    run_id          TEXT NOT NULL,
    strategy_id     TEXT NOT NULL,
    signal_date     TEXT NOT NULL,
    execution_date  TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    planned_quantity INTEGER NOT NULL,
    reference_price REAL NOT NULL,
    planned_value   REAL NOT NULL,
    target_weight   REAL NOT NULL,
    order_status    TEXT NOT NULL,
    reason          TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS fills (
    fill_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id        INTEGER NOT NULL,
    order_key       TEXT NOT NULL UNIQUE,
    trade_date      TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    quantity        INTEGER NOT NULL,
    market_price    REAL NOT NULL,
    fill_price      REAL NOT NULL,
    gross_amount    REAL NOT NULL,
    slippage_amount REAL NOT NULL,
    commission      REAL NOT NULL,
    net_cash_effect REAL NOT NULL,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id)
);

CREATE TABLE IF NOT EXISTS cash_ledger (
    entry_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date       TEXT NOT NULL,
    entry_type       TEXT NOT NULL,
    related_order_key TEXT,
    related_fill_id  INTEGER,
    amount           REAL NOT NULL,
    cash_before      REAL NOT NULL,
    cash_after       REAL NOT NULL,
    description      TEXT,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    trade_date         TEXT NOT NULL,
    symbol             TEXT NOT NULL,
    quantity           INTEGER NOT NULL,
    available_quantity INTEGER NOT NULL,
    average_cost       REAL NOT NULL,
    close_price        REAL NOT NULL,
    market_value       REAL NOT NULL,
    unrealized_pnl     REAL NOT NULL,
    realized_pnl       REAL NOT NULL,
    actual_weight      REAL NOT NULL,
    target_weight      REAL NOT NULL,
    created_at         TEXT NOT NULL,
    PRIMARY KEY (trade_date, symbol)
);

CREATE TABLE IF NOT EXISTS daily_nav (
    trade_date       TEXT PRIMARY KEY,
    cash             REAL NOT NULL,
    market_value     REAL NOT NULL,
    total_equity     REAL NOT NULL,
    daily_pnl        REAL,
    daily_return     REAL,
    cumulative_return REAL,
    turnover         REAL,
    commission       REAL,
    slippage         REAL,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS anomalies (
    anomaly_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT,
    trade_date  TEXT,
    severity    TEXT NOT NULL,
    anomaly_type TEXT NOT NULL,
    symbol      TEXT,
    message     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'OPEN',
    created_at  TEXT NOT NULL,
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_orders_execution ON orders(execution_date, order_status);
CREATE INDEX IF NOT EXISTS idx_fills_date ON fills(trade_date);
CREATE INDEX IF NOT EXISTS idx_cash_date ON cash_ledger(trade_date);
CREATE INDEX IF NOT EXISTS idx_positions_date ON positions(trade_date);
CREATE INDEX IF NOT EXISTS idx_nav_date ON daily_nav(trade_date);
"""


class PaperDatabase:
    """SQLite 账本封装。"""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(str(self.db_path))
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA foreign_keys = ON")
            con.execute("PRAGMA journal_mode = WAL")
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    # ---------- 初始化 ----------
    def initialize(self, force: bool = False) -> bool:
        """创建 schema；force=True 时重建（仅用于显式重置）。"""
        if force:
            if self.db_path.exists():
                self.db_path.unlink()
        with self.connect() as con:
            con.executescript(SCHEMA)
            row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is not None:
                if row["value"] != SCHEMA_VERSION:
                    raise RuntimeError(
                        f"数据库 schema 版本不匹配: {row['value']} != {SCHEMA_VERSION}，需要迁移"
                    )
                return False
            con.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                (SCHEMA_VERSION,),
            )
            return True

    def is_initialized(self) -> bool:
        if not self.db_path.exists():
            return False
        with self.connect() as con:
            row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            return row is not None and row["value"] == SCHEMA_VERSION

    # ---------- 账户初始化 ----------
    def init_account(self, initial_cash: float, trade_date: str | date | None = None) -> None:
        td = trade_date.isoformat() if hasattr(trade_date, "isoformat") else str(trade_date)
        with self.connect() as con:
            exists = con.execute(
                "SELECT 1 FROM cash_ledger WHERE entry_type='INITIAL_CAPITAL' LIMIT 1"
            ).fetchone()
            if exists:
                raise RuntimeError("模拟账户已初始化，拒绝重复初始化（如需重置请删除数据库）")
            cash = money_round(float(initial_cash))
            con.execute(
                """INSERT INTO cash_ledger
                   (trade_date, entry_type, amount, cash_before, cash_after, description, created_at)
                   VALUES (?, 'INITIAL_CAPITAL', ?, 0.0, ?, '初始资金入账', ?)""",
                (td, cash, cash, now_iso()),
            )

    def get_initial_cash(self) -> float:
        with self.connect() as con:
            row = con.execute(
                "SELECT cash_after FROM cash_ledger WHERE entry_type='INITIAL_CAPITAL' ORDER BY entry_id LIMIT 1"
            ).fetchone()
            return float(row["cash_after"]) if row else 0.0

    # ---------- 运行记录 ----------
    def start_run(
        self,
        run_type: str,
        as_of_date: str,
        strategy_id: str = "",
        strategy_version: str = "",
        config_hash: str = "",
        code_commit: str = "",
        data_snapshot_id: str = "",
    ) -> str:
        run_id = f"{run_type}-{as_of_date}-{now_iso().replace(':', '').replace(' ', 'T')}"
        with self.connect() as con:
            con.execute(
                """INSERT INTO runs
                   (run_id, run_type, as_of_date, strategy_id, strategy_version,
                    config_hash, code_commit, data_snapshot_id, started_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'RUNNING')""",
                (
                    run_id,
                    run_type,
                    as_of_date,
                    strategy_id,
                    strategy_version,
                    config_hash,
                    code_commit,
                    data_snapshot_id,
                    now_iso(),
                ),
            )
        return run_id

    def finish_run(self, run_id: str, status: str, error_message: str | None = None) -> None:
        with self.connect() as con:
            con.execute(
                "UPDATE runs SET finished_at=?, status=?, error_message=? WHERE run_id=?",
                (now_iso(), status, error_message, run_id),
            )

    def has_successful_run(self, run_type: str, as_of_date: str) -> bool:
        with self.connect() as con:
            row = con.execute(
                "SELECT 1 FROM runs WHERE run_type=? AND as_of_date=? AND status='SUCCESS' LIMIT 1",
                (run_type, as_of_date),
            ).fetchone()
            return row is not None

    def date_already_executed(self, execution_date: str) -> bool:
        return self.has_successful_run("EXECUTE", execution_date)

    # ---------- 信号 ----------
    def insert_signals(self, run_id: str, signal_date: str, targets: Iterable[Any]) -> int:
        rows = [
            (
                run_id,
                signal_date,
                t.symbol,
                t.signal,
                float(t.score) if t.score == t.score else None,
                int(t.rank),
                float(t.target_weight),
                t.reason,
                now_iso(),
            )
            for t in targets
        ]
        with self.connect() as con:
            con.executemany(
                """INSERT OR REPLACE INTO signals
                   (run_id, signal_date, symbol, signal_type, score, rank, target_weight, reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            return len(rows)

    def get_signals(self, signal_date: str) -> list[sqlite3.Row]:
        with self.connect() as con:
            return con.execute(
                "SELECT * FROM signals WHERE signal_date=? ORDER BY rank, symbol", (signal_date,)
            ).fetchall()

    def get_latest_signals(self, as_of_date: str) -> list[sqlite3.Row]:
        """取截至 as_of_date 最近一次信号日期的全部信号。"""
        with self.connect() as con:
            row = con.execute(
                "SELECT MAX(signal_date) AS d FROM signals WHERE signal_date<=?",
                (as_of_date,),
            ).fetchone()
            if not row or not row["d"]:
                return []
            return con.execute(
                "SELECT * FROM signals WHERE signal_date=? ORDER BY rank, symbol", (row["d"],)
            ).fetchall()

    # ---------- 订单 ----------
    def insert_orders(self, run_id: str, orders: Iterable[Any]) -> int:
        rows = [
            (
                o.order_key,
                run_id,
                o.strategy_id,
                o.signal_date.isoformat(),
                o.execution_date.isoformat(),
                o.symbol,
                o.side,
                int(o.planned_quantity),
                float(o.reference_price),
                float(o.planned_value),
                float(o.target_weight),
                o.order_status,
                o.reason,
                now_iso(),
            )
            for o in orders
        ]
        inserted = 0
        with self.connect() as con:
            for row in rows:
                cur = con.execute(
                    """INSERT OR IGNORE INTO orders
                       (order_key, run_id, strategy_id, signal_date, execution_date, symbol, side,
                        planned_quantity, reference_price, planned_value, target_weight,
                        order_status, reason, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    row,
                )
                inserted += cur.rowcount
        return inserted

    def get_orders(
        self,
        execution_date: str | None = None,
        signal_date: str | None = None,
        status: str | None = None,
    ) -> list[sqlite3.Row]:
        where: list[str] = []
        params: list[str] = []
        if execution_date:
            where.append("execution_date=?")
            params.append(execution_date)
        if signal_date:
            where.append("signal_date=?")
            params.append(signal_date)
        if status:
            where.append("order_status=?")
            params.append(status)
        sql = "SELECT * FROM orders"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY side DESC, target_weight DESC, symbol"
        with self.connect() as con:
            return con.execute(sql, params).fetchall()

    def update_order_status(self, order_key: str, status: str, reason: str | None = None) -> None:
        with self.connect() as con:
            con.execute(
                "UPDATE orders SET order_status=?, reason=COALESCE(?, reason), updated_at=? WHERE order_key=?",
                (status, reason, now_iso(), order_key),
            )

    # ---------- 成交 / 现金流水 / 持仓 / 净值 ----------
    def insert_fill(
        self,
        *,
        order_id: int,
        order_key: str,
        trade_date: str,
        symbol: str,
        side: str,
        quantity: int,
        market_price: float,
        fill_price: float,
        gross_amount: float,
        slippage_amount: float,
        commission: float,
        net_cash_effect: float,
    ) -> int:
        with self.connect() as con:
            cur = con.execute(
                """INSERT OR IGNORE INTO fills
                   (order_id, order_key, trade_date, symbol, side, quantity, market_price,
                    fill_price, gross_amount, slippage_amount, commission, net_cash_effect, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    order_id,
                    order_key,
                    trade_date,
                    symbol,
                    side,
                    int(quantity),
                    float(market_price),
                    float(fill_price),
                    float(gross_amount),
                    float(slippage_amount),
                    float(commission),
                    float(net_cash_effect),
                    now_iso(),
                ),
            )
            return cur.lastrowid or 0

    def insert_cash_ledger(
        self,
        *,
        trade_date: str,
        entry_type: str,
        amount: float,
        cash_before: float,
        cash_after: float,
        description: str,
        related_order_key: str | None = None,
        related_fill_id: int | None = None,
    ) -> None:
        with self.connect() as con:
            con.execute(
                """INSERT INTO cash_ledger
                   (trade_date, entry_type, related_order_key, related_fill_id, amount,
                    cash_before, cash_after, description, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trade_date,
                    entry_type,
                    related_order_key,
                    related_fill_id,
                    money_round(amount),
                    money_round(cash_before),
                    money_round(cash_after),
                    description,
                    now_iso(),
                ),
            )

    def get_cash(self, trade_date: str) -> float:
        """截至 trade_date 的现金（最近一条流水余额）。"""
        with self.connect() as con:
            row = con.execute(
                "SELECT cash_after FROM cash_ledger WHERE trade_date<=? ORDER BY entry_id DESC LIMIT 1",
                (trade_date,),
            ).fetchone()
            if row:
                return float(row["cash_after"])
            # 初始资金视为账户成立即可用（即使入账日期晚于查询日）
            init = con.execute(
                "SELECT cash_after FROM cash_ledger WHERE entry_type='INITIAL_CAPITAL' ORDER BY entry_id LIMIT 1"
            ).fetchone()
            return float(init["cash_after"]) if init else 0.0

    def cash_ledger_rows(self, trade_date: str | None = None) -> list[sqlite3.Row]:
        with self.connect() as con:
            if trade_date:
                return con.execute(
                    "SELECT * FROM cash_ledger WHERE trade_date=? ORDER BY entry_id", (trade_date,)
                ).fetchall()
            return con.execute("SELECT * FROM cash_ledger ORDER BY entry_id").fetchall()

    def upsert_position(self, row: dict) -> None:
        with self.connect() as con:
            con.execute(
                """INSERT OR REPLACE INTO positions
                   (trade_date, symbol, quantity, available_quantity, average_cost, close_price,
                    market_value, unrealized_pnl, realized_pnl, actual_weight, target_weight, created_at)
                   VALUES (:trade_date, :symbol, :quantity, :available_quantity, :average_cost,
                           :close_price, :market_value, :unrealized_pnl, :realized_pnl,
                           :actual_weight, :target_weight, :created_at)""",
                row,
            )

    def get_positions(self, trade_date: str) -> list[sqlite3.Row]:
        """截至 trade_date 的持仓（按 symbol 取最近快照）。"""
        with self.connect() as con:
            rows = con.execute(
                """SELECT p.* FROM positions p
                   JOIN (SELECT symbol, MAX(trade_date) AS td FROM positions
                         WHERE trade_date<=? GROUP BY symbol) latest
                     ON p.symbol=latest.symbol AND p.trade_date=latest.td
                   WHERE p.quantity > 0
                   ORDER BY p.symbol""",
                (trade_date,),
            ).fetchall()
            return rows

    def positions_on(self, trade_date: str) -> list[sqlite3.Row]:
        with self.connect() as con:
            return con.execute(
                "SELECT * FROM positions WHERE trade_date=? AND quantity>0 ORDER BY symbol",
                (trade_date,),
            ).fetchall()

    def all_positions(self) -> list[sqlite3.Row]:
        with self.connect() as con:
            return con.execute(
                "SELECT trade_date, symbol, quantity FROM positions WHERE quantity>0 ORDER BY trade_date, symbol"
            ).fetchall()

    def upsert_daily_nav(self, row: dict) -> None:
        with self.connect() as con:
            con.execute(
                """INSERT OR REPLACE INTO daily_nav
                   (trade_date, cash, market_value, total_equity, daily_pnl, daily_return,
                    cumulative_return, turnover, commission, slippage, created_at)
                   VALUES (:trade_date, :cash, :market_value, :total_equity, :daily_pnl,
                           :daily_return, :cumulative_return, :turnover, :commission, :slippage,
                           :created_at)""",
                row,
            )

    def get_daily_nav(self, trade_date: str) -> sqlite3.Row | None:
        with self.connect() as con:
            return con.execute("SELECT * FROM daily_nav WHERE trade_date=?", (trade_date,)).fetchone()

    def nav_series(self) -> list[sqlite3.Row]:
        with self.connect() as con:
            return con.execute("SELECT * FROM daily_nav ORDER BY trade_date").fetchall()

    def nav_before(self, trade_date: str) -> sqlite3.Row | None:
        with self.connect() as con:
            return con.execute(
                "SELECT * FROM daily_nav WHERE trade_date<? ORDER BY trade_date DESC LIMIT 1",
                (trade_date,),
            ).fetchone()

    def get_fills(self, trade_date: str | None = None) -> list[sqlite3.Row]:
        with self.connect() as con:
            if trade_date:
                return con.execute(
                    "SELECT * FROM fills WHERE trade_date=? ORDER BY fill_id", (trade_date,)
                ).fetchall()
            return con.execute("SELECT * FROM fills ORDER BY fill_id").fetchall()

    # ---------- 异常 ----------
    def insert_anomaly(
        self,
        *,
        run_id: str | None,
        trade_date: str,
        severity: str,
        anomaly_type: str,
        message: str,
        symbol: str | None = None,
    ) -> None:
        with self.connect() as con:
            con.execute(
                """INSERT INTO anomalies
                   (run_id, trade_date, severity, anomaly_type, symbol, message, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?)""",
                (run_id, trade_date, severity, anomaly_type, symbol, message, now_iso()),
            )

    def anomalies_for(self, trade_date: str) -> list[sqlite3.Row]:
        with self.connect() as con:
            return con.execute(
                "SELECT * FROM anomalies WHERE trade_date=? ORDER BY anomaly_id", (trade_date,)
            ).fetchall()

    def duplicate_orders(self) -> list[sqlite3.Row]:
        with self.connect() as con:
            return con.execute(
                """SELECT order_key, COUNT(*) AS cnt FROM orders
                   GROUP BY order_key HAVING cnt > 1"""
            ).fetchall()

    def duplicate_fills(self) -> list[sqlite3.Row]:
        with self.connect() as con:
            return con.execute(
                """SELECT order_key, COUNT(*) AS cnt FROM fills
                   GROUP BY order_key HAVING cnt > 1"""
            ).fetchall()

    def unmatched_orders(self) -> list[sqlite3.Row]:
        with self.connect() as con:
            return con.execute(
                """SELECT o.order_key FROM orders o
                   WHERE o.order_status='FILLED'
                   AND NOT EXISTS (SELECT 1 FROM fills f WHERE f.order_key=o.order_key)"""
            ).fetchall()
