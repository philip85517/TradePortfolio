from __future__ import annotations

import numpy as np
import pandas as pd


def ema(close: pd.Series, span: int = 20) -> pd.Series:
    return close.ewm(span=span, adjust=False).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, window: int = 20) -> pd.Series:
    return true_range(df).rolling(window).mean()


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = (-delta.clip(upper=0)).rolling(window).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.fillna(100).where(loss != 0, 100)


def add_indicators(market_data: pd.DataFrame, config: dict) -> pd.DataFrame:
    cfg = config.get("scoring", {})
    out = market_data.copy().sort_values(["symbol", "date"])
    frames = []
    for _, g in out.groupby("symbol", sort=False):
        g = g.sort_values("date").copy()
        g["return_20d"] = g["close"] / g["close"].shift(cfg.get("lookback_short", 20)) - 1
        g["return_60d"] = g["close"] / g["close"].shift(cfg.get("lookback_mid", 60)) - 1
        g["return_120d"] = g["close"] / g["close"].shift(cfg.get("lookback_long", 120)) - 1
        g["ema20"] = ema(g["close"], cfg.get("ema_window", 20))
        g["ma60"] = g["close"].rolling(cfg.get("ma_mid_window", 60)).mean()
        g["atr20"] = atr(g, cfg.get("atr_window", 20))
        g["atr20_pct"] = (g["atr20"] / g["close"]).where(g["atr20"] > 0)
        g["return_atr_20d"] = g["return_20d"] / g["atr20_pct"].where(g["atr20_pct"] > 0)

        move_window = cfg.get("effective_move_window", 20)
        path = g["close"].diff().abs().rolling(move_window).sum()
        g["effective_move_20d"] = (g["close"] - g["close"].shift(move_window)).abs() / path.replace(0, np.nan)

        g["ma20_gap"] = g["close"] / g["ema20"] - 1
        g["ma20_gap_stability"] = -g["ma20_gap"].rolling(cfg.get("gap_stability_window", 20)).std()

        price_range = g["high"] - g["low"]
        close_position = (g["close"] - g["low"]) / price_range.replace(0, np.nan)
        g["daily_close_position"] = close_position.fillna(0.5)
        g["close_position_quality"] = g["daily_close_position"].rolling(
            cfg.get("close_position_window", 10)
        ).mean()
        g["rsi14"] = rsi(g["close"], cfg.get("rsi_window", 14))
        g["avg_amount_20d"] = g["amount"].rolling(20).mean()
        g["liquidity"] = np.log(g["avg_amount_20d"].where(g["avg_amount_20d"] > 0))
        frames.append(g)

    return pd.concat(frames, ignore_index=True).sort_values(["date", "symbol"]).reset_index(drop=True)

