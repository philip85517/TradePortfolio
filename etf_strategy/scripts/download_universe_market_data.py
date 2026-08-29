from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.market_data_store import DEFAULT_MARKET_DB_PATH, TIMEFRAMES, connect_market_db, load_universe_from_db
from src.market_data_updater import Instrument, build_requests, update_market_data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download OHLCV for symbols stored in market_universe")
    parser.add_argument("--db", default=str(DEFAULT_MARKET_DB_PATH))
    parser.add_argument("--markets", nargs="*", default=["a_share", "hk", "crypto"])
    parser.add_argument("--timeframes", default="1d,1w,1mo")
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--only-missing", action="store_true")
    parser.add_argument("--max-symbols", type=int, default=None)
    parser.add_argument("--all", action="store_true", help="Allow downloading all matching universe symbols.")
    parser.add_argument("--sleep", type=float, default=0.05)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.all and args.max_symbols is None:
        raise SystemExit("Refusing an unbounded universe download. Pass --max-symbols N for a batch or --all explicitly.")

    end = pd.to_datetime(args.end_date).normalize() if args.end_date else pd.Timestamp(date.today())
    start = pd.to_datetime(args.start_date).normalize() if args.start_date else end - timedelta(days=365 * args.years + 2)
    timeframes = [item.strip() for item in args.timeframes.split(",") if item.strip()]
    universe = load_universe_from_db(args.db, markets=args.markets)
    if args.only_missing:
        universe = _filter_missing(universe, args.db, timeframes)
    if args.max_symbols is not None:
        universe = universe.head(args.max_symbols)
    instruments = [
        Instrument(
            market=row.market,
            symbol=row.symbol,
            source_symbol=row.source_symbol if pd.notna(row.source_symbol) else row.symbol,
            provider=row.provider if pd.notna(row.provider) else None,
            options={"adjust": "qfq"} if row.market in {"a_share", "hk", "us"} else {"exchange": "okx"},
        )
        for row in universe.itertuples(index=False)
    ]
    requests = build_requests(instruments, timeframes, start, end, args.db, incremental=args.incremental)
    print(f"Downloading {len(requests)} requests for {len(instruments)} symbols")
    result = update_market_data(requests, args.db, provider_name="auto", sleep_seconds=args.sleep)
    print(
        f"Completed {result['completed']}/{result['requests']} requests; "
        f"rows_written={result['rows_written']:,}; failures={len(result['failures'])}"
    )
    if result["failures"]:
        for failure in result["failures"][:50]:
            print(f"- {failure['market']}:{failure['symbol']} {failure['timeframe']}: {failure['reason']}")
        if len(result["failures"]) > 50:
            print(f"... {len(result['failures']) - 50} more failures")


def _filter_missing(universe: pd.DataFrame, db_path: str | Path, timeframes: list[str]) -> pd.DataFrame:
    if universe.empty:
        return universe
    with connect_market_db(db_path) as con:
        actual = con.execute(
            """
            SELECT market, symbol, count(DISTINCT timeframe) AS downloaded_timeframes
            FROM market_ohlcv
            WHERE timeframe IN (SELECT unnest(?::VARCHAR[]))
            GROUP BY market, symbol
            """,
            [timeframes],
        ).fetch_df()
    if actual.empty:
        return universe
    merged = universe.merge(actual, how="left", on=["market", "symbol"])
    merged["downloaded_timeframes"] = merged["downloaded_timeframes"].fillna(0).astype(int)
    return merged[merged["downloaded_timeframes"] < len(timeframes)].drop(columns=["downloaded_timeframes"])


if __name__ == "__main__":
    main()
