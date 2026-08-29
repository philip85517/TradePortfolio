from __future__ import annotations

import pandas as pd

from src.backtester import _has_theme_exposure, run_backtest
from src.portfolio import Portfolio, Position


def _data() -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-01", periods=160)
    rows = []
    for i, date in enumerate(dates):
        close = 10 + i * 0.03
        if i == 150:
            close = 14.0
        if i == 151:
            close = 14.3
        rows.append(
            {
                "date": date,
                "symbol": "ETF001",
                "name": "ETF",
                "open": close - 0.05,
                "high": close + 0.05,
                "low": close - 0.2,
                "close": close,
                "volume": 1_000_000,
                "amount": 80_000_000,
                "fund_size": 800_000_000,
                "premium_discount_rate": 0.0,
                "asset_class": "A_SHARE_BROAD",
                "is_leverage": False,
                "is_inverse": False,
                "is_active": False,
                "is_single_stock": False,
                "listing_date": pd.Timestamp("2020-01-01"),
            }
        )
    return pd.DataFrame(rows)


def test_backtest_executes_entry_on_next_open(config):
    data = _data()
    result = run_backtest(config, data)
    trades = result["trades"]
    assert not trades.empty
    first_signal = result["signals"][result["signals"]["signal"] == "BUY"].iloc[0]
    first_buy = trades[trades["side"] == "BUY"].iloc[0]
    assert first_buy["date"] > first_signal["date"]
    execution_open = data.loc[data["date"] == first_buy["date"], "open"].iloc[0]
    assert first_buy["price"] == execution_open * (1 + config["execution"]["slippage_rate"])


def test_hard_stop_uses_stop_without_future_open(config):
    data = _data()
    data.loc[data.index[-2], "low"] = 1.0
    result = run_backtest(config, data)
    sells = result["trades"][result["trades"]["side"] == "SELL"]
    if not sells.empty:
        hard_stop = sells[sells["reason"] == "hard_stop"]
        assert not hard_stop.empty
        assert hard_stop.iloc[0]["date"] <= data.iloc[-2]["date"]


def test_existing_theme_exposure_blocks_same_direction_buy(config):
    portfolio = Portfolio(initial_cash=1_000_000)
    portfolio.positions["513520"] = Position(
        symbol="513520",
        name="日经ETF",
        asset_class="OTHER",
        shares=1000,
        entry_price=1.0,
        stop_price=0.9,
        initial_stop=0.9,
        entry_date=pd.Timestamp("2025-01-01"),
    )
    candidate = pd.Series({"symbol": "159866", "name": "日经225ETF", "asset_class": "OTHER"})

    assert _has_theme_exposure(portfolio, candidate, config)
