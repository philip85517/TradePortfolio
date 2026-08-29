from __future__ import annotations

from src.position_sizing import calculate_position_size


def test_five_percent_stop_caps_at_max_position(config):
    size = calculate_position_size(10, 9.5, 1_000_000, config)
    assert size["should_trade"]
    assert size["position_value"] == 100_000


def test_eight_percent_stop_uses_risk_budget(config):
    size = calculate_position_size(12.5, 11.5, 1_000_000, config)
    assert size["should_trade"]
    assert size["position_value"] == 62_500


def test_below_min_trade_value_blocks_trade(config):
    config["position"]["min_trade_value"] = 200_000
    size = calculate_position_size(10, 9.2, 1_000_000, config)
    assert not size["should_trade"]


def test_shares_round_to_lot_size(config):
    size = calculate_position_size(13, 12, 1_000_000, config)
    assert size["shares"] % 100 == 0
    assert size["position_value"] == size["shares"] * 13
