from __future__ import annotations

import pandas as pd


def close_position(row: pd.Series) -> float:
    if row["high"] == row["low"]:
        return 0.5
    return (row["close"] - row["low"]) / (row["high"] - row["low"])


def max_consecutive_true(values: pd.Series) -> int:
    best = current = 0
    for value in values.fillna(False).astype(bool):
        current = current + 1 if value else 0
        best = max(best, current)
    return best


def generate_entry_signal(symbol: str, history_df: pd.DataFrame, config: dict) -> bool:
    entry_cfg = config.get("entry", {})
    hist = history_df[history_df["symbol"] == symbol].sort_values("date").copy()
    if hist.empty:
        return False
    lookback = entry_cfg.get("pullback_lookback", 5)
    recent = hist.tail(lookback)
    today = hist.iloc[-1]

    required = ["close", "open", "high", "low", "ema20", "ma60", "atr20"]
    if pd.isna(today[required]).any():
        return False

    if entry_cfg.get("require_close_above_ma60", True) and not today["close"] > today["ma60"]:
        return False
    if entry_cfg.get("require_ma20_above_ma60", True) and not today["ema20"] > today["ma60"]:
        return False

    pullback_level = recent["ema20"] + entry_cfg.get("ema_pullback_atr_multiple", 0.5) * recent["atr20"]
    if not (recent["low"] <= pullback_level).any():
        return False

    below_ema = recent["close"] < recent["ema20"]
    if max_consecutive_true(below_ema) > entry_cfg.get("max_consecutive_close_below_ema20", 2):
        return False

    if not today["close"] > today["ema20"]:
        return False
    if entry_cfg.get("require_bullish_candle", True) and not today["close"] > today["open"]:
        return False
    if today["high"] == today["low"]:
        return False
    if close_position(today) < entry_cfg.get("close_position_threshold", 0.75):
        return False

    return True


def calculate_initial_stop(entry_price: float, atr20: float, ema20: float | None = None, recent_low: float | None = None, config: dict | None = None) -> float:
    cfg = (config or {}).get("exit", {})
    return entry_price - cfg.get("atr_stop_multiple", 1.5) * atr20


def generate_exit_signal(position: dict, history_df: pd.DataFrame, scored_df: pd.DataFrame, config: dict) -> dict:
    exit_cfg = config.get("exit", {})
    symbol = position["symbol"]
    hist = history_df[history_df["symbol"] == symbol].sort_values("date")
    if hist.empty:
        return {"exit": False, "reason": None, "updated_stop": position.get("stop_price")}

    today = hist.iloc[-1]
    stop_price = position.get("stop_price")
    updated_stop = stop_price

    if stop_price is not None and today["low"] <= stop_price:
        return {"exit": True, "reason": "hard_stop", "exit_price": stop_price, "updated_stop": stop_price}

    consecutive = exit_cfg.get("consecutive_close_below_ema20_exit", 2)
    if max_consecutive_true((hist.tail(consecutive)["close"] < hist.tail(consecutive)["ema20"])) >= consecutive:
        return {"exit": True, "reason": "trend_break", "updated_stop": updated_stop}

    daily_score = scored_df[
        (scored_df["symbol"] == symbol) & (pd.to_datetime(scored_df["date"]) == pd.to_datetime(today["date"]))
    ]
    if not daily_score.empty:
        rank = daily_score.iloc[-1].get("daily_rank")
        if pd.notna(rank) and rank > exit_cfg.get("rank_exit_threshold", 30):
            return {"exit": True, "reason": "rank_degrade", "updated_stop": updated_stop}

    initial_risk = max(position.get("entry_price", 0) - position.get("initial_stop", stop_price or 0), 0)
    if initial_risk > 0 and today["close"] - position.get("entry_price", 0) >= exit_cfg.get("profit_to_break_even_r", 2) * initial_risk:
        updated_stop = max(updated_stop or 0, position.get("entry_price", 0))

    if exit_cfg.get("enable_trailing_stop", True) and pd.notna(today.get("atr20")):
        trailing = today["close"] - exit_cfg.get("trailing_stop_atr_multiple", 2.0) * today["atr20"]
        updated_stop = max(updated_stop or 0, trailing)

    return {"exit": False, "reason": None, "updated_stop": updated_stop}

