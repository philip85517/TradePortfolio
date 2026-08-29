from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.market_data_quality import ValidationWindow, validate_database, verify_qfq_against_unadjusted, write_validation_report
from src.market_data_store import DEFAULT_MARKET_DB_PATH, TIMEFRAMES
from src.market_data_updater import filter_instruments, load_market_universe


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate local multi-market OHLCV data coverage and adjustment quality")
    parser.add_argument("--config", default="config/market_data_universe.yaml")
    parser.add_argument("--db", default=str(DEFAULT_MARKET_DB_PATH))
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--timeframes", default=",".join(TIMEFRAMES.keys()))
    parser.add_argument("--markets", nargs="*", default=None)
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--output", default="data/processed/market_data_validation.md")
    parser.add_argument("--skip-qfq-source-check", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    end = pd.to_datetime(args.end_date).normalize() if args.end_date else pd.Timestamp(date.today())
    start = pd.to_datetime(args.start_date).normalize() if args.start_date else end - timedelta(days=365 * args.years + 2)
    timeframes = [item.strip() for item in args.timeframes.split(",") if item.strip()]
    instruments, _ = load_market_universe(args.config)
    instruments = filter_instruments(instruments, markets=args.markets, symbols=args.symbols)
    window = ValidationWindow(start=start, end=end)

    coverage, problems = validate_database(args.db, instruments, timeframes, window)
    qfq_checks = pd.DataFrame()
    if not args.skip_qfq_source_check:
        qfq_checks = verify_qfq_against_unadjusted(instruments, window)
    write_validation_report(args.output, coverage, qfq_checks, window)

    print(f"Wrote validation report to {args.output}")
    print(f"Checked {len(coverage)} market/symbol/timeframe rows; problems={len(problems)}")
    if not problems.empty:
        print(problems[["market", "symbol", "timeframe", "rows", "start_ts", "end_ts", "adjustments", "status"]].to_string(index=False))


if __name__ == "__main__":
    main()
