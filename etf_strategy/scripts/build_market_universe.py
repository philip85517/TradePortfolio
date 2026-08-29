from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.market_data_store import DEFAULT_MARKET_DB_PATH, upsert_universe
from src.market_universe import discover_universe


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover full-market instrument universe and store it in DuckDB")
    parser.add_argument("--db", default=str(DEFAULT_MARKET_DB_PATH))
    parser.add_argument("--markets", nargs="*", default=["a_share", "hk", "crypto"])
    parser.add_argument("--include-us", action="store_true", help="US discovery uses a very slow free Sina endpoint.")
    parser.add_argument("--crypto-quote", default="USDT", help="Set empty string to include all OKX spot quote currencies.")
    parser.add_argument("--csv", default="data/processed/market_universe.csv")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    quote = args.crypto_quote if args.crypto_quote else None
    universe = discover_universe(args.markets, include_us=args.include_us, crypto_quote=quote)
    Path(args.csv).parent.mkdir(parents=True, exist_ok=True)
    universe.to_csv(args.csv, index=False)
    result = upsert_universe(universe, args.db)
    print(f"Discovered {len(universe):,} instruments across {result['markets']} markets")
    print(universe.groupby("market")["symbol"].nunique().sort_index().to_string())
    print(f"Wrote {args.csv} and market_universe table in {args.db}")


if __name__ == "__main__":
    main()
