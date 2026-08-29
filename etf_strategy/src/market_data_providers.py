from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Protocol

import numpy as np
import pandas as pd

from .market_data_store import TIMEFRAMES, normalize_bars


class MarketDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class FetchRequest:
    market: str
    symbol: str
    timeframe: str
    start: pd.Timestamp
    end: pd.Timestamp
    source_symbol: str | None = None
    provider: str | None = None
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def provider_symbol(self) -> str:
        return self.source_symbol or self.symbol


class MarketDataProvider(Protocol):
    name: str

    def fetch_ohlcv(self, request: FetchRequest) -> pd.DataFrame:
        ...


class SyntheticProvider:
    name = "synthetic"

    def fetch_ohlcv(self, request: FetchRequest) -> pd.DataFrame:
        if request.timeframe not in TIMEFRAMES:
            raise MarketDataError(f"Unsupported timeframe: {request.timeframe}")

        freq = {
            "5m": "5min",
            "15m": "15min",
            "30m": "30min",
            "1h": "1h",
            "1d": "1D",
            "1w": "W-FRI",
            "1mo": "MS",
        }[request.timeframe]
        index = pd.date_range(request.start, request.end, freq=freq)
        if request.timeframe in {"5m", "15m", "30m", "1h"}:
            index = index[index.dayofweek < 5]
        if len(index) == 0:
            return pd.DataFrame()

        seed = abs(hash((request.market, request.symbol, request.timeframe))) % (2**32)
        rng = np.random.default_rng(seed)
        base = 10 + (seed % 5000) / 100
        returns = rng.normal(0.0001, 0.01, len(index))
        close = base * np.cumprod(1 + returns)
        open_ = close * (1 + rng.normal(0, 0.002, len(index)))
        high = np.maximum(open_, close) * (1 + rng.uniform(0.0005, 0.01, len(index)))
        low = np.minimum(open_, close) * (1 - rng.uniform(0.0005, 0.01, len(index)))
        volume = rng.integers(10_000, 1_000_000, len(index)).astype(float)
        df = pd.DataFrame(
            {
                "market": request.market,
                "symbol": request.symbol,
                "timeframe": request.timeframe,
                "ts": index,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "amount": volume * close,
                "source": self.name,
                "adjusted": False,
                "adjustment": "none",
            }
        )
        return normalize_bars(df)


class AkshareProvider:
    name = "akshare"

    _daily_functions = {
        "a_share": "stock_zh_a_hist",
        "hk": "stock_hk_hist",
        "us": "stock_us_hist",
    }
    _minute_functions = {
        "a_share": "stock_zh_a_hist_min_em",
        "hk": "stock_hk_hist_min_em",
        "us": "stock_us_hist_min_em",
    }
    _daily_periods = {"1d": "daily", "1w": "weekly", "1mo": "monthly"}
    _minute_periods = {"5m": "5", "15m": "15", "30m": "30", "1h": "60"}

    def __init__(self, adjust: str = "qfq") -> None:
        self.adjust = adjust

    def fetch_ohlcv(self, request: FetchRequest) -> pd.DataFrame:
        if request.market not in {"a_share", "hk", "us"}:
            raise MarketDataError(f"AKShare does not support market: {request.market}")
        if request.timeframe in self._daily_periods:
            raw = self._fetch_daily(request)
        elif request.timeframe in self._minute_periods:
            raw = self._fetch_minute(request)
        else:
            raise MarketDataError(f"Unsupported AKShare timeframe: {request.timeframe}")
        return _standardize_akshare_frame(raw, request, self.name)

    def _ak(self):
        try:
            import akshare as ak
        except ImportError as exc:
            raise MarketDataError("AKShare provider requires `pip install akshare`.") from exc
        return ak

    def _fetch_daily(self, request: FetchRequest) -> pd.DataFrame:
        ak = self._ak()
        func = getattr(ak, self._daily_functions[request.market])
        kwargs = {
            "symbol": request.provider_symbol,
            "period": self._daily_periods[request.timeframe],
            "start_date": request.start.strftime("%Y%m%d"),
            "end_date": request.end.strftime("%Y%m%d"),
        }
        if request.market in {"a_share", "hk", "us"}:
            kwargs["adjust"] = request.options.get("adjust", self.adjust)
        try:
            return _call_with_fallbacks(func, kwargs)
        except Exception as exc:
            if _is_requests_error(exc):
                raise
            if request.market == "a_share" and request.timeframe == "1d":
                return self._fetch_a_share_daily_fallback(request)
            raise

    def _fetch_a_share_daily_fallback(self, request: FetchRequest) -> pd.DataFrame:
        ak = self._ak()
        symbol = _a_share_prefixed_symbol(request.provider_symbol)
        adjust = request.options.get("adjust", self.adjust)
        kwargs = {
            "symbol": symbol,
            "start_date": request.start.strftime("%Y%m%d"),
            "end_date": request.end.strftime("%Y%m%d"),
            "adjust": adjust if adjust != "none" else "",
        }
        try:
            return ak.stock_zh_a_daily(**kwargs)
        except Exception:
            df = ak.stock_zh_a_hist_tx(**kwargs)
            if "volume" not in df.columns and "amount" in df.columns:
                df = df.copy()
                df["volume"] = pd.to_numeric(df["amount"], errors="coerce") * 100
                df["amount"] = pd.NA
            return df

    def _fetch_minute(self, request: FetchRequest) -> pd.DataFrame:
        ak = self._ak()
        func = getattr(ak, self._minute_functions[request.market])
        kwargs = {
            "symbol": request.provider_symbol,
            "start_date": request.start.strftime("%Y-%m-%d %H:%M:%S"),
            "end_date": request.end.strftime("%Y-%m-%d %H:%M:%S"),
            "period": self._minute_periods[request.timeframe],
            "adjust": request.options.get("adjust", self.adjust),
        }
        return _call_with_fallbacks(func, kwargs)


class BaoStockProvider:
    name = "baostock"

    _frequencies = {"5m": "5", "15m": "15", "30m": "30", "1h": "60", "1d": "d", "1w": "w", "1mo": "m"}

    def fetch_ohlcv(self, request: FetchRequest) -> pd.DataFrame:
        if request.market != "a_share":
            raise MarketDataError(f"BaoStock only supports A-share data, got {request.market}")
        if request.timeframe not in self._frequencies:
            raise MarketDataError(f"Unsupported BaoStock timeframe: {request.timeframe}")
        try:
            import baostock as bs
        except ImportError as exc:
            raise MarketDataError("BaoStock provider requires `pip install baostock`.") from exc

        login = bs.login()
        if login.error_code != "0":
            raise MarketDataError(f"BaoStock login failed: {login.error_msg}")
        try:
            fields = "date,time,code,open,high,low,close,volume,amount,adjustflag"
            if request.timeframe in {"1d", "1w", "1mo"}:
                fields = "date,code,open,high,low,close,volume,amount,adjustflag"
            result = bs.query_history_k_data_plus(
                _baostock_symbol(request.provider_symbol),
                fields,
                start_date=request.start.strftime("%Y-%m-%d"),
                end_date=request.end.strftime("%Y-%m-%d"),
                frequency=self._frequencies[request.timeframe],
                adjustflag=_baostock_adjustflag(str(request.options.get("adjust", "qfq"))),
            )
            if result.error_code != "0":
                raise MarketDataError(f"BaoStock query failed: {result.error_msg}")
            rows = []
            while result.next():
                rows.append(result.get_row_data())
        finally:
            bs.logout()

        if not rows:
            return pd.DataFrame()
        raw = pd.DataFrame(rows, columns=result.fields)
        if request.timeframe in {"5m", "15m", "30m", "1h"}:
            raw["ts"] = pd.to_datetime(raw["time"].str.slice(0, 14), format="%Y%m%d%H%M%S")
        else:
            raw["ts"] = pd.to_datetime(raw["date"])
        raw["market"] = request.market
        raw["symbol"] = request.symbol
        raw["timeframe"] = request.timeframe
        raw["source"] = self.name
        raw["adjusted"] = raw["adjustflag"].eq("2")
        raw["adjustment"] = raw["adjustflag"].map({"1": "hfq", "2": "qfq", "3": "none"}).fillna("unknown")
        return normalize_bars(raw)


class CcxtCryptoProvider:
    name = "ccxt"

    _timeframes = {"5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h", "1d": "1d", "1w": "1w", "1mo": "1M"}

    def __init__(self, exchange_id: str = "binance", limit: int = 1000) -> None:
        self.exchange_id = exchange_id
        self.limit = limit

    def fetch_ohlcv(self, request: FetchRequest) -> pd.DataFrame:
        if request.timeframe not in self._timeframes:
            raise MarketDataError(f"Unsupported CCXT timeframe: {request.timeframe}")
        try:
            import ccxt
        except ImportError as exc:
            exchange_id = request.options.get("exchange", self.exchange_id)
            if exchange_id == "binance":
                return self._fetch_binance_rest(request)
            if exchange_id == "okx":
                return self._fetch_okx_rest(request)
            raise MarketDataError("CCXT provider requires `pip install ccxt`.") from exc

        exchange_id = request.options.get("exchange", self.exchange_id)
        exchange_cls = getattr(ccxt, exchange_id)
        exchange = exchange_cls({"enableRateLimit": True})
        ccxt_timeframe = self._timeframes[request.timeframe]
        if hasattr(exchange, "load_markets"):
            exchange.load_markets()
        if getattr(exchange, "timeframes", None) and ccxt_timeframe not in exchange.timeframes:
            raise MarketDataError(f"{exchange_id} does not advertise timeframe {ccxt_timeframe}")

        start_ms = int(pd.Timestamp(request.start).timestamp() * 1000)
        end_ms = int(pd.Timestamp(request.end).timestamp() * 1000)
        step_ms = int(TIMEFRAMES[request.timeframe].total_seconds() * 1000)
        rows: list[list[float]] = []
        since = start_ms
        while since <= end_ms:
            batch = exchange.fetch_ohlcv(request.provider_symbol, ccxt_timeframe, since=since, limit=self.limit)
            if not batch:
                break
            rows.extend(row for row in batch if start_ms <= row[0] <= end_ms)
            last_ts = int(batch[-1][0])
            next_since = last_ts + step_ms
            if next_since <= since:
                break
            since = next_since

        if not rows:
            return pd.DataFrame()
        raw = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        raw["ts"] = pd.to_datetime(raw["ts"], unit="ms", utc=True).dt.tz_convert(None)
        raw["amount"] = raw["volume"] * raw["close"]
        raw["market"] = request.market
        raw["symbol"] = request.symbol
        raw["timeframe"] = request.timeframe
        raw["source"] = f"ccxt:{exchange_id}"
        raw["adjusted"] = False
        raw["adjustment"] = "none"
        return normalize_bars(raw)

    def _fetch_binance_rest(self, request: FetchRequest) -> pd.DataFrame:
        import requests

        interval = self._timeframes[request.timeframe]
        symbol = request.provider_symbol.replace("/", "").upper()
        start_ms = int(pd.Timestamp(request.start).timestamp() * 1000)
        end_ms = int(pd.Timestamp(request.end).timestamp() * 1000)
        step_ms = int(TIMEFRAMES[request.timeframe].total_seconds() * 1000)
        rows = []
        since = start_ms
        while since <= end_ms:
            response = requests.get(
                "https://api.binance.com/api/v3/klines",
                params={
                    "symbol": symbol,
                    "interval": interval,
                    "startTime": since,
                    "endTime": end_ms,
                    "limit": self.limit,
                },
                timeout=20,
            )
            response.raise_for_status()
            batch = response.json()
            if not batch:
                break
            rows.extend(row for row in batch if start_ms <= int(row[0]) <= end_ms)
            last_ts = int(batch[-1][0])
            next_since = last_ts + step_ms
            if next_since <= since:
                break
            since = next_since

        if not rows:
            return pd.DataFrame()
        raw = pd.DataFrame(
            rows,
            columns=[
                "ts",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "close_time",
                "quote_volume",
                "trade_count",
                "taker_buy_base",
                "taker_buy_quote",
                "ignore",
            ],
        )
        raw["ts"] = pd.to_datetime(raw["ts"], unit="ms", utc=True).dt.tz_convert(None)
        raw["amount"] = pd.to_numeric(raw["quote_volume"], errors="coerce")
        raw["market"] = request.market
        raw["symbol"] = request.symbol
        raw["timeframe"] = request.timeframe
        raw["source"] = "binance-rest"
        raw["adjusted"] = False
        raw["adjustment"] = "none"
        return normalize_bars(raw)

    def _fetch_okx_rest(self, request: FetchRequest) -> pd.DataFrame:
        import requests

        interval = {
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1H",
            "1d": "1Dutc",
            "1w": "1Wutc",
            "1mo": "1Mutc",
        }[request.timeframe]
        inst_id = request.provider_symbol.replace("/", "-").upper()
        start_ms = int(pd.Timestamp(request.start).timestamp() * 1000)
        end_ms = int(pd.Timestamp(request.end).timestamp() * 1000)
        cursor = end_ms
        rows = []
        while cursor >= start_ms:
            response = requests.get(
                "https://www.okx.com/api/v5/market/history-candles",
                params={"instId": inst_id, "bar": interval, "after": str(cursor), "limit": "100"},
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") != "0":
                raise MarketDataError(f"OKX error: {payload}")
            batch = payload.get("data") or []
            if not batch:
                break
            parsed = [row for row in batch if start_ms <= int(row[0]) <= end_ms]
            rows.extend(parsed)
            oldest = min(int(row[0]) for row in batch)
            next_cursor = oldest - 1
            if next_cursor >= cursor:
                break
            cursor = next_cursor

        if not rows:
            return pd.DataFrame()
        raw = pd.DataFrame(
            rows,
            columns=["ts", "open", "high", "low", "close", "volume", "volume_ccy", "amount", "confirm"],
        )
        raw["ts"] = pd.to_datetime(raw["ts"].astype("int64"), unit="ms", utc=True).dt.tz_convert(None)
        raw["market"] = request.market
        raw["symbol"] = request.symbol
        raw["timeframe"] = request.timeframe
        raw["source"] = "okx-rest"
        raw["adjusted"] = False
        raw["adjustment"] = "none"
        return normalize_bars(raw)


def provider_for(request: FetchRequest, provider_name: str = "auto") -> MarketDataProvider:
    name = provider_name or request.provider or "auto"
    if name == "auto":
        name = request.provider or ("ccxt" if request.market == "crypto" else "baostock" if request.market == "a_share" else "akshare")
    if name == "synthetic":
        return SyntheticProvider()
    if name == "akshare":
        return AkshareProvider()
    if name == "baostock":
        return BaoStockProvider()
    if name == "ccxt":
        return CcxtCryptoProvider(exchange_id=request.options.get("exchange", "binance"))
    raise MarketDataError(f"Unknown market data provider: {name}")


def _call_with_fallbacks(func, kwargs: dict[str, Any]) -> pd.DataFrame:
    attempts = [
        kwargs,
        {key: value for key, value in kwargs.items() if key != "adjust"},
        {key: value for key, value in kwargs.items() if key not in {"adjust", "period"}},
    ]
    last_error: Exception | None = None
    for attempt in attempts:
        try:
            return func(**attempt)
        except TypeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return pd.DataFrame()


def _is_requests_error(exc: Exception) -> bool:
    try:
        import requests
    except ImportError:
        return False
    return isinstance(exc, requests.RequestException)


def _a_share_prefixed_symbol(symbol: str) -> str:
    if symbol.startswith(("sh", "sz", "bj")):
        return symbol
    if symbol.startswith("920"):
        return f"bj{symbol}"
    if symbol.startswith(("5", "6", "9")):
        return f"sh{symbol}"
    if symbol.startswith(("4", "8")):
        return f"bj{symbol}"
    return f"sz{symbol}"


def _baostock_symbol(symbol: str) -> str:
    value = symbol.lower().replace("_", ".")
    if value.startswith(("sh.", "sz.", "bj.")):
        return value
    if value.startswith(("sh", "sz", "bj")) and len(value) == 8:
        return f"{value[:2]}.{value[2:]}"
    code = value.zfill(6)
    if code.startswith("920"):
        return f"bj.{code}"
    if code.startswith(("5", "6", "9")):
        return f"sh.{code}"
    if code.startswith(("4", "8")):
        return f"bj.{code}"
    return f"sz.{code}"


def _baostock_adjustflag(adjust: str) -> str:
    if adjust in {"qfq", "front", "2", "前复权"}:
        return "2"
    if adjust in {"hfq", "back", "1", "后复权"}:
        return "1"
    return "3"


def _standardize_akshare_frame(raw: pd.DataFrame, request: FetchRequest, source: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    df = raw.copy()
    rename = {
        "日期": "ts",
        "时间": "ts",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量": "volume",
        "成交额": "amount",
        "date": "ts",
        "time": "ts",
    }
    df = df.rename(columns={key: value for key, value in rename.items() if key in df.columns})
    required = {"ts", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise MarketDataError(f"AKShare returned unsupported columns; missing {sorted(missing)} from {list(raw.columns)}")
    df["market"] = request.market
    df["symbol"] = request.symbol
    df["timeframe"] = request.timeframe
    df["source"] = source
    adjustment = str(request.options.get("adjust", "qfq") or "none")
    adjusted = adjustment not in {"", "none", "None", "不复权"}
    df["adjusted"] = adjusted
    df["adjustment"] = adjustment if adjusted else "none"
    df = df[df["ts"].notna()]
    df["ts"] = pd.to_datetime(df["ts"])
    df = df[(df["ts"] >= request.start) & (df["ts"] <= request.end)]
    return normalize_bars(df)
