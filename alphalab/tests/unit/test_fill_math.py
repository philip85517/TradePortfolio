from alphalab.brokers.paper_broker import compute_commission, compute_fill_price


def test_buy_slippage():
    assert compute_fill_price(10.0, "BUY", 5) == 10.005


def test_sell_slippage():
    assert compute_fill_price(10.0, "SELL", 5) == 9.995


def test_commission_rate():
    assert compute_commission(10000.0, 3, 0) == 3.0


def test_min_commission():
    assert compute_commission(100.0, 3, 5) == 5.0


def test_zero_commission():
    assert compute_commission(0.0, 3, 0) == 0.0

