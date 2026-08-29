from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.market_data_store import DEFAULT_MARKET_DB_PATH, load_bars, upsert_bars


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Derive weekly/monthly bars from adjusted daily OHLCV bars")
    parser.add_argument("--db", default=str(DEFAULT_MARKET_DB_PATH))
    parser.add_argument("--markets", nargs="*", default=None)
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--timeframes", default="1w,1mo")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    timeframes = [item.strip() for item in args.timeframes.split(",") if item.strip()]
    daily = load_bars(args.db, markets=args.markets, symbols=args.symbols, timeframes=["1d"])
    if daily.empty:
        print("No daily bars found.")
        return
    total_rows = 0
    for timeframe in timeframes:
        derived = derive(daily, timeframe)
        result = upsert_bars(derived, db_path=args.db, source="derived-from-1d", note=f"derived {timeframe} from qfq daily bars")
        total_rows += result["rows"]
        print(f"Derived {result['rows']:,} {timeframe} rows")
    print(f"Derived total {total_rows:,} rows into {args.db}")


def derive(daily: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if timeframe == "1w":
        freq = "W-FRI"
    elif timeframe == "1mo":
        freq = "ME"
    else:
        raise ValueError("Only 1w and 1mo are supported")
    rows = []
    for (market, symbol), group in daily.sort_values("ts").groupby(["market", "symbol"]):
        indexed = group.set_index("ts")
        resampled = indexed.resample(freq).agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
                "amount": "sum",
                "adjusted": "last",
                "adjustment": "last",
            }
        )
        resampled = resampled.dropna(subset=["open", "high", "low", "close"]).reset_index()
        resampled["market"] = market
        resampled["symbol"] = symbol
        resampled["timeframe"] = timeframe
        resampled["source"] = "derived-from-1d"
        rows.append(resampled)
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True)


if __name__ == "__main__":
    main()
