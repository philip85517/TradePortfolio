"""撮合引擎纯函数行为。"""

from alphalab.brokers.paper_broker import simulate_fills


def _order(side, symbol, qty, key_suffix):
    return {
        "order_key": f"k-{key_suffix}",
        "symbol": symbol,
        "side": side,
        "planned_quantity": qty,
        "target_weight": 0.19,
        "order_id": 0,
    }


def test_sell_before_buy_execution_order():
    cfg = {"execution": {"slippage_bps": 5, "commission_bps": 3, "min_commission_cny": 0}, "risk": {}}
    orders = [
        _order("BUY", "510500.SH", 5000, "buy"),
        _order("SELL", "510300.SH", 3000, "sell"),
    ]
    positions = {"510300.SH": {"quantity": 5000, "available_quantity": 5000, "average_cost": 2.0, "realized_pnl": 0.0}}
    open_prices = {"510300.SH": 2.5, "510500.SH": 3.0}
    result = simulate_fills(orders, positions, 20000.0, open_prices, cfg, trade_date="2026-06-08")
    assert [f.side for f in result["fills"]] == ["SELL", "BUY"]
    # 卖出回笼后现金应增加
    sell_fill = result["fills"][0]
    assert sell_fill.net_cash_effect > 0
    # 买入后现金 = 5000 + 卖出净额 - 买入成本 - 佣金
    assert result["cash"] > 0
    assert result["positions"]["510300.SH"]["quantity"] == 2000
    assert result["positions"]["510500.SH"]["quantity"] == 5000


def test_missing_price_rejected():
    cfg = {"execution": {"slippage_bps": 5, "commission_bps": 3, "min_commission_cny": 0}, "risk": {}}
    orders = [_order("BUY", "510300.SH", 5000, "b")]
    result = simulate_fills(orders, {}, 100000.0, {}, cfg)
    assert result["fills"] == []
    assert result["rejected"][0].reason.startswith("NO_PRICE")


def test_oversell_rejected():
    cfg = {"execution": {"slippage_bps": 5, "commission_bps": 3, "min_commission_cny": 0}, "risk": {}}
    orders = [_order("SELL", "510300.SH", 9999, "s")]
    positions = {"510300.SH": {"quantity": 1000, "available_quantity": 1000, "average_cost": 2.0, "realized_pnl": 0.0}}
    result = simulate_fills(orders, positions, 1000.0, {"510300.SH": 2.5}, cfg)
    assert result["rejected"][0].reason.startswith("OVERSELL")


def test_insufficient_cash_rejected():
    cfg = {"execution": {"slippage_bps": 5, "commission_bps": 3, "min_commission_cny": 0}, "risk": {}}
    orders = [_order("BUY", "510300.SH", 5000, "b")]
    result = simulate_fills(orders, {}, 100.0, {"510300.SH": 100.0}, cfg)
    assert result["rejected"][0].reason.startswith("INSUFFICIENT_CASH")


def test_full_sell_clears_position():
    cfg = {"execution": {"slippage_bps": 5, "commission_bps": 3, "min_commission_cny": 0}, "risk": {}}
    orders = [_order("SELL", "510300.SH", 1000, "s")]
    positions = {"510300.SH": {"quantity": 1000, "available_quantity": 1000, "average_cost": 2.0, "realized_pnl": 0.0}}
    result = simulate_fills(orders, positions, 0.0, {"510300.SH": 2.5}, cfg)
    assert result["positions"]["510300.SH"]["quantity"] == 0
    assert result["positions"]["510300.SH"]["average_cost"] == 0.0
    assert result["positions"]["510300.SH"]["realized_pnl"] > 0
