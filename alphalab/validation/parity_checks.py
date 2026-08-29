"""回测与模拟回放一致性对比（SPEC 第 23 节）。"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class ParityDiff:
    trade_date: str
    diff_type: str
    symbol: str
    backtest_value: float
    replay_value: float
    diff: float
    possible_cause: str = ""


def compare_nav_series(
    backtest_nav: pd.DataFrame,
    replay_nav: pd.DataFrame,
    tolerance: float = 0.01,
) -> list[ParityDiff]:
    """逐日比较总资产/现金；返回差异列表。"""
    diffs: list[ParityDiff] = []
    bt = backtest_nav.set_index("trade_date")
    rp = replay_nav.set_index("trade_date")
    for d in bt.index.union(rp.index):
        b = bt.loc[d] if d in bt.index else None
        r = rp.loc[d] if d in rp.index else None
        if b is None or r is None:
            diffs.append(
                ParityDiff(str(d), "MISSING_NAV", "", 0.0, 0.0, 0.0, "某一侧缺少该日净值")
            )
            continue
        for col, label in [("total_equity", "NAV"), ("cash", "CASH")]:
            diff = float(r[col]) - float(b[col])
            if abs(diff) > tolerance:
                diffs.append(
                    ParityDiff(
                        str(d),
                        label,
                        "",
                        float(b[col]),
                        float(r[col]),
                        diff,
                        "成交/现金逻辑漂移",
                    )
                )
    return diffs


def compare_positions(
    backtest_positions: list[dict],
    replay_positions: list[dict],
) -> list[ParityDiff]:
    """比较持仓快照（数量完全一致）。"""
    diffs: list[ParityDiff] = []
    bt = {(p["trade_date"], p["symbol"]): int(p["quantity"]) for p in backtest_positions}
    rp = {(p["trade_date"], p["symbol"]): int(p["quantity"]) for p in replay_positions}
    for key in sorted(set(bt) | set(rp)):
        b = bt.get(key, 0)
        r = rp.get(key, 0)
        if b != r:
            diffs.append(
                ParityDiff(key[0], "POSITION", key[1], b, r, r - b, "持仓数量不一致")
            )
    return diffs


def format_parity_report(diffs: list[ParityDiff]) -> str:
    if not diffs:
        return "[PASS] 回测与回放完全一致（差异均不超过容差）"
    lines = ["| 日期 | 类型 | 标的 | 回测值 | 回放值 | 差异 | 可能原因 |", "|---|---|---|---:|---:|---:|---|"]
    for d in diffs:
        lines.append(
            f"| {d.trade_date} | {d.diff_type} | {d.symbol or '-'} | {d.backtest_value:.2f} "
            f"| {d.replay_value:.2f} | {d.diff:+.2f} | {d.possible_cause} |"
        )
    return "\n".join(lines)

