from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.market_data_store import DEFAULT_MARKET_DB_PATH, connect_market_db, load_universe_from_db, upsert_universe
from src.market_data_updater import Instrument, build_requests, update_market_data
from src.market_universe import discover_universe

DEFAULT_STOCK_MARKETS = ["a_share", "hk", "us"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover and update free A-share/HK/US stock OHLCV data")
    parser.add_argument("--db", default=str(DEFAULT_MARKET_DB_PATH))
    parser.add_argument("--markets", nargs="*", default=DEFAULT_STOCK_MARKETS)
    parser.add_argument("--timeframes", default="1d", help="Comma-separated bars. Default matches ETF daily bars: 1d.")
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--incremental", action="store_true", help="Start each symbol/timeframe after the latest stored bar.")
    parser.add_argument("--chunk-days", type=int, default=1, help="Fetch in N-day chunks; 1 gives day-by-day updates.")
    parser.add_argument("--sleep", type=float, default=0.05)
    parser.add_argument("--adjust", default="qfq", choices=["qfq", "hfq", "none"])
    parser.add_argument("--refresh-universe", action="store_true", help="Refresh the local stock universe before downloading.")
    parser.add_argument("--universe-csv", default="data/processed/stock_universe.csv")
    parser.add_argument("--only-missing", action="store_true", help="Download only symbols missing at least one requested timeframe.")
    parser.add_argument("--symbols", nargs="*", default=None, help="Optional filters as market:symbol, e.g. hk:00700 us:AAPL")
    parser.add_argument("--max-symbols", type=int, default=None, help="Limit this batch size.")
    parser.add_argument("--offset", type=int, default=0, help="Skip the first N sorted universe rows for manual batching.")
    parser.add_argument("--all", action="store_true", help="Allow downloading every selected stock symbol.")
    parser.add_argument("--provider", default="auto", choices=["auto", "akshare", "baostock", "synthetic"])
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    markets = list(dict.fromkeys(args.markets))
    timeframes = [item.strip() for item in args.timeframes.split(",") if item.strip()]
    if not timeframes:
        raise SystemExit("At least one timeframe is required.")
    if not args.all and args.max_symbols is None and args.symbols is None and not args.dry_run:
        raise SystemExit("Refusing an unbounded stock download. Pass --max-symbols N for a batch or --all explicitly.")

    db_path = Path(args.db)
    if args.dry_run and not db_path.exists():
        universe = _empty_universe()
    else:
        universe = load_universe_from_db(db_path, markets=markets)
    missing_markets = sorted(set(markets) - set(universe["market"].drop_duplicates())) if not universe.empty else markets
    if args.refresh_universe or missing_markets:
        if missing_markets and not args.refresh_universe:
            print(f"Universe missing markets {missing_markets}; refreshing selected stock universe.", flush=True)
        discovered = discover_universe(markets, include_us="us" in markets, crypto_quote=None)
        discovered = discovered[discovered["market"].isin(markets)].copy()
        if args.dry_run:
            print(f"Would refresh {len(discovered):,} stock universe rows across {discovered['market'].nunique()} markets.", flush=True)
            universe = discovered
        else:
            Path(args.universe_csv).parent.mkdir(parents=True, exist_ok=True)
            discovered.to_csv(args.universe_csv, index=False)
            result = upsert_universe(discovered, db_path)
            print(f"Refreshed {len(discovered):,} stock universe rows across {result['markets']} markets.", flush=True)
            universe = load_universe_from_db(db_path, markets=markets)

    universe = universe[universe["market"].isin(markets)].sort_values(["market", "symbol"]).reset_index(drop=True)
    if "status" in universe.columns:
        universe = universe[~universe["status"].isin(["no_price_data", "inactive", "delisted"])].reset_index(drop=True)
    if args.symbols:
        selected = parse_symbol_filters(args.symbols)
        universe = universe[universe.apply(lambda row: (row["market"], str(row["symbol"])) in selected, axis=1)].reset_index(drop=True)
    if args.only_missing:
        universe = filter_missing_timeframes(universe, db_path, timeframes)
    if args.offset:
        universe = universe.iloc[args.offset :].reset_index(drop=True)
    if args.max_symbols is not None:
        universe = universe.head(args.max_symbols)

    end = pd.to_datetime(args.end_date).normalize() if args.end_date else pd.Timestamp(date.today())
    start = pd.to_datetime(args.start_date).normalize() if args.start_date else end - timedelta(days=365 * args.years + 2)
    instruments = build_stock_instruments(universe, adjust=args.adjust)
    requests = build_requests(instruments, timeframes, start, end, db_path, incremental=args.incremental)

    print(
        f"Prepared {len(requests):,} requests for {len(instruments):,} stock symbols, "
        f"{start.date()} to {end.date()}, timeframes={','.join(timeframes)}",
        flush=True,
    )
    if args.dry_run:
        print(universe.groupby("market")["symbol"].nunique().sort_index().to_string() if not universe.empty else "No symbols selected.")
        for request in requests[:20]:
            print(
                f"- {request.market}:{request.symbol} source={request.source_symbol or request.symbol} "
                f"{request.timeframe} {request.start.date()}..{request.end.date()} provider={request.provider or args.provider}"
            )
        if len(requests) > 20:
            print(f"... {len(requests) - 20:,} more requests")
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
        f"Completed {result['completed']:,}/{result['requests']:,} requests; "
        f"rows_written={result['rows_written']:,}; failures={len(result['failures']):,}",
        flush=True,
    )
    print(
        f"Database now has {summary['rows']:,} rows, {summary['instruments']:,} instruments, "
        f"{summary['start_ts']} to {summary['end_ts']}",
        flush=True,
    )
    if result["failures"]:
        print("Failures:")
        for failure in result["failures"][:100]:
            print(f"- {failure['market']}:{failure['symbol']} {failure['timeframe']}: {failure['reason']}")
        if len(result["failures"]) > 100:
            print(f"... {len(result['failures']) - 100:,} more failures")


def build_stock_instruments(universe: pd.DataFrame, adjust: str = "qfq") -> list[Instrument]:
    normalized_adjust = "" if adjust == "none" else adjust
    instruments = []
    for row in universe.itertuples(index=False):
        provider = row.provider if pd.notna(row.provider) else None
        source_symbol = row.source_symbol if pd.notna(row.source_symbol) else row.symbol
        instruments.append(
            Instrument(
                market=row.market,
                symbol=row.symbol,
                source_symbol=source_symbol,
                provider=provider,
                options={"adjust": normalized_adjust},
            )
        )
    return instruments


def _empty_universe() -> pd.DataFrame:
    return pd.DataFrame(columns=["market", "symbol", "name", "source_symbol", "provider", "quote_ccy", "status"])


def parse_symbol_filters(values: list[str]) -> set[tuple[str, str]]:
    parsed = set()
    for value in values:
        if ":" not in value:
            raise SystemExit("Symbol filters must use market:symbol, for example hk:00700 or us:AAPL")
        market, symbol = value.split(":", 1)
        parsed.add((market.strip(), symbol.strip()))
    return parsed


def filter_missing_timeframes(universe: pd.DataFrame, db_path: str | Path, timeframes: list[str]) -> pd.DataFrame:
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
