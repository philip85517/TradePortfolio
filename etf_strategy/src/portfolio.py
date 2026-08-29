from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class Position:
    symbol: str
    name: str
    asset_class: str
    shares: int
    entry_price: float
    stop_price: float
    initial_stop: float
    entry_date: pd.Timestamp

    def market_value(self, price: float) -> float:
        return self.shares * price


@dataclass
class Portfolio:
    initial_cash: float
    cash: float | None = None
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.cash is None:
            self.cash = self.initial_cash

    def total_value(self, prices: dict[str, float]) -> float:
        value = float(self.cash or 0)
        for symbol, position in self.positions.items():
            if symbol in prices and pd.notna(prices[symbol]):
                value += position.market_value(prices[symbol])
        return value

    def position_value(self, symbol: str, price: float) -> float:
        position = self.positions.get(symbol)
        return 0.0 if position is None else position.market_value(price)

    def total_position_value(self, prices: dict[str, float]) -> float:
        return sum(
            position.market_value(prices[symbol])
            for symbol, position in self.positions.items()
            if symbol in prices and pd.notna(prices[symbol])
        )

    def asset_class_value(self, asset_class: str, prices: dict[str, float]) -> float:
        return sum(
            position.market_value(prices[position.symbol])
            for position in self.positions.values()
            if position.asset_class == asset_class
            and position.symbol in prices
            and pd.notna(prices[position.symbol])
        )

    def buy(self, date, row: pd.Series, shares: int, price: float, stop_price: float, cost: float) -> None:
        gross = shares * price
        self.cash = float(self.cash or 0) - gross - cost
        self.positions[row["symbol"]] = Position(
            symbol=row["symbol"],
            name=row.get("name", row["symbol"]),
            asset_class=row.get("asset_class", "OTHER"),
            shares=shares,
            entry_price=price,
            stop_price=stop_price,
            initial_stop=stop_price,
            entry_date=pd.to_datetime(date),
        )
        self.trades.append(
            {
                "date": pd.to_datetime(date),
                "symbol": row["symbol"],
                "name": row.get("name", row["symbol"]),
                "side": "BUY",
                "shares": shares,
                "price": price,
                "cost": cost,
                "gross": gross,
                "reason": "entry",
            }
        )

    def sell(self, date, symbol: str, price: float, cost: float, reason: str) -> None:
        position = self.positions.pop(symbol)
        gross = position.shares * price
        self.cash = float(self.cash or 0) + gross - cost
        pnl = gross - cost - position.shares * position.entry_price
        self.trades.append(
            {
                "date": pd.to_datetime(date),
                "symbol": symbol,
                "name": position.name,
                "side": "SELL",
                "shares": position.shares,
                "price": price,
                "cost": cost,
                "gross": gross,
                "pnl": pnl,
                "reason": reason,
                "holding_days": (pd.to_datetime(date) - position.entry_date).days,
                "asset_class": position.asset_class,
            }
        )

