"""Markdown 日报生成（SPEC 第 20 节）。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from ..config import REPO_ROOT
from ..data import Universe
from ..data.loader import load_market_data
from ..storage import PaperDatabase
from ..utils import parse_date
from .common import latest_target_weights


def _fmt(v, digits: int = 2) -> str:
    if v is None:
        return "-"
    return f"{float(v):,.{digits}f}"


def _pct(v, digits: int = 2) -> str:
    if v is None:
        return "-"
    return f"{float(v):.{digits}%}"


def build_report(
    trade_date: date | str,
    db: PaperDatabase,
    universe: Universe,
    config: dict,
    market_data: pd.DataFrame | None = None,
) -> str:
    d = parse_date(trade_date)
    d_str = d.isoformat()
    symbols = universe.symbols()
    if market_data is None:
        market_data, _ = load_market_data(symbols, d, d, config)

    nav = db.get_daily_nav(d_str)
    fills = db.get_fills(d_str)
    positions = db.positions_on(d_str)
    ledger = db.cash_ledger_rows(d_str)
    today_orders = db.get_orders(execution_date=d_str)
    plan_orders = db.get_orders(signal_date=d_str)
    anomalies = db.anomalies_for(d_str)
    signals = db.get_latest_signals(d_str)
    target_w = {s["symbol"]: s["target_weight"] for s in signals}

    lines: list[str] = []
    lines.append(f"# AlphaLab 模拟交易日报 {d_str}")
    lines.append("")

    strategy_cfg = config.get("strategy", {})
    lines.append("## 账户摘要")
    lines.append("")
    lines.append("| 项目 | 数值 |")
    lines.append("|---|---:|")
    lines.append(f"| 交易日期 | {d_str} |")
    lines.append(f"| 策略版本 | {strategy_cfg.get('id', '-')} {strategy_cfg.get('version', '-')} |")
    lines.append(f"| 总资产 | {_fmt(nav['total_equity'] if nav else 0)} |")
    lines.append(f"| 现金 | {_fmt(nav['cash'] if nav else db.get_cash(d_str))} |")
    lines.append(f"| 持仓市值 | {_fmt(nav['market_value'] if nav else 0)} |")
    lines.append(f"| 当日收益 | {_fmt(nav['daily_pnl'] if nav else 0)}（{_pct(nav['daily_return'] if nav else 0)}） |")
    lines.append(f"| 累计收益 | {_pct(nav['cumulative_return'] if nav else 0)} |")
    lines.append(f"| 当日换手 | {_pct(nav['turnover'] if nav else 0)} |")
    lines.append(f"| 手续费 | {_fmt(nav['commission'] if nav else 0)} |")
    lines.append(f"| 模拟滑点 | {_fmt(nav['slippage'] if nav else 0)} |")
    lines.append("")

    lines.append("## 今日成交")
    lines.append("")
    if fills:
        lines.append("| 标的 | 方向 | 数量 | 市场价 | 模拟成交价 | 滑点 | 手续费 | 状态 |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---|")
        for f in fills:
            side = "买入" if f["side"] == "BUY" else "卖出"
            lines.append(
                f"| {f['symbol']} | {side} | {f['quantity']} | {_fmt(f['market_price'], 4)} "
                f"| {_fmt(f['fill_price'], 4)} | {_fmt(f['slippage_amount'])} | "
                f"{_fmt(f['commission'])} | 成交 |"
            )
    else:
        lines.append("无成交。")
    lines.append("")

    lines.append("## 当前持仓")
    lines.append("")
    if positions:
        lines.append("| 标的 | 数量 | 成本价 | 收盘价 | 市值 | 浮动盈亏 | 实际权重 | 目标权重 |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        for p in positions:
            lines.append(
                f"| {p['symbol']} | {p['quantity']} | {_fmt(p['average_cost'], 4)} "
                f"| {_fmt(p['close_price'], 4)} | {_fmt(p['market_value'])} | "
                f"{_fmt(p['unrealized_pnl'])} | {_pct(p['actual_weight'])} | "
                f"{_pct(target_w.get(p['symbol'], 0.0))} |"
            )
    else:
        lines.append("空仓。")
    lines.append("")

    lines.append("## 现金流水")
    lines.append("")
    if ledger:
        lines.append("| 类型 | 金额 | 变动前 | 变动后 | 说明 |")
        lines.append("|---|---:|---:|---:|---|")
        for e in ledger:
            lines.append(
                f"| {e['entry_type']} | {_fmt(e['amount'])} | {_fmt(e['cash_before'])} "
                f"| {_fmt(e['cash_after'])} | {e['description'] or '-'} |"
            )
    else:
        lines.append("当日无现金变动。")
    lines.append("")

    lines.append("## 今日订单")
    lines.append("")
    if today_orders:
        lines.append("| 标的 | 方向 | 计划数量 | 参考价 | 状态 | 原因 |")
        lines.append("|---|---|---:|---:|---|---|")
        for o in today_orders:
            side = "买入" if o["side"] == "BUY" else "卖出"
            lines.append(
                f"| {o['symbol']} | {side} | {o['planned_quantity']} | {_fmt(o['reference_price'], 4)} "
                f"| {o['order_status']} | {o['reason'] or '-'} |"
            )
    else:
        lines.append("无订单。")
    lines.append("")

    lines.append("## 下一交易日计划")
    lines.append("")
    if plan_orders:
        lines.append("| 标的 | 动作 | 计划数量 | 目标权重 | 原因 |")
        lines.append("|---|---|---:|---:|---|")
        for o in plan_orders:
            action = "买入" if o["side"] == "BUY" else "卖出"
            lines.append(
                f"| {o['symbol']} | {action} | {o['planned_quantity']} | "
                f"{_pct(o['target_weight'])} | {o['reason'] or '-'} |"
            )
    else:
        lines.append("无待执行计划（非调仓日或已全部计划）。")
    lines.append("")

    lines.append("## 异常检查")
    lines.append("")
    if anomalies:
        lines.append("| 级别 | 类型 | 信息 |")
        lines.append("|---|---|---|")
        for a in anomalies:
            lines.append(f"| {a['severity']} | {a['anomaly_type']} | {a['message']} |")
    else:
        lines.append("[PASS] 无异常记录。")
    lines.append("")
    return "\n".join(lines)


def write_report(trade_date: date | str, text: str, reports_dir: str | Path | None = None) -> Path:
    d = parse_date(trade_date)
    out_dir = Path(reports_dir) if reports_dir else REPO_ROOT / "alphalab" / "reports" / "paper"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{d.isoformat()}.md"
    path.write_text(text, encoding="utf-8")
    return path

