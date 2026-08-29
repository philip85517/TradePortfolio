from __future__ import annotations

import argparse
import signal
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.market_data_backfill import CoverageWindow, build_coverage_requests, inspect_coverage
from src.market_data_providers import FetchRequest, _baostock_adjustflag, _baostock_symbol
from src.market_data_store import (
    DEFAULT_MARKET_DB_PATH,
    _record_update_attempt,
    connect_market_db,
    load_universe_from_db,
    normalize_bars,
    upsert_bars,
)
from src.market_data_updater import Instrument

FREQUENCIES = {"5m": "5", "15m": "15", "30m": "30", "1h": "60", "1d": "d", "1w": "w", "1mo": "m"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bulk download A-share OHLCV from BaoStock with one login session")
    parser.add_argument("--db", default=str(DEFAULT_MARKET_DB_PATH))
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--timeframes", default="5m,15m,30m,1h,1d,1w,1mo")
    parser.add_argument("--adjust", default="qfq")
    parser.add_argument("--only-missing", action="store_true")
    parser.add_argument("--coverage-mode", choices=["none", "edges"], default="edges", help="Fetch only missing leading/trailing date ranges.")
    parser.add_argument("--coverage-only", action="store_true", help="Write the coverage report without downloading bars.")
    parser.add_argument("--coverage-report", default=None, help="Write a CSV coverage audit to this path.")
    parser.add_argument("--target", choices=["existing-price", "universe"], default="existing-price")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--retry-delay", type=float, default=1.0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--progress-every", type=int, default=10)
    parser.add_argument("--request-timeout", type=int, default=240)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    end = pd.to_datetime(args.end_date).normalize() if args.end_date else pd.Timestamp(date.today())
    start = pd.to_datetime(args.start_date).normalize() if args.start_date else end - timedelta(days=365 * args.years + 2)
    timeframes = [item.strip() for item in args.timeframes.split(",") if item.strip()]
    universe = load_universe_from_db(args.db, markets=["a_share"])
    if universe.empty:
        raise SystemExit("No A-share universe rows found. Run scripts/build_market_universe.py first.")
    universe = universe.sort_values("symbol")
    if args.target == "existing-price":
        universe = filter_to_existing_price_symbols(universe, args.db, timeframes)
    if args.only_missing and args.coverage_mode == "none":
        universe = filter_missing(universe, args.db, timeframes)
    if args.offset:
        universe = universe.iloc[args.offset :]
    if args.limit is not None:
        universe = universe.head(args.limit)

    instruments = build_instruments(universe, adjust=args.adjust)
    if args.coverage_mode == "edges":
        requests = build_coverage_requests(instruments, timeframes, start, end, args.db)
    else:
        requests = [
            build_request(instrument, timeframe, start, end)
            for instrument in instruments
            for timeframe in timeframes
        ]
    coverage = inspect_coverage(args.db, instruments, timeframes, CoverageWindow(start=start, end=end))
    if args.coverage_report:
        Path(args.coverage_report).parent.mkdir(parents=True, exist_ok=True)
        coverage.to_csv(args.coverage_report, index=False)
    print(f"Prepared {len(requests):,} ranges for {len(instruments):,} A-share symbols, {start.date()} to {end.date()}", flush=True)
    if args.coverage_only:
        if not args.coverage_report:
            raise SystemExit("--coverage-only requires --coverage-report")
        print(f"Coverage report written to {args.coverage_report}")
        return

    import baostock as bs

    login = bs.login()
    if login.error_code != "0":
        raise SystemExit(f"BaoStock login failed: {login.error_msg}")
    total_rows = 0
    failures: list[str] = []
    existing = existing_symbol_timeframes(args.db, timeframes) if args.only_missing and args.coverage_mode == "none" else set()
    con = connect_market_db(args.db)
    try:
        for index, request in enumerate(requests, start=1):
            if args.only_missing and (request.symbol, request.timeframe) in existing:
                continue
            symbol_rows = 0
            try:
                data = fetch_with_retries(
                    bs,
                    request,
                    adjust=args.adjust,
                    timeout=args.request_timeout,
                    retries=args.retries,
                    retry_delay=args.retry_delay,
                )
                if data.empty:
                    failures.append(f"{request.symbol} {request.timeframe} {request.start.date()}..{request.end.date()}: empty")
                    _record_update_attempt(
                        con,
                        request.market,
                        request.symbol,
                        request.timeframe,
                        "baostock",
                        request.start,
                        request.end,
                        rows_written=0,
                        note="empty provider response after successful query",
                        status="empty",
                        attempts=1,
                    )
                elif args.coverage_mode == "edges":
                    rows = insert_new_bars(
                        con,
                        data,
                        source="baostock",
                        note=f"coverage backfill {args.adjust} {request.start.date()}..{request.end.date()}",
                    )
                    total_rows += rows
                    symbol_rows = rows
                else:
                    result = upsert_bars(
                        data,
                        db_path=args.db,
                        source="baostock",
                        note=f"bulk baostock {args.adjust} {request.start.date()}..{request.end.date()}",
                    )
                    rows = result["rows"]
                    total_rows += rows
                    symbol_rows = rows
            except Exception as exc:  # noqa: BLE001 - keep the batch moving.
                failures.append(f"{request.symbol} {request.timeframe} {request.start.date()}..{request.end.date()}: {exc}")
                _record_update_attempt(
                    con,
                    request.market,
                    request.symbol,
                    request.timeframe,
                    "baostock",
                    request.start,
                    request.end,
                    rows_written=0,
                    note=str(exc),
                    status="failed",
                    attempts=args.retries + 1,
                )
                bs.logout()
                login = bs.login()
                if login.error_code != "0":
                    raise SystemExit(f"BaoStock re-login failed: {login.error_msg}")
            if args.sleep:
                time.sleep(args.sleep)
            if index % args.progress_every == 0 or index == len(requests):
                print(
                    f"Completed {index}/{len(requests)} ranges, "
                    f"last={request.symbol} {request.timeframe}, last_rows={symbol_rows:,}, "
                    f"total_rows={total_rows:,}, failures={len(failures)}",
                    flush=True,
                )
    finally:
        con.close()
        bs.logout()

    print(f"Downloaded {total_rows:,} rows across {len(requests):,} ranges into {args.db}")
    if failures:
        print("Failures:")
        for failure in failures[:100]:
            print(f"- {failure}")
        if len(failures) > 100:
            print(f"... {len(failures) - 100} more failures")
    if args.coverage_report:
        refreshed = inspect_coverage(args.db, instruments, timeframes, CoverageWindow(start=start, end=end))
        refreshed.to_csv(args.coverage_report, index=False)
        print(f"Refreshed coverage report: {args.coverage_report}; complete={int(refreshed['coverage_ok'].sum())}/{len(refreshed)}")


def build_instruments(universe: pd.DataFrame, adjust: str) -> list[Instrument]:
    return [
        Instrument(
            market=row.market,
            symbol=row.symbol,
            source_symbol=row.source_symbol if pd.notna(row.source_symbol) else row.symbol,
            provider=row.provider if pd.notna(row.provider) else "baostock",
            options={"adjust": adjust},
        )
        for row in universe.itertuples(index=False)
    ]


def build_request(instrument, timeframe: str, start: pd.Timestamp, end: pd.Timestamp):
    return FetchRequest(
        market=instrument.market,
        symbol=instrument.symbol,
        timeframe=timeframe,
        start=start,
        end=end,
        source_symbol=instrument.source_symbol,
        provider=instrument.provider,
        options=instrument.options,
    )


def fetch_with_retries(bs, request, adjust: str, timeout: int, retries: int, retry_delay: float) -> pd.DataFrame:
    last_error = None
    for attempt in range(retries + 1):
        try:
            return run_with_timeout(
                lambda: fetch_baostock_bars(
                    bs,
                    symbol=request.symbol,
                    source_symbol=request.source_symbol or request.symbol,
                    timeframe=request.timeframe,
                    start=request.start,
                    end=request.end,
                    adjust=adjust,
                ),
                seconds=timeout,
            )
        except Exception as exc:  # noqa: BLE001 - retry provider/network failures.
            last_error = exc
            if attempt == retries:
                break
            if retry_delay:
                time.sleep(retry_delay * (2**attempt))
    assert last_error is not None
    raise last_error


def fetch_baostock_bars(bs, symbol: str, source_symbol: str, timeframe: str, start: pd.Timestamp, end: pd.Timestamp, adjust: str) -> pd.DataFrame:
    fields = "date,time,code,open,high,low,close,volume,amount,adjustflag"
    if timeframe in {"1d", "1w", "1mo"}:
        fields = "date,code,open,high,low,close,volume,amount,adjustflag"
    result = bs.query_history_k_data_plus(
        _baostock_symbol(source_symbol),
        fields,
        start_date=start.strftime("%Y-%m-%d"),
        end_date=end.strftime("%Y-%m-%d"),
        frequency=FREQUENCIES[timeframe],
        adjustflag=_baostock_adjustflag(adjust),
    )
    if result.error_code != "0":
        raise RuntimeError(result.error_msg)
    rows = []
    while result.next():
        rows.append(result.get_row_data())
    if not rows:
        return pd.DataFrame()
    raw = pd.DataFrame(rows, columns=result.fields)
    if timeframe in {"5m", "15m", "30m", "1h"}:
        raw["ts"] = pd.to_datetime(raw["time"].str.slice(0, 14), format="%Y%m%d%H%M%S")
    else:
        raw["ts"] = pd.to_datetime(raw["date"])
    raw["market"] = "a_share"
    raw["symbol"] = symbol
    raw["timeframe"] = timeframe
    raw["source"] = "baostock"
    raw["adjusted"] = raw["adjustflag"].eq("2")
    raw["adjustment"] = raw["adjustflag"].map({"1": "hfq", "2": "qfq", "3": "none"}).fillna("unknown")
    return raw


def run_with_timeout(func, seconds: int):
    if seconds <= 0:
        return func()

    def handler(signum, frame):  # noqa: ARG001
        raise TimeoutError(f"BaoStock request timed out after {seconds}s")

    previous = signal.signal(signal.SIGALRM, handler)
    signal.alarm(seconds)
    try:
        return func()
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def filter_missing(universe: pd.DataFrame, db_path: str | Path, timeframes: list[str]) -> pd.DataFrame:
    with connect_market_db(db_path) as con:
        actual = con.execute(
            """
            SELECT market, symbol, count(DISTINCT timeframe) AS downloaded_timeframes
            FROM market_ohlcv
            WHERE market = 'a_share'
              AND timeframe IN (SELECT unnest(?::VARCHAR[]))
            GROUP BY market, symbol
            """,
            [timeframes],
        ).fetch_df()
    if actual.empty:
        return universe
    merged = universe.merge(actual[["symbol", "downloaded_timeframes"]], how="left", on="symbol")
    merged["downloaded_timeframes"] = merged["downloaded_timeframes"].fillna(0).astype(int)
    return merged[merged["downloaded_timeframes"] < len(timeframes)].drop(columns=["downloaded_timeframes"])


def existing_symbol_timeframes(db_path: str | Path, timeframes: list[str]) -> set[tuple[str, str]]:
    with connect_market_db(db_path) as con:
        rows = con.execute(
            """
            SELECT symbol, timeframe
            FROM market_ohlcv
            WHERE market = 'a_share'
              AND timeframe IN (SELECT unnest(?::VARCHAR[]))
            GROUP BY symbol, timeframe
            """,
            [timeframes],
        ).fetchall()
    return {(str(symbol), str(timeframe)) for symbol, timeframe in rows}


def insert_new_bars(con, data: pd.DataFrame, source: str, note: str | None = None) -> int:
    normalized = normalize_bars(data)
    if normalized.empty:
        return 0
    normalized["source"] = source
    con.register("incoming_new_bars", normalized)
    con.execute(
        """
        INSERT INTO market_ohlcv (
            market, symbol, timeframe, ts, trade_date, open, high, low,
            close, volume, amount, source, adjusted, adjustment
        )
        SELECT market, symbol, timeframe, ts, trade_date, open, high, low,
               close, volume, amount, source, adjusted, adjustment
        FROM incoming_new_bars
        """
    )
    for (market, symbol, timeframe), group in normalized.groupby(["market", "symbol", "timeframe"]):
        con.execute(
            """
            INSERT INTO market_update_log (
                market, symbol, timeframe, source, start_ts, end_ts, rows_written, note
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                market,
                symbol,
                timeframe,
                source,
                group["ts"].min().to_pydatetime(),
                group["ts"].max().to_pydatetime(),
                len(group),
                note,
            ],
        )
    con.unregister("incoming_new_bars")
    return int(len(normalized))


def filter_to_existing_price_symbols(universe: pd.DataFrame, db_path: str | Path, timeframes: list[str]) -> pd.DataFrame:
    if universe.empty:
        return universe
    with connect_market_db(db_path) as con:
        actual = con.execute(
            """
            SELECT DISTINCT market, symbol
            FROM market_ohlcv
            WHERE timeframe IN (SELECT unnest(?::VARCHAR[]))
            """,
            [timeframes],
        ).fetch_df()
    if actual.empty:
        return universe
    return universe.merge(actual, how="inner", on=["market", "symbol"])


if __name__ == "__main__":
    main()
