from __future__ import annotations

import pandas as pd

from scripts.update_stock_data import build_stock_instruments, filter_missing_timeframes
from src.market_data_store import upsert_bars
from src.market_universe import parse_nasdaq_trader_universe


def test_parse_nasdaq_trader_universe_filters_etfs_and_maps_source_symbols():
    nasdaq_text = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N
NA|Nano Labs Ltd - Class A Ordinary Shares|G|N|N|100|N|N
QQQ|Invesco QQQ Trust|Q|N|N|100|Y|N
ACHR.W|Archer Aviation Inc. Redeemable Warrants|G|N|N|100|N|N
FBYDP|Falcon's Beyond Global, Inc. - 11% Series B Cumulative Convertible Preferred Stock|G|N|N|100|N|N
TEST|Test Company|Q|Y|N|100|N|N
File Creation Time: 0625202618:03|||||||
"""
    other_text = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
A|Agilent Technologies, Inc. Common Stock|N|A|N|100|N|A
BCSS.U|Bain Capital GSS Investment Corp. Units|N|BCSS.U|N|100|N|BCSS.U
CELG.R|Bristol-Myers Squibb Company Celegne Contingent Value Rights|N|CELG.R|N|100|N|CELG.R
SPY|SPDR S&P 500 ETF Trust|P|SPY|Y|100|N|SPY
"""

    universe = parse_nasdaq_trader_universe(nasdaq_text, other_text)

    assert universe["symbol"].tolist() == ["AAPL", "NA", "A"]
    assert universe["market"].tolist() == ["us", "us", "us"]
    assert universe["source_symbol"].tolist() == ["105.AAPL", "105.NA", "106.A"]
    assert set(universe["provider"]) == {"akshare"}


def test_build_stock_instruments_uses_universe_provider_and_adjustment():
    universe = pd.DataFrame(
        {
            "market": ["a_share", "us"],
            "symbol": ["000001", "AAPL"],
            "source_symbol": ["000001", "105.AAPL"],
            "provider": ["baostock", "akshare"],
        }
    )

    instruments = build_stock_instruments(universe, adjust="qfq")

    assert [(item.market, item.symbol, item.source_symbol, item.provider) for item in instruments] == [
        ("a_share", "000001", "000001", "baostock"),
        ("us", "AAPL", "105.AAPL", "akshare"),
    ]
    assert instruments[0].options == {"adjust": "qfq"}


def test_filter_missing_timeframes_keeps_symbols_with_incomplete_daily_data(tmp_path):
    db = tmp_path / "market.duckdb"
    upsert_bars(
        pd.DataFrame(
            {
                "market": ["us"],
                "symbol": ["AAPL"],
                "timeframe": ["1d"],
                "ts": ["2024-01-02"],
                "open": [10],
                "high": [11],
                "low": [9],
                "close": [10.5],
                "volume": [1000],
            }
        ),
        db_path=db,
        source="unit",
    )
    universe = pd.DataFrame(
        {
            "market": ["us", "us"],
            "symbol": ["AAPL", "MSFT"],
            "name": ["Apple", "Microsoft"],
            "source_symbol": ["105.AAPL", "105.MSFT"],
            "provider": ["akshare", "akshare"],
        }
    )

    missing = filter_missing_timeframes(universe, db, ["1d"])

    assert missing["symbol"].tolist() == ["MSFT"]
