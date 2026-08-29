"""账户与账本恒等式检查（SPEC 第 19 节）。"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ..storage import PaperDatabase
from ..utils import parse_date


def _anomaly(severity: str, anomaly_type: str, message: str) -> dict:
    return {"severity": severity, "anomaly_type": anomaly_type, "message": message}


def check_cash_ledger_continuity(db: PaperDatabase, tolerance: float = 0.01) -> list[dict]:
    out: list[dict] = []
    rows = db.cash_ledger_rows()
    for row in rows:
        expected = row["cash_before"] + row["amount"]
        if abs(expected - row["cash_after"]) > tolerance:
            out.append(
                _anomaly(
                    "FATAL",
                    "CASH_LEDGER_BREAK",
                    f"现金流水不连续: entry#{row['entry_id']} "
                    f"before={row['cash_before']} + amount={row['amount']} != after={row['cash_after']}",
                )
            )
    return out


def check_asset_identity(
    db: PaperDatabase,
    trade_date: str,
    market_data: pd.DataFrame,
    tolerance: float = 0.01,
) -> list[dict]:
    out: list[dict] = []
    nav = db.get_daily_nav(trade_date)
    if nav is None:
        return out
    day = market_data[pd.to_datetime(market_data["date"]) == pd.Timestamp(trade_date)]
    close = {row["symbol"]: float(row["close"]) for _, row in day.iterrows()}
    mv = 0.0
    for pos in db.positions_on(trade_date):
        price = close.get(pos["symbol"])
        if price is None:
            out.append(
                _anomaly(
                    "WARN",
                    "MISSING_CLOSE",
                    f"{pos['symbol']} 在 {trade_date} 无收盘价，无法验证资产恒等式",
                )
            )
            continue
        mv += pos["quantity"] * price
    expected_equity = nav["cash"] + mv
    if abs(expected_equity - nav["total_equity"]) > tolerance:
        out.append(
            _anomaly(
                "FATAL",
                "ASSET_IDENTITY",
                f"资产恒等式不成立: 现金+市值={expected_equity:.2f} != 总资产={nav['total_equity']:.2f}",
            )
        )
    return out


def check_position_identity(db: PaperDatabase, trade_date: str) -> list[dict]:
    """持仓恒等式：期末数量 = 期初数量 + 买入 - 卖出。"""
    out: list[dict] = []
    d = parse_date(trade_date)
    prev_date = None
    for row in db.nav_series():
        if row["trade_date"] < trade_date:
            prev_date = row["trade_date"]
    prev = {p["symbol"]: int(p["quantity"]) for p in db.get_positions(prev_date)} if prev_date else {}
    cur = {p["symbol"]: int(p["quantity"]) for p in db.positions_on(trade_date)}
    bought: dict[str, int] = {}
    sold: dict[str, int] = {}
    for f in db.get_fills(trade_date):
        if f["side"] == "BUY":
            bought[f["symbol"]] = bought.get(f["symbol"], 0) + f["quantity"]
        else:
            sold[f["symbol"]] = sold.get(f["symbol"], 0) + f["quantity"]
    symbols = set(prev) | set(cur) | set(bought) | set(sold)
    for sym in sorted(symbols):
        expected = prev.get(sym, 0) + bought.get(sym, 0) - sold.get(sym, 0)
        actual = cur.get(sym, 0)
        if expected != actual:
            out.append(
                _anomaly(
                    "FATAL",
                    "POSITION_IDENTITY",
                    f"{sym} 持仓不守恒: 期初{prev.get(sym, 0)} + 买{bought.get(sym, 0)} - 卖{sold.get(sym, 0)} "
                    f"= {expected} != 期末 {actual}",
                )
            )
    return out


def check_no_negative(db: PaperDatabase, trade_date: str) -> list[dict]:
    out: list[dict] = []
    cash = db.get_cash(trade_date)
    if cash < -0.01:
        out.append(_anomaly("FATAL", "NEGATIVE_CASH", f"{trade_date} 现金为负: {cash:.2f}"))
    for pos in db.positions_on(trade_date):
        if pos["quantity"] < 0:
            out.append(
                _anomaly("FATAL", "NEGATIVE_POSITION", f"{pos['symbol']} 持仓为负: {pos['quantity']}")
            )
    return out


def check_duplicates(db: PaperDatabase) -> list[dict]:
    out: list[dict] = []
    for row in db.duplicate_orders():
        out.append(_anomaly("FATAL", "DUPLICATE_ORDER", f"重复订单: {row['order_key']} x{row['cnt']}"))
    for row in db.duplicate_fills():
        out.append(_anomaly("FATAL", "DUPLICATE_FILL", f"重复成交: {row['order_key']} x{row['cnt']}"))
    return out


def check_order_fill_link(db: PaperDatabase) -> list[dict]:
    out: list[dict] = []
    for row in db.unmatched_orders():
        out.append(
            _anomaly("FATAL", "UNMATCHED_ORDER", f"订单标记 FILLED 但无成交记录: {row['order_key']}")
        )
    return out


def check_nav_continuity(db: PaperDatabase, tolerance: float = 0.02) -> list[dict]:
    out: list[dict] = []
    rows = db.nav_series()
    for prev, cur in zip(rows, rows[1:]):
        expected_pnl = cur["total_equity"] - prev["total_equity"]
        if cur["daily_pnl"] is not None and abs(expected_pnl - cur["daily_pnl"]) > tolerance:
            out.append(
                _anomaly(
                    "WARN",
                    "NAV_CONTINUITY",
                    f"净值不连续: {cur['trade_date']} 实际pnl={cur['daily_pnl']:.2f} "
                    f"vs 净值差={expected_pnl:.2f}",
                )
            )
    return out


def run_account_checks(
    db: PaperDatabase,
    trade_date: str,
    market_data: pd.DataFrame | None = None,
    config: dict | None = None,
) -> list[dict]:
    """执行全部账户检查，返回异常列表。"""
    cfg = config or {}
    tolerance = float(cfg.get("validation", {}).get("asset_balance_tolerance_cny", 0.01))
    checks: list[list[dict]] = [
        check_cash_ledger_continuity(db, tolerance),
        check_no_negative(db, trade_date),
        check_duplicates(db),
        check_order_fill_link(db),
        check_position_identity(db, trade_date),
        check_nav_continuity(db),
    ]
    if market_data is not None:
        checks.append(check_asset_identity(db, trade_date, market_data, tolerance))
    return [a for group in checks for a in group]

