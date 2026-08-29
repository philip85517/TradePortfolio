from __future__ import annotations

import argparse
from pathlib import Path

from src.backtester import run_backtest
from src.data_loader import load_market_data
from src.utils import load_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ETFStrategy backtest")
    parser.add_argument("--config", default="config/strategy_config.yaml")
    parser.add_argument("--data", default=None)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--initial-cash", type=float, default=None)
    parser.add_argument("--output-dir", default="outputs")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    if args.initial_cash is not None:
        config["strategy"]["initial_cash"] = args.initial_cash
    data = load_market_data(args.data)
    result = run_backtest(config, data, args.start_date, args.end_date, Path(args.output_dir))
    print("Backtest complete")
    print(result["metrics"])
    print(f"Trades: {len(result['trades'])}")


if __name__ == "__main__":
    main()

