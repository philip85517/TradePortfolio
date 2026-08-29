from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.market_data_store import DEFAULT_MARKET_DB_PATH, TIMEFRAMES, connect_market_db


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare expected universe scope with downloaded OHLCV coverage")
    parser.add_argument("--db", default=str(DEFAULT_MARKET_DB_PATH))
    parser.add_argument("--timeframes", default=",".join(TIMEFRAMES.keys()))
    parser.add_argument("--output", default="data/processed/market_scope_report.md")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    timeframes = [item.strip() for item in args.timeframes.split(",") if item.strip()]
    with connect_market_db(args.db) as con:
        universe = con.execute(
            """
            SELECT market, count(DISTINCT symbol) AS expected_symbols
            FROM market_universe
            GROUP BY market
            ORDER BY market
            """
        ).fetch_df()
        downloaded = con.execute(
            """
            SELECT market, timeframe, count(DISTINCT symbol) AS downloaded_symbols,
                   count(*) AS rows, min(ts) AS start_ts, max(ts) AS end_ts
            FROM market_ohlcv
            GROUP BY market, timeframe
            ORDER BY market, timeframe
            """
        ).fetch_df()
        gaps = con.execute(
            f"""
            WITH expected AS (
                SELECT market, symbol, unnest(?::VARCHAR[]) AS timeframe
                FROM market_universe
            ),
            actual AS (
                SELECT DISTINCT market, symbol, timeframe
                FROM market_ohlcv
            )
            SELECT expected.market, expected.timeframe,
                   count(*) AS expected_symbols,
                   count(actual.symbol) AS downloaded_symbols,
                   count(*) - count(actual.symbol) AS missing_symbols
            FROM expected
            LEFT JOIN actual
              ON expected.market = actual.market
             AND expected.symbol = actual.symbol
             AND expected.timeframe = actual.timeframe
            GROUP BY expected.market, expected.timeframe
            ORDER BY expected.market, expected.timeframe
            """,
            [timeframes],
        ).fetch_df()
    if not gaps.empty:
        gaps["status"] = gaps.apply(_gap_status, axis=1)

    lines = [
        "# Market Scope Report",
        "",
        "## Expected Universe",
        "",
        universe.to_markdown(index=False) if not universe.empty else "_No universe table rows._",
        "",
        "## Downloaded Coverage",
        "",
        downloaded.to_markdown(index=False) if not downloaded.empty else "_No downloaded bars._",
        "",
        "## Scope Gaps",
        "",
        gaps.to_markdown(index=False) if not gaps.empty else "_No gaps because no expected universe was loaded._",
        "",
    ]
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {path}")
    if not gaps.empty:
        print(gaps.to_string(index=False))


def _gap_status(row) -> str:
    if row["missing_symbols"] == 0:
        return "ok"
    if row["market"] in {"hk", "us"} and row["timeframe"] in {"5m", "15m", "30m", "1h"}:
        return "source_limit_free_intraday_history"
    return "missing_download"


if __name__ == "__main__":
    main()
