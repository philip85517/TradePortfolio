from __future__ import annotations

import argparse
import io
import sys
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.market_data_store import DEFAULT_MARKET_DB_PATH, upsert_bars

BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"
COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_asset_volume",
    "number_of_trades",
    "taker_buy_base_asset_volume",
    "taker_buy_quote_asset_volume",
    "ignore",
]
TIMEFRAME_TO_BINANCE = {
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "1d": "1d",
    "1w": "1w",
    "1mo": "1mo",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import free Binance public monthly kline ZIP files into DuckDB")
    parser.add_argument("--symbol", default="BTCUSDT", help="Binance symbol, e.g. BTCUSDT")
    parser.add_argument("--store-symbol", default="BTC/USDT", help="Normalized symbol stored in DuckDB")
    parser.add_argument("--market", default="crypto")
    parser.add_argument("--timeframes", default="5m,15m,30m,1h,1d,1w,1mo")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--db", default=str(DEFAULT_MARKET_DB_PATH))
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--retries", type=int, default=3)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start = pd.to_datetime(args.start_date).normalize()
    end = pd.to_datetime(args.end_date).normalize()
    timeframes = [item.strip() for item in args.timeframes.split(",") if item.strip()]
    total_rows = 0
    failures = []
    for timeframe in timeframes:
        binance_interval = TIMEFRAME_TO_BINANCE[timeframe]
        for month in pd.period_range(start=start, end=end, freq="M"):
            try:
                print(f"Fetching {timeframe} {month}", flush=True)
                data = fetch_month(
                    args.symbol,
                    binance_interval,
                    month,
                    args.market,
                    args.store_symbol,
                    timeframe,
                    start,
                    end,
                    timeout=args.timeout,
                    retries=args.retries,
                )
                if data.empty:
                    continue
                result = upsert_bars(
                    data,
                    db_path=args.db,
                    source="binance-bulk",
                    note=f"binance monthly zip {args.symbol} {binance_interval} {month}",
                )
                total_rows += result["rows"]
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    continue
                failures.append(f"{timeframe} {month}: {exc}")
            except Exception as exc:  # noqa: BLE001 - import should keep processing months.
                failures.append(f"{timeframe} {month}: {exc}")
            if args.sleep:
                import time

                time.sleep(args.sleep)
    print(f"Imported {total_rows:,} rows from Binance public data into {args.db}")
    if failures:
        print("Failures:")
        for failure in failures:
            print(f"- {failure}")


def fetch_month(
    symbol: str,
    interval: str,
    month: pd.Period,
    market: str,
    store_symbol: str,
    timeframe: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    timeout: float = 20.0,
    retries: int = 3,
) -> pd.DataFrame:
    url = f"{BASE_URL}/{symbol}/{interval}/{symbol}-{interval}-{month.strftime('%Y-%m')}.zip"
    response = _get_with_retries(url, timeout=timeout, retries=retries)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        csv_name = archive.namelist()[0]
        with archive.open(csv_name) as fh:
            raw = pd.read_csv(fh, header=None, names=COLUMNS)
    open_time = pd.to_numeric(raw["open_time"], errors="coerce")
    unit = "us" if open_time.dropna().max() and open_time.dropna().max() > 10_000_000_000_000 else "ms"
    raw["ts"] = pd.to_datetime(open_time, unit=unit, utc=True).dt.tz_convert(None)
    raw = raw[(raw["ts"] >= start) & (raw["ts"] <= end)]
    if raw.empty:
        return pd.DataFrame()
    return pd.DataFrame(
        {
            "market": market,
            "symbol": store_symbol,
            "timeframe": timeframe,
            "ts": raw["ts"],
            "open": raw["open"],
            "high": raw["high"],
            "low": raw["low"],
            "close": raw["close"],
            "volume": raw["volume"],
            "amount": raw["quote_asset_volume"],
            "source": "binance-bulk",
            "adjusted": False,
            "adjustment": "none",
        }
    )


def _get_with_retries(url: str, timeout: float, retries: int) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.HTTPError:
            raise
        except Exception as exc:  # noqa: BLE001 - retry transient network stalls.
            last_error = exc
            if attempt == retries:
                break
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Failed to fetch {url}")


if __name__ == "__main__":
    main()
