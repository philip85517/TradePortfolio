from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def config():
    return {
        "strategy": {"initial_cash": 1_000_000},
        "universe_filter": {
            "min_listing_days": 180,
            "min_avg_amount_20d": 30_000_000,
            "min_fund_size": 500_000_000,
            "exclude_leverage": True,
            "exclude_inverse": True,
            "exclude_active": True,
            "exclude_single_stock": True,
            "max_premium_abs": 0.03,
            "max_missing_ratio_120d": 0.1,
        },
        "scoring": {
            "lookback_short": 20,
            "lookback_mid": 60,
            "lookback_long": 120,
            "atr_window": 20,
            "ema_window": 20,
            "ma_mid_window": 60,
            "effective_move_window": 20,
            "gap_stability_window": 20,
            "close_position_window": 10,
            "rsi_window": 14,
            "weights": {
                "return_20d": 0.15,
                "return_60d": 0.25,
                "return_120d": 0.15,
                "return_atr_20d": 0.15,
                "effective_move_20d": 0.15,
                "ma20_gap_stability": 0.05,
                "close_position_quality": 0.05,
                "liquidity": 0.05,
            },
            "overheat": {
                "ma20_gap_penalty_threshold": 0.12,
                "ma60_gap_penalty_threshold": 0.25,
                "rsi_penalty_threshold": 75,
                "penalty_score": 20,
            },
        },
        "candidate": {
            "top_n_watchlist": 10,
            "top_n_holdings": 5,
            "max_per_theme": 1,
            "max_per_asset_class": 2,
            "correlation_lookback": 60,
            "correlation_threshold": 0.8,
        },
        "entry": {
            "require_close_above_ma60": True,
            "require_ma20_above_ma60": True,
            "ema_pullback_atr_multiple": 0.5,
            "pullback_lookback": 5,
            "max_consecutive_close_below_ema20": 2,
            "close_position_threshold": 0.75,
            "require_bullish_candle": True,
        },
        "exit": {
            "atr_stop_multiple": 1.5,
            "consecutive_close_below_ema20_exit": 2,
            "rank_exit_threshold": 30,
            "enable_rotation_exit": True,
            "enable_trailing_stop": True,
            "profit_to_break_even_r": 2,
            "trailing_stop_atr_multiple": 2.0,
        },
        "position": {
            "max_position_value_per_etf": 100000,
            "max_loss_per_trade": 5000,
            "max_total_position_pct": 0.8,
            "max_single_asset_class_pct": 0.4,
            "min_trade_value": 2000,
        },
        "execution": {
            "commission_rate": 0.0003,
            "slippage_rate": 0.0005,
            "min_lot_size": 100,
        },
    }
