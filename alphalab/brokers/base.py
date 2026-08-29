"""Broker 统一接口（SPEC 第 15 节）。"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BrokerAdapter(ABC):
    @abstractmethod
    def get_cash(self, trade_date):
        """返回截至 trade_date 的可用现金。"""

    @abstractmethod
    def get_positions(self, trade_date):
        """返回截至 trade_date 的持仓列表。"""

    @abstractmethod
    def submit_orders(self, orders):
        """提交订单（幂等）。"""

    @abstractmethod
    def get_orders(self, trade_date):
        """返回 trade_date 的订单列表。"""

    @abstractmethod
    def get_fills(self, trade_date):
        """返回 trade_date 的成交列表。"""

