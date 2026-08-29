from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.backtester import prepare_strategy_data
from src.data_loader import load_market_data
from src.position_sizing import calculate_position_size
from src.report import write_daily_plan
from src.scoring import build_watchlist, trailing_returns_matrix
from src.signals import calculate_initial_stop, generate_entry_signal
from src.utils import load_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate ETFStrategy daily plan")
    parser.add_argument("--config", default="config/strategy_config.yaml")
    parser.add_argument("--data", default=None)
    parser.add_argument("--date", required=True)
    parser.add_argument("--output-dir", default="outputs/reports")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    data = load_market_data(args.data)
    scored = prepare_strategy_data(data, config)
    date = pd.to_datetime(args.date)
    hist = scored[scored["date"] <= date]
    returns = trailing_returns_matrix(hist, date, config.get("candidate", {}).get("correlation_lookback", 60))
    watchlist = build_watchlist(scored, date, config, returns)

    buy_rows = []
    for _, row in watchlist.iterrows():
        if generate_entry_signal(row["symbol"], hist, config):
            entry_price = row["close"]
            stop_price = calculate_initial_stop(entry_price, row["atr20"], row["ema20"], None, config)
            sizing = calculate_position_size(entry_price, stop_price, config["strategy"].get("initial_cash", 1_000_000), config)
            if sizing["should_trade"]:
                buy_rows.append(
                    {
                        "symbol": row["symbol"],
                        "name": row.get("name", row["symbol"]),
                        "assumed_buy_price": entry_price,
                        "stop_price": stop_price,
                        "position_value": sizing["position_value"],
                        "max_loss": sizing["max_loss"],
                        "reason": "EMA20 pullback reclaim",
                    }
                )
    buy_signals = pd.DataFrame(buy_rows)
    path = Path(args.output_dir) / f"daily_plan_{date.strftime('%Y%m%d')}.md"
    write_daily_plan(date, pd.DataFrame(), watchlist, buy_signals, pd.DataFrame(), path)
    print(f"Daily plan written to {path}")


if __name__ == "__main__":
    main()

