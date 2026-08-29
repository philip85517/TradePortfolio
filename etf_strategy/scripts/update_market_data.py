from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.market_data_store import DEFAULT_MARKET_DB_PATH, TIMEFRAMES, market_database_summary
from src.market_data_updater import build_requests, filter_instruments, load_market_universe, update_market_data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update multi-market OHLCV data into DuckDB")
    parser.add_argument("--config", default="config/market_data_universe.yaml")
    parser.add_argument("--db", default=str(DEFAULT_MARKET_DB_PATH))
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--timeframes", default=",".join(TIMEFRAMES.keys()))
    parser.add_argument("--markets", nargs="*", default=None, help="Optional market filters, e.g. a_share us crypto")
    parser.add_argument("--symbols", nargs="*", default=None, help="Optional filters as market:symbol, e.g. us:AAPL")
    parser.add_argument("--provider", default="auto", choices=["auto", "akshare", "baostock", "ccxt", "synthetic"])
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--chunk-days", type=int, default=None, help="Fetch each request in N-day chunks. Use 1 for daily incremental refreshes.")
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--replace-db", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    end = pd.to_datetime(args.end_date).normalize() if args.end_date else pd.Timestamp(date.today())
    start = pd.to_datetime(args.start_date).normalize() if args.start_date else end - timedelta(days=365 * args.years + 2)
    timeframes = [item.strip() for item in args.timeframes.split(",") if item.strip()]

    db_path = Path(args.db)
    if args.replace_db and db_path.exists():
        db_path.unlink()

    instruments, configured_timeframes = load_market_universe(args.config)
    instruments = filter_instruments(instruments, markets=args.markets, symbols=args.symbols)
    if args.timeframes:
        selected_timeframes = timeframes
    else:
        selected_timeframes = configured_timeframes
    requests = build_requests(instruments, selected_timeframes, start, end, db_path, incremental=args.incremental)

    print(
        f"Prepared {len(requests)} requests for {len(instruments)} instruments, "
        f"{start.date()} to {end.date()}, timeframes={','.join(selected_timeframes)}"
    )
    if args.dry_run:
        for request in requests:
            print(
                f"- {request.market}:{request.symbol} {request.timeframe} "
                f"{request.start.isoformat()}..{request.end.isoformat()} provider={request.provider or args.provider}"
            )
        return

    result = update_market_data(
        requests,
        db_path=db_path,
        provider_name=args.provider,
        chunk_days=args.chunk_days,
        sleep_seconds=args.sleep,
    )
    summary = result["db_summary"]
    print(
        f"Wrote {result['rows_written']:,} rows into {db_path}. "
        f"Database now has {summary['rows']:,} rows, {summary['instruments']} instruments, "
        f"{summary['start_ts']} to {summary['end_ts']}"
    )
    if result["failures"]:
        print("Failures:")
        for failure in result["failures"]:
            print(f"- {failure['market']}:{failure['symbol']} {failure['timeframe']}: {failure['reason']}")
    else:
        print("All requests completed.")

    by_market = market_database_summary(db_path)["by_market"]
    if not by_market.empty:
        print(by_market.to_string(index=False))


if __name__ == "__main__":
    main()
