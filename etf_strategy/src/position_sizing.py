from __future__ import annotations

import math


def calculate_position_size(entry_price: float, stop_price: float, cash: float, config: dict) -> dict:
    cfg = config.get("position", {})
    exec_cfg = config.get("execution", {})

    if entry_price <= 0:
        return _no_trade("invalid_entry_price")
    stop_loss_pct = (entry_price - stop_price) / entry_price
    if stop_loss_pct <= 0:
        return _no_trade("invalid_stop")

    theoretical = cfg.get("max_loss_per_trade", 5000) / stop_loss_pct
    position_value = min(theoretical, cfg.get("max_position_value_per_etf", 100000), cash)
    if position_value < cfg.get("min_trade_value", 2000):
        return _no_trade("below_min_trade_value", stop_loss_pct=stop_loss_pct)

    lot = exec_cfg.get("min_lot_size", 100)
    shares = math.floor(position_value / entry_price / lot) * lot
    if shares <= 0:
        return _no_trade("below_min_lot", stop_loss_pct=stop_loss_pct)

    actual_value = shares * entry_price
    return {
        "should_trade": True,
        "shares": shares,
        "position_value": actual_value,
        "theoretical_position_value": theoretical,
        "stop_loss_pct": stop_loss_pct,
        "max_loss": actual_value * stop_loss_pct,
        "reason": None,
    }


def _no_trade(reason: str, **extra) -> dict:
    out = {
        "should_trade": False,
        "shares": 0,
        "position_value": 0.0,
        "theoretical_position_value": 0.0,
        "stop_loss_pct": None,
        "max_loss": 0.0,
        "reason": reason,
    }
    out.update(extra)
    return out

