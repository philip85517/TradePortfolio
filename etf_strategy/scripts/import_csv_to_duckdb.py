from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data_store import DEFAULT_DB_PATH, database_summary, import_csv_to_db


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import normalized ETF CSV data into DuckDB")
    parser.add_argument("--csv", default="data/raw/etf_daily_5y.csv")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = import_csv_to_db(args.csv, args.db)
    summary = database_summary(args.db)
    print(f"Imported {result['rows']:,} rows for {result['symbols']:,} ETFs into {args.db}")
    print(
        f"Database now has {summary['rows']:,} rows, {summary['symbols']:,} ETFs, "
        f"{summary['start_date']} to {summary['end_date']}"
    )


if __name__ == "__main__":
    main()
