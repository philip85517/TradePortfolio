from __future__ import annotations

from pathlib import Path

import pandas as pd

from .data_loader import load_market_data
from .indicators import add_indicators
from .portfolio import Portfolio
from .position_sizing import calculate_position_size
from .report import performance_metrics
from .scoring import build_watchlist, calculate_score, trailing_returns_matrix
from .signals import calculate_initial_stop, generate_entry_signal, generate_exit_signal
from .universe import classify_etf_theme


def prepare_strategy_data(market_data: pd.DataFrame, config: dict) -> pd.DataFrame:
    indicators = add_indicators(market_data, config)
    return calculate_score(indicators, config)


def run_backtest(
    config: dict,
    market_data: pd.DataFrame | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    output_dir: str | Path | None = None,
) -> dict:
    data = load_market_data() if market_data is None else market_data.copy()
    data["date"] = pd.to_datetime(data["date"])
    if start_date:
        data = data[data["date"] >= pd.to_datetime(start_date)]
    if end_date:
        data = data[data["date"] <= pd.to_datetime(end_date)]

    scored = prepare_strategy_data(data, config)
    dates = sorted(scored["date"].drop_duplicates())
    portfolio = Portfolio(initial_cash=float(config["strategy"].get("initial_cash", 1_000_000)))
    pending_buys: list[dict] = []
    pending_sells: list[dict] = []
    equity_rows: list[dict] = []
    watchlist_rows: list[pd.DataFrame] = []
    signal_rows: list[dict] = []

    for date in dates:
        daily = scored[scored["date"] == date].set_index("symbol")
        open_prices = daily["open"].to_dict()
        close_prices = daily["close"].to_dict()

        _execute_pending_sells(portfolio, date, daily, pending_sells, config)
        pending_sells = []
        _execute_pending_buys(portfolio, date, daily, pending_buys, config)
        pending_buys = []

        # Intraday hard stop is executable without looking beyond the current bar.
        for symbol, position in list(portfolio.positions.items()):
            if symbol in daily.index and daily.loc[symbol, "low"] <= position.stop_price:
                sell_price = position.stop_price * (1 - config["execution"].get("slippage_rate", 0.0005))
                cost = sell_price * position.shares * config["execution"].get("commission_rate", 0.0003)
                portfolio.sell(date, symbol, sell_price, cost, "hard_stop")
                signal_rows.append({"date": date, "symbol": symbol, "signal": "SELL", "reason": "hard_stop"})

        hist = scored[scored["date"] <= date]
        returns_matrix = trailing_returns_matrix(hist, date, config.get("candidate", {}).get("correlation_lookback", 60))
        watchlist = build_watchlist(scored, date, config, returns_matrix)
        if not watchlist.empty:
            watchlist_rows.append(watchlist.assign(date=date))

        for symbol, position in list(portfolio.positions.items()):
            if symbol not in daily.index:
                continue
            exit_signal = generate_exit_signal(position.__dict__, hist, scored, config)
            position.stop_price = exit_signal.get("updated_stop") or position.stop_price
            if exit_signal["exit"] and exit_signal["reason"] != "hard_stop":
                pending_sells.append({"symbol": symbol, "reason": exit_signal["reason"]})
                signal_rows.append({"date": date, "symbol": symbol, "signal": "SELL", "reason": exit_signal["reason"]})

        for _, row in watchlist.iterrows():
            symbol = row["symbol"]
            if symbol in portfolio.positions or len(portfolio.positions) + len(pending_buys) >= config["candidate"].get("top_n_holdings", 5):
                continue
            if _has_theme_exposure(portfolio, row, config):
                continue
            if generate_entry_signal(symbol, hist, config):
                pending_buys.append({"symbol": symbol, "score": row["total_score"]})
                signal_rows.append({"date": date, "symbol": symbol, "signal": "BUY", "reason": "entry", "score": row["total_score"]})

        equity_rows.append(
            {
                "date": date,
                "cash": portfolio.cash,
                "position_value": portfolio.total_position_value(close_prices),
                "equity": portfolio.total_value(close_prices),
                "positions": len(portfolio.positions),
            }
        )

    result = {
        "equity": pd.DataFrame(equity_rows),
        "trades": pd.DataFrame(portfolio.trades),
        "watchlists": pd.concat(watchlist_rows, ignore_index=True) if watchlist_rows else pd.DataFrame(),
        "signals": pd.DataFrame(signal_rows),
        "scored": scored,
    }
    result["metrics"] = performance_metrics(result["equity"], result["trades"], config)

    if output_dir is not None:
        write_backtest_outputs(result, output_dir)
    return result


def _execute_pending_buys(portfolio: Portfolio, date, daily: pd.DataFrame, pending_buys: list[dict], config: dict) -> None:
    if not pending_buys:
        return
    exec_cfg = config.get("execution", {})
    for order in sorted(pending_buys, key=lambda x: x.get("score", 0), reverse=True):
        symbol = order["symbol"]
        if symbol not in daily.index:
            continue
        row = daily.loc[symbol].copy()
        row["symbol"] = symbol
        if _has_theme_exposure(portfolio, row, config):
            continue
        buy_price = row["open"] * (1 + exec_cfg.get("slippage_rate", 0.0005))
        stop = calculate_initial_stop(buy_price, row["atr20"], row["ema20"], None, config)
        sizing = calculate_position_size(buy_price, stop, float(portfolio.cash or 0), config)
        if not sizing["should_trade"]:
            continue
        cost = sizing["shares"] * buy_price * exec_cfg.get("commission_rate", 0.0003)
        if sizing["shares"] * buy_price + cost > float(portfolio.cash or 0):
            continue
        portfolio.buy(date, row, sizing["shares"], buy_price, stop, cost)


def _has_theme_exposure(portfolio: Portfolio, row: pd.Series, config: dict) -> bool:
    if config.get("candidate", {}).get("max_per_theme", 1) <= 0:
        return False
    target_theme = classify_etf_theme(row.get("name"), row.get("asset_class"))
    return any(
        classify_etf_theme(position.name, position.asset_class) == target_theme
        for position in portfolio.positions.values()
    )


def _execute_pending_sells(portfolio: Portfolio, date, daily: pd.DataFrame, pending_sells: list[dict], config: dict) -> None:
    exec_cfg = config.get("execution", {})
    for order in pending_sells:
        symbol = order["symbol"]
        if symbol not in portfolio.positions or symbol not in daily.index:
            continue
        position = portfolio.positions[symbol]
        sell_price = daily.loc[symbol, "open"] * (1 - exec_cfg.get("slippage_rate", 0.0005))
        cost = position.shares * sell_price * exec_cfg.get("commission_rate", 0.0003)
        portfolio.sell(date, symbol, sell_price, cost, order.get("reason", "exit"))


def write_backtest_outputs(result: dict, output_dir: str | Path) -> None:
    output = Path(output_dir)
    (output / "reports").mkdir(parents=True, exist_ok=True)
    (output / "trades").mkdir(parents=True, exist_ok=True)
    result["equity"].to_csv(output / "reports" / "equity_curve.csv", index=False)
    result["trades"].to_csv(output / "trades" / "trades.csv", index=False)
    result["signals"].to_csv(output / "reports" / "signals.csv", index=False)
    result["watchlists"].to_csv(output / "reports" / "watchlists.csv", index=False)
