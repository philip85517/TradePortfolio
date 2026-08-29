"""ETF 日线轮动 V0 —— 统一策略核心（纯函数）。

约束（SPEC 9.3）：
- 无数据库写入、无全局可变状态、无网络、无 QMT、无当前时间隐式依赖；
- 相同输入必然返回相同输出；
- 市场数据在调用前必须已截断到 signal_date，否则抛出 FutureDataError。
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

import numpy as np
import pandas as pd

from ..portfolio.target_portfolio import build_equal_weights
from ..utils import parse_date
from .base import (
    FutureDataError,
    StrategyResult,
    TargetPosition,
    WeightConstraintError,
)
from .indicators import compute_indicators


def _assert_no_future_data(market_data: pd.DataFrame, signal_date: date, reject: bool = True) -> None:
    if market_data.empty:
        return
    max_date = pd.to_datetime(market_data["date"]).max()
    if max_date > pd.Timestamp(signal_date):
        if reject:
            raise FutureDataError(
                f"检测到未来数据: 数据最大日期 {max_date.date()} > 信号日期 {signal_date}"
            )


def _build_candidate_pool(
    market_data: pd.DataFrame,
    etf_metadata: pd.DataFrame,
    signal_date: date,
    config: dict,
) -> tuple[pd.DataFrame, dict]:
    uni_cfg = config.get("universe", {})
    sd = pd.Timestamp(signal_date)
    meta = etf_metadata.copy()
    meta["listing_date"] = pd.to_datetime(meta["listing_date"])
    meta["listing_days"] = (sd - meta["listing_date"]).dt.days

    mask = pd.Series(True, index=meta.index)
    min_days = int(uni_cfg.get("min_listing_days", 180))
    mask &= meta["listing_days"] >= min_days
    if uni_cfg.get("min_aum_cny"):
        mask &= meta["fund_size"].astype(float) >= float(uni_cfg["min_aum_cny"])
    if uni_cfg.get("exclude_leveraged", True):
        mask &= ~meta["is_leverage"].astype(bool)
    if uni_cfg.get("exclude_inverse", True):
        mask &= ~meta["is_inverse"].astype(bool)
    if uni_cfg.get("exclude_active", True):
        mask &= ~meta["is_active"].astype(bool)
    if uni_cfg.get("exclude_single_stock", True):
        mask &= ~meta["is_single_stock"].astype(bool)

    candidates = meta.loc[mask].copy()
    reasons: list[str] = []
    if len(candidates) < len(meta):
        reasons.append(f"上市/规模/类型过滤后候选 {len(candidates)}/{len(meta)} 只")

    # 流动性过滤（截至信号日的 20 日均成交额）
    ind = compute_indicators(market_data, config)
    latest = ind[pd.to_datetime(ind["date"]) == sd].copy()
    if latest.empty:
        return pd.DataFrame(), {"reasons": reasons + ["信号日无行情数据"], "latest": None}
    min_turnover = float(uni_cfg.get("min_avg_turnover_20d_cny", 30_000_000))
    latest = latest[latest["avg_amount_20d"].fillna(0) >= min_turnover]
    latest = latest[latest["symbol"].isin(set(candidates["symbol"]))]
    reasons.append(f"流动性过滤后 {len(latest)} 只")
    return latest, {"reasons": reasons, "latest": latest}


def _composite_score(candidates: pd.DataFrame, config: dict) -> pd.DataFrame:
    alpha = config.get("alpha", {})
    weights = alpha.get("weights", {})
    out = candidates.copy()
    out["valid_score_data"] = out[
        ["return_60d", "return_20d", "return_atr_20d", "effective_move_20d", "avg_amount_20d"]
    ].notna().all(axis=1)
    factor_map = {"liquidity": "avg_amount_20d"}
    for factor in weights:
        source = factor_map.get(factor, factor)
        out[f"{factor}_score"] = out.groupby("date")[source].rank(pct=True) * 100.0
    total = pd.Series(0.0, index=out.index)
    for factor, w in weights.items():
        total += out[f"{factor}_score"].fillna(0.0) * float(w)
    out["total_score"] = total.where(out["valid_score_data"])
    out["rank"] = out.groupby("date")["total_score"].rank(ascending=False, method="min")
    return out


def _is_rebalance_day(signal_date: date, config: dict) -> bool:
    strategy_cfg = config.get("strategy", {})
    freq = strategy_cfg.get("rebalance_frequency", "weekly")
    if freq == "weekly":
        return parse_date(signal_date).weekday() == int(strategy_cfg.get("rebalance_weekday", 4))
    if freq == "daily":
        return True
    raise ValueError(f"不支持的调仓频率: {freq}")


def _latest_close(market_data: pd.DataFrame, symbol: str, signal_date: date) -> float:
    part = market_data[
        (market_data["symbol"] == symbol) & (pd.to_datetime(market_data["date"]) <= pd.Timestamp(signal_date))
    ]
    if part.empty:
        raise KeyError(f"{symbol} 在 {signal_date} 无行情")
    return float(part.sort_values("date").iloc[-1]["close"])


def _hold_targets(
    signal_date: date,
    current_positions: dict[str, dict],
    market_data: pd.DataFrame,
    total_equity: float,
) -> tuple[list[TargetPosition], dict]:
    if not current_positions or total_equity <= 0:
        return [], {"hold": True, "reason": "非调仓日且空仓，全部持有现金"}
    targets: list[TargetPosition] = []
    diag_values: dict[str, float] = {}
    for sym, pos in current_positions.items():
        qty = int(pos.get("quantity", 0))
        if qty <= 0:
            continue
        price = _latest_close(market_data, sym, signal_date)
        value = qty * price
        weight = value / total_equity
        diag_values[sym] = weight
        targets.append(
            TargetPosition(
                symbol=sym,
                target_weight=round(weight, 6),
                score=float("nan"),
                rank=0,
                signal="HOLD",
                reason="非调仓日，维持现有持仓",
            )
        )
    return targets, {"hold": True, "weights": diag_values}


def generate_target_portfolio(
    signal_date: date | str,
    market_data: pd.DataFrame,
    etf_metadata: pd.DataFrame,
    current_positions: dict[str, dict] | Sequence[dict],
    total_equity: float,
    config: dict,
) -> StrategyResult:
    """生成 T 日目标组合。

    参数:
        signal_date: 信号日期（T 日，数据必须截断至 T 日）。
        market_data: 截至 signal_date 的行情（含 warmup 历史）。
        etf_metadata: ETF 元数据。
        current_positions: {symbol: {"quantity", "available_quantity", "average_cost"}}。
        total_equity: 当前总资产。
        config: 策略配置。
    """
    sd = parse_date(signal_date)
    reject_future = bool(config.get("validation", {}).get("reject_future_data", True))
    _assert_no_future_data(market_data, sd, reject=reject_future)

    if isinstance(current_positions, dict):
        positions = dict(current_positions)
    else:
        positions = {str(p["symbol"]): dict(p) for p in current_positions}

    # 非调仓日：维持现有持仓
    if not _is_rebalance_day(sd, config):
        targets, diag = _hold_targets(sd, positions, market_data, total_equity)
        return StrategyResult(signal_date=sd, targets=targets, diagnostics=diag)

    candidates, pool_diag = _build_candidate_pool(market_data, etf_metadata, sd, config)
    if candidates.empty:
        return StrategyResult(
            signal_date=sd,
            targets=[],
            diagnostics={**pool_diag, "reason": "候选池为空，全部持有现金"},
        )

    scored = _composite_score(candidates, config)
    eligible = scored.dropna(subset=["total_score"]).sort_values(["date", "total_score"], ascending=[True, False])
    if eligible.empty:
        return StrategyResult(
            signal_date=sd,
            targets=[],
            diagnostics={**pool_diag, "reason": "无有效评分数据，全部持有现金"},
        )

    # 绝对趋势过滤
    trend_cfg = config.get("trend_filter", {})
    if trend_cfg.get("enabled", True):
        ma_w = int(trend_cfg.get("moving_average_window", 60))
        trend_col = f"ma{ma_w}"
        before = len(eligible)
        eligible = eligible[eligible["close"] > eligible[trend_col]].copy()
        pool_diag["reasons"].append(f"MA{ma_w} 趋势过滤 {before}→{len(eligible)} 只")

    top_n = int(config.get("alpha", {}).get("top_n", 5))
    selected = eligible.head(top_n).reset_index(drop=True)
    if selected.empty:
        return StrategyResult(
            signal_date=sd,
            targets=[],
            diagnostics={**pool_diag, "reason": "无候选通过趋势过滤，全部持有现金"},
        )

    weighted = build_equal_weights(list(selected["symbol"]), config)
    targets: list[TargetPosition] = []
    held = {sym: int(pos.get("quantity", 0)) > 0 for sym, pos in positions.items()}
    for w in weighted:
        row = selected[selected["symbol"] == w.symbol].iloc[0]
        rank = int(row["rank"])
        action = "HOLD" if held.get(w.symbol) else "BUY"
        reason = (
            f"综合评分 {row['total_score']:.1f}，排名 {rank}，"
            f"60日动量 {row['return_60d']:.2%}，MA60趋势通过"
        )
        targets.append(
            TargetPosition(
                symbol=w.symbol,
                target_weight=round(w.weight, 6),
                score=round(float(row["total_score"]), 4),
                rank=rank,
                signal=action,
                reason=reason,
            )
        )

    # 未进目标组合的持仓 → 全部卖出
    target_symbols = {t.symbol for t in targets}
    for sym, pos in positions.items():
        if int(pos.get("quantity", 0)) > 0 and sym not in target_symbols:
            targets.append(
                TargetPosition(
                    symbol=sym,
                    target_weight=0.0,
                    score=float("nan"),
                    rank=0,
                    signal="EXIT",
                    reason="评分/排名跌出目标组合，清仓",
                )
            )

    total_w = sum(t.target_weight for t in targets)
    if total_w > 1.0 + 1e-9:
        raise WeightConstraintError(f"目标权重和 {total_w:.4%} 超过 100%")

    diagnostics = {
        **pool_diag,
        "rebalance": True,
        "candidates": len(candidates),
        "eligible_after_trend": len(eligible),
        "selected": [t.symbol for t in targets if t.target_weight > 0],
        "total_weight": round(total_w, 6),
    }
    return StrategyResult(signal_date=sd, targets=targets, diagnostics=diagnostics)

