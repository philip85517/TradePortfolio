from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .risk import max_drawdown


def performance_metrics(equity_df: pd.DataFrame, trades_df: pd.DataFrame, config: dict) -> dict:
    if equity_df.empty:
        return {}
    equity = equity_df.set_index("date")["equity"].astype(float)
    returns = equity.pct_change().dropna()
    total_return = equity.iloc[-1] / equity.iloc[0] - 1 if equity.iloc[0] else 0.0
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1 / 365.25)
    annual_return = (1 + total_return) ** (1 / years) - 1
    annual_vol = returns.std() * np.sqrt(252) if not returns.empty else 0.0
    mdd, mdd_start, mdd_end = max_drawdown(equity)
    sell_trades = trades_df[trades_df.get("side", pd.Series(dtype=str)) == "SELL"] if not trades_df.empty else pd.DataFrame()
    wins = sell_trades[sell_trades.get("pnl", 0) > 0] if not sell_trades.empty else pd.DataFrame()
    losses = sell_trades[sell_trades.get("pnl", 0) <= 0] if not sell_trades.empty else pd.DataFrame()
    win_rate = len(wins) / len(sell_trades) if len(sell_trades) else 0.0
    profit_loss_ratio = (
        wins["pnl"].mean() / abs(losses["pnl"].mean())
        if len(wins) and len(losses) and losses["pnl"].mean() != 0
        else 0.0
    )
    return {
        "total_return": float(total_return),
        "annual_return": float(annual_return),
        "annual_volatility": float(annual_vol),
        "sharpe": float(annual_return / annual_vol) if annual_vol else 0.0,
        "max_drawdown": float(mdd),
        "max_drawdown_start": mdd_start,
        "max_drawdown_end": mdd_end,
        "calmar": float(annual_return / abs(mdd)) if mdd else 0.0,
        "win_rate": float(win_rate),
        "profit_loss_ratio": float(profit_loss_ratio),
        "avg_holding_days": float(sell_trades.get("holding_days", pd.Series(dtype=float)).mean() or 0),
        "trade_count": int(len(sell_trades)),
        "max_trade_loss": float(sell_trades.get("pnl", pd.Series([0])).min() if not sell_trades.empty else 0),
        "max_trade_profit": float(sell_trades.get("pnl", pd.Series([0])).max() if not sell_trades.empty else 0),
        "return_drawdown_ratio": float(total_return / abs(mdd)) if mdd else 0.0,
        "excess_return_vs_benchmark": np.nan,
        "information_ratio": np.nan,
    }


def annual_performance(equity_df: pd.DataFrame, trades_df: pd.DataFrame) -> pd.DataFrame:
    if equity_df.empty:
        return pd.DataFrame()
    equity = equity_df.copy()
    equity["year"] = pd.to_datetime(equity["date"]).dt.year
    rows = []
    for year, g in equity.groupby("year"):
        series = g.set_index("date")["equity"]
        year_return = series.iloc[-1] / series.iloc[0] - 1
        mdd, _, _ = max_drawdown(series)
        sells = trades_df[(pd.to_datetime(trades_df.get("date", pd.Series(dtype=str))).dt.year == year) & (trades_df.get("side", "") == "SELL")] if not trades_df.empty else pd.DataFrame()
        rows.append(
            {
                "year": year,
                "annual_return": year_return,
                "annual_max_drawdown": mdd,
                "trade_count": len(sells),
                "win_rate": (sells["pnl"] > 0).mean() if len(sells) else 0.0,
                "turnover": np.nan,
            }
        )
    return pd.DataFrame(rows)


def asset_class_performance(trades_df: pd.DataFrame) -> pd.DataFrame:
    if trades_df.empty or "asset_class" not in trades_df:
        return pd.DataFrame()
    sells = trades_df[trades_df["side"] == "SELL"].copy()
    if sells.empty:
        return pd.DataFrame()
    return (
        sells.groupby("asset_class")
        .agg(
            trade_count=("pnl", "size"),
            win_rate=("pnl", lambda x: (x > 0).mean()),
            total_pnl=("pnl", "sum"),
            avg_pnl=("pnl", "mean"),
            max_loss=("pnl", "min"),
            avg_holding_days=("holding_days", "mean"),
        )
        .reset_index()
    )


def write_daily_plan(
    date,
    positions: pd.DataFrame,
    watchlist: pd.DataFrame,
    buy_signals: pd.DataFrame,
    sell_signals: pd.DataFrame,
    output_path: str | Path,
) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    date_text = pd.to_datetime(date).strftime("%Y-%m-%d")

    def table(df: pd.DataFrame, columns: list[str]) -> str:
        if df.empty:
            df = pd.DataFrame(columns=columns)
        return df.reindex(columns=columns).to_markdown(index=False)

    content = f"""# ETFStrategy 日度交易计划：{date_text}

## 当前持仓

{table(positions, ["symbol", "name", "position_value", "entry_price", "latest_price", "stop_price", "unrealized_pnl", "daily_rank"])}

## 今日观察池 Top10

{table(watchlist, ["watch_rank", "symbol", "name", "asset_class", "total_score", "return_20d", "return_60d", "effective_move_20d", "atr20_pct"])}

## 明日买入计划

{table(buy_signals, ["symbol", "name", "assumed_buy_price", "stop_price", "position_value", "max_loss", "reason"])}

## 明日卖出计划

{table(sell_signals, ["symbol", "name", "reason", "assumed_sell_price"])}
"""
    output_path.write_text(content, encoding="utf-8")
    return output_path

