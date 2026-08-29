from datetime import date

from alphalab.data.loader import load_market_data
from alphalab.portfolio.order_planner import plan_orders
from alphalab.strategies.base import TargetPosition

SYMBOLS = ["510300.SH", "510500.SH", "159915.SZ", "588000.SH", "518880.SH", "513100.SH"]


def _md(config):
    cfg = dict(config)
    cfg.setdefault("data", {})["force_synthetic_symbols"] = list(SYMBOLS)
    md, _ = load_market_data(SYMBOLS, "2026-05-01", "2026-06-12", cfg)
    return md


def _targets(weights, rank=1):
    return [
        TargetPosition(
            symbol=sym,
            target_weight=w,
            score=100.0 - i,
            rank=rank + i,
            signal="BUY",
            reason="测试",
        )
        for i, (sym, w) in enumerate(weights.items())
    ]


def test_plan_buy_orders_lot_rounded(forced_synthetic_config):
    md = _md(forced_synthetic_config)
    targets = _targets({"510300.SH": 0.19})
    orders, _ = plan_orders(targets, {}, 100000.0, 100000.0, md, date(2026, 6, 5), forced_synthetic_config)
    buys = [o for o in orders if o.side == "BUY"]
    assert len(buys) == 1
    assert buys[0].planned_quantity % 100 == 0
    assert buys[0].planned_quantity > 0


def test_plan_sell_all(forced_synthetic_config):
    md = _md(forced_synthetic_config)
    positions = {"510300.SH": {"quantity": 5000, "available_quantity": 5000, "average_cost": 1.0}}
    orders, _ = plan_orders(
        _targets({}), positions, 50000.0, 50000.0, md, date(2026, 6, 5), forced_synthetic_config
    )
    sells = [o for o in orders if o.side == "SELL"]
    assert len(sells) == 1
    assert sells[0].planned_quantity == 5000


def test_plan_insufficient_cash_scales_down(forced_synthetic_config):
    md = _md(forced_synthetic_config)
    # 现金极少 → 买单应被缩减或跳过，但绝不超预算
    targets = _targets({"510300.SH": 0.19, "510500.SH": 0.19})
    orders, _ = plan_orders(targets, {}, 1000.0, 100000.0, md, date(2026, 6, 5), forced_synthetic_config)
    buys = [o for o in orders if o.side == "BUY"]
    total = sum(o.planned_value for o in buys)
    assert total <= 1000.0 + 1e-6


def test_plan_no_orders_when_no_delta(forced_synthetic_config):
    md = _md(forced_synthetic_config)
    positions = {"510300.SH": {"quantity": 7600, "available_quantity": 7600, "average_cost": 2.5}}
    orders, _ = plan_orders(
        _targets({"510300.SH": 0.19}),
        positions,
        10000.0,
        100000.0,
        md,
        date(2026, 6, 5),
        forced_synthetic_config,
    )
    # 持仓接近目标权重且差额不足一手时不应生成订单
    assert all(o.side != "BUY" for o in orders)


def test_sell_before_buy_order_sequence(forced_synthetic_config):
    md = _md(forced_synthetic_config)
    positions = {"510300.SH": {"quantity": 5000, "available_quantity": 5000, "average_cost": 1.0}}
    orders, _ = plan_orders(
        _targets({"510500.SH": 0.19}),
        positions,
        1000.0,
        100000.0,
        md,
        date(2026, 6, 5),
        forced_synthetic_config,
    )
    sides = [o.side for o in orders]
    # 规划输出先卖后买（SELL 在 BUY 之前）
    if "BUY" in sides:
        assert sides.index("SELL") < sides.index("BUY")


def test_plan_uses_next_real_trading_day(forced_synthetic_config):
    md = _md(forced_synthetic_config)
    orders, _ = plan_orders(
        _targets({"510300.SH": 0.19}),
        {},
        100000.0,
        100000.0,
        md,
        date(2026, 6, 5),
        forced_synthetic_config,
    )
    assert orders[0].execution_date.isoformat() == "2026-06-08"  # 6/5 为周五，下一数据日周一

