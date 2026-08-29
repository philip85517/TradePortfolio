from __future__ import annotations

import argparse
import copy
import json
import mimetypes
import subprocess
import sys
import threading
from datetime import datetime, time, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pandas as pd

from src.data_store import DEFAULT_DB_PATH, connect, database_summary, load_market_data_from_db
from src.indicators import add_indicators
from src.market_data_store import DEFAULT_MARKET_DB_PATH, connect_market_db
from src.scoring import calculate_score
from src.universe import classify_etf_theme, deduplicate_by_theme
from src.utils import load_config

ROOT = Path(__file__).resolve().parent
STATIC_ROOT = ROOT / "web" / "static"
CONFIG_PATH = ROOT / "config" / "strategy_config.yaml"


class DashboardState:
    def __init__(self, db_path: Path, config_path: Path, market_db_path: Path = DEFAULT_MARKET_DB_PATH):
        self.db_path = db_path
        self.market_db_path = market_db_path
        self.config = load_config(config_path)
        self.score_cache: dict[tuple, pd.DataFrame] = {}
        self.stock_score_cache: dict[tuple, pd.DataFrame] = {}
        self.stock_score_inflight: dict[tuple, threading.Event] = {}
        self.rolling_plan_cache: dict[tuple, dict] = {}
        self.score_cache_lock = threading.RLock()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ETFStrategy dashboard")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--market-db", default=str(DEFAULT_MARKET_DB_PATH))
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    state = DashboardState(Path(args.db), Path(args.config), Path(args.market_db))

    class Handler(DashboardHandler):
        dashboard_state = state

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"ETFStrategy dashboard: http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


class DashboardHandler(BaseHTTPRequestHandler):
    dashboard_state: DashboardState

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook.
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self._send_file(STATIC_ROOT / "index.html")
            elif parsed.path in {"/app.js", "/styles.css"}:
                self._send_file(STATIC_ROOT / parsed.path.removeprefix("/"))
            elif parsed.path.startswith("/static/"):
                self._send_file(STATIC_ROOT / parsed.path.removeprefix("/static/"))
            elif parsed.path == "/api/summary":
                self._send_json(api_summary(self.dashboard_state))
            elif parsed.path == "/api/leaderboard":
                self._send_json(api_leaderboard(self.dashboard_state, parse_qs(parsed.query)))
            elif parsed.path == "/api/etf_detail":
                self._send_json(api_etf_detail(self.dashboard_state, parse_qs(parsed.query)))
            elif parsed.path == "/api/timeseries":
                self._send_json(api_timeseries(self.dashboard_state, parse_qs(parsed.query)))
            elif parsed.path == "/api/watchlist":
                self._send_json(api_watchlist(self.dashboard_state, parse_qs(parsed.query)))
            elif parsed.path == "/api/theme_heatmap":
                self._send_json(api_theme_heatmap(self.dashboard_state, parse_qs(parsed.query)))
            elif parsed.path == "/api/stock_overview":
                self._send_json(api_stock_overview(self.dashboard_state, parse_qs(parsed.query)))
            elif parsed.path == "/api/rolling_plans":
                self._send_json(api_rolling_plans(self.dashboard_state, parse_qs(parsed.query)))
            else:
                self.send_error(404, "Not found")
        except Exception as exc:  # noqa: BLE001 - return JSON errors to the UI.
            self._send_json({"error": str(exc)}, status=500)

    def do_POST(self) -> None:  # noqa: N802 - stdlib hook.
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/update":
                self._send_json(api_update_data(self.dashboard_state))
            else:
                self.send_error(404, "Not found")
        except Exception as exc:  # noqa: BLE001 - return JSON errors to the UI.
            self._send_json({"error": str(exc)}, status=500)

    def log_message(self, fmt: str, *args) -> None:
        return

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(404, "Not found")
            return
        body = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{mime}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(to_jsonable(payload), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def api_summary(state: DashboardState) -> dict:
    summary = database_summary(state.db_path)
    freshness = data_freshness(summary.get("end_date"))
    with connect(state.db_path) as con:
        update_log = con.execute(
            """
            SELECT run_at, source, start_date, end_date, rows_written, symbols_written, failures, note
            FROM update_log
            ORDER BY run_at DESC
            LIMIT 5
            """
        ).fetch_df()
        latest_class = con.execute(
            """
            SELECT asset_class,
                   count(DISTINCT symbol) AS etf_count,
                   sum(amount) AS latest_amount,
                   avg(close) AS avg_close
            FROM etf_daily
            WHERE date = (SELECT max(date) FROM etf_daily)
            GROUP BY asset_class
            ORDER BY etf_count DESC
            """
        ).fetch_df()
    return {
        "summary": {k: v for k, v in summary.items() if k != "by_class"},
        "by_class": summary["by_class"].to_dict("records"),
        "market_groups": market_group_counts(summary["by_class"]).to_dict("records"),
        "stock_market_groups": stock_market_counts(state.market_db_path).to_dict("records"),
        "latest_class": latest_class.to_dict("records"),
        "update_log": update_log.to_dict("records"),
        "freshness": freshness,
    }


def api_leaderboard(state: DashboardState, params: dict[str, list[str]]) -> dict:
    asset_class = first(params, "asset_class", "ALL")
    market = first(params, "market", "ALL")
    search = first(params, "search", "").strip()
    sort = first(params, "sort", "daily_rank")
    limit = int(first(params, "limit", "80"))
    allowed_sorts = {
        "daily_rank": "daily_rank",
        "total_score": "total_score",
        "return_20d": "return_20d",
        "return_60d": "return_60d",
        "return_120d": "return_120d",
        "amount": "amount",
        "fund_size": "fund_size",
        "close": "close",
    }
    sort_key = allowed_sorts.get(sort, "daily_rank")
    latest = latest_etf_date(state)
    if latest is None:
        return {"rows": []}
    class_filter = asset_classes_for_market(market)
    if asset_class != "ALL":
        class_filter = [asset_class]
    scored = scored_window(state, latest, class_filter)
    if scored.empty:
        return {"rows": []}
    latest_rows = scored[pd.to_datetime(scored["date"]) == latest].dropna(subset=["total_score"]).copy()
    if search:
        needle = search.casefold()
        latest_rows = latest_rows[
            latest_rows["symbol"].astype(str).str.casefold().str.contains(needle, regex=False)
            | latest_rows["name"].astype(str).str.casefold().str.contains(needle, regex=False)
        ]
    ascending = sort_key == "daily_rank"
    groups = build_theme_groups(latest_rows, sort_key, ascending).head(limit)
    return {"rows": groups.to_dict("records")}


def api_etf_detail(state: DashboardState, params: dict[str, list[str]]) -> dict:
    symbol = first(params, "symbol", "")
    if not symbol:
        return {"symbol": None, "detail": None}
    latest = latest_etf_date(state)
    if latest is None:
        return {"symbol": symbol, "detail": None}
    scored = scored_window(state, latest)
    if scored.empty:
        return {"symbol": symbol, "detail": None}
    rows = scored[scored["symbol"].astype(str) == str(symbol)].sort_values("date")
    if rows.empty:
        return {"symbol": symbol, "detail": None}
    latest_row = rows.iloc[-1]
    weights = state.config.get("scoring", {}).get("weights", {})
    factors = []
    for factor, weight in weights.items():
        source = "liquidity" if factor == "liquidity" else factor
        score_column = f"{factor}_score"
        score_value = latest_row.get(score_column)
        factors.append(
            {
                "factor": factor,
                "raw_value": latest_row.get(source),
                "score": score_value,
                "weight": weight,
                "contribution": float(score_value) * float(weight) if pd.notna(score_value) else None,
            }
        )
    detail = {
        "date": latest_row.get("date"),
        "symbol": latest_row.get("symbol"),
        "name": latest_row.get("name"),
        "asset_class": latest_row.get("asset_class"),
        "theme": classify_etf_theme(latest_row.get("name"), latest_row.get("asset_class")),
        "rank": latest_row.get("daily_rank"),
        "total_score": latest_row.get("total_score"),
        "overheat_penalty": latest_row.get("overheat_penalty"),
        "factors": factors,
        "checks": etf_entry_checks(latest_row),
        "score_history": rows.tail(80)[
            [
                "date",
                "close",
                "total_score",
                "daily_rank",
                "return_20d",
                "return_60d",
                "return_120d",
                "overheat_penalty",
            ]
        ].to_dict("records"),
    }
    return {"symbol": symbol, "detail": detail}


def etf_entry_checks(row: pd.Series) -> list[dict]:
    def check(label: str, passed: bool, value=None, note: str | None = None) -> dict:
        return {"label": label, "passed": bool(passed), "value": value, "note": note}

    close = row.get("close")
    ema20 = row.get("ema20")
    ma60 = row.get("ma60")
    rsi14 = row.get("rsi14")
    atr20 = row.get("atr20")
    close_position_quality = row.get("close_position_quality")
    ma20_gap = close / ema20 - 1 if pd.notna(close) and pd.notna(ema20) and ema20 else None
    ma60_gap = close / ma60 - 1 if pd.notna(close) and pd.notna(ma60) and ma60 else None
    return [
        check("收盘价站上 EMA20", pd.notna(ma20_gap) and ma20_gap > 0, ma20_gap),
        check("收盘价站上 MA60", pd.notna(ma60_gap) and ma60_gap > 0, ma60_gap),
        check("EMA20 高于 MA60", pd.notna(ema20) and pd.notna(ma60) and ema20 > ma60, None),
        check("RSI 未明显过热", pd.notna(rsi14) and rsi14 <= 75, rsi14, "超过 75 会触发过热扣分"),
        check("ATR 可计算", pd.notna(atr20) and atr20 > 0, atr20),
        check("收盘位置质量可用", pd.notna(close_position_quality), close_position_quality),
    ]


def api_timeseries(state: DashboardState, params: dict[str, list[str]]) -> dict:
    symbol = first(params, "symbol", "")
    period = first(params, "period", "day")
    if period not in {"day", "week", "month"}:
        period = "day"
    days = int(first(params, "days", "260"))
    if not symbol:
        return {"rows": [], "symbol": None, "period": period}
    with connect(state.db_path) as con:
        df = con.execute(
            """
            SELECT date, symbol, name, asset_class, open, high, low, close, amount, volume
            FROM etf_daily
            WHERE symbol = ?
            ORDER BY date DESC
            LIMIT ?
            """,
            [symbol, days],
        ).fetch_df()
    if df.empty:
        return {"rows": [], "symbol": symbol, "period": period}
    df = df.sort_values("date").reset_index(drop=True)
    daily = df.copy()
    daily["return"] = daily["close"].pct_change()
    stats = {
        "name": daily.iloc[-1]["name"],
        "asset_class": daily.iloc[-1]["asset_class"],
        "latest_close": daily.iloc[-1]["close"],
        "latest_amount": daily.iloc[-1]["amount"],
        "return_20d": safe_return(daily["close"], 20),
        "return_60d": safe_return(daily["close"], 60),
        "volatility_60d": float(daily["return"].tail(60).std() * (252 ** 0.5)) if len(daily) >= 60 else None,
    }
    chart_df = aggregate_timeseries(df, period)
    return {"symbol": symbol, "period": period, "stats": stats, "rows": chart_df.to_dict("records")}


def aggregate_timeseries(df: pd.DataFrame, period: str) -> pd.DataFrame:
    data = df.copy()
    data["date"] = pd.to_datetime(data["date"])
    numeric_columns = ["open", "high", "low", "close", "amount", "volume"]
    for column in numeric_columns:
        if column in data:
            data[column] = pd.to_numeric(data[column], errors="coerce")

    if period != "day":
        rule = "W-FRI" if period == "week" else "ME"
        data["bar_date"] = data["date"]
        data = data.set_index("date")
        grouped = data.resample(rule)
        data = grouped.agg(
            {
                "bar_date": "last",
                "symbol": "last",
                "name": "last",
                "asset_class": "last",
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "amount": "sum",
                "volume": "sum",
            }
        )
        data = data.dropna(subset=["open", "high", "low", "close"]).reset_index()
        data["date"] = data["bar_date"]
        data = data.drop(columns=["bar_date"])

    data = data.sort_values("date").reset_index(drop=True)
    data["ema20"] = data["close"].ewm(span=20, adjust=False).mean()
    data["ma60"] = data["close"].rolling(60).mean()
    data["date"] = pd.to_datetime(data["date"]).dt.date
    return data


def api_watchlist(state: DashboardState, params: dict[str, list[str]]) -> dict:
    end_date = first(params, "date", None)
    market = first(params, "market", "ALL")
    latest = pd.to_datetime(end_date) if end_date else None
    with connect(state.db_path) as con:
        if latest is None:
            latest = pd.to_datetime(con.execute("SELECT max(date) FROM etf_daily").fetchone()[0])
    scored = scored_window(state, latest, asset_classes_for_market(market))
    if scored.empty:
        return {"date": latest.date(), "rows": []}
    top_n = state.config.get("candidate", {}).get("top_n_watchlist", 10)
    daily = scored[pd.to_datetime(scored["date"]) == latest].dropna(subset=["total_score"]).copy()
    watchlist = build_theme_groups(daily, "daily_rank", True).head(top_n)
    watchlist.insert(0, "watch_rank", range(1, len(watchlist) + 1))
    return {"date": latest.date(), "rows": watchlist.to_dict("records")}


def api_theme_heatmap(state: DashboardState, params: dict[str, list[str]]) -> dict:
    market = first(params, "market", "ALL")
    asset_class = first(params, "asset_class", "ALL")
    latest = latest_etf_date(state)
    if latest is None:
        return {"date": None, "rows": []}
    class_filter = asset_classes_for_market(market)
    if asset_class != "ALL":
        class_filter = [asset_class]
    scored = scored_window(state, latest, class_filter)
    if scored.empty:
        return {"date": latest.date(), "rows": []}
    daily = scored[pd.to_datetime(scored["date"]) == latest].dropna(subset=["total_score"]).copy()
    groups = build_theme_groups(daily, "daily_rank", True)
    return {"date": latest.date(), "rows": groups.head(36).to_dict("records")}


def api_stock_overview(state: DashboardState, params: dict[str, list[str]]) -> dict:
    market = first(params, "market", "a_share")
    level = first(params, "level", "level1")
    sort = first(params, "sort", "group_rank")
    search = first(params, "search", "").strip()
    limit = int(first(params, "limit", "80"))
    if level not in {"level1", "level2", "level3"}:
        level = "level1"
    latest = latest_stock_date(state, market)
    if latest is None:
        return {"date": None, "rows": [], "market": market, "level": level}
    scored = stock_scored_window(state, latest, market)
    if scored.empty:
        return {"date": latest.date(), "rows": [], "market": market, "level": level}
    daily = latest_stock_rows(scored)
    snapshot = load_stock_latest_snapshot(state.market_db_path, market)
    if not snapshot.empty:
        present = set(daily["symbol"].astype(str)) if not daily.empty else set()
        missing = snapshot[~snapshot["symbol"].astype(str).isin(present)].copy()
        if not missing.empty:
            daily = pd.concat([daily, missing], ignore_index=True, sort=False)
    if search:
        needle = search.casefold()
        daily = daily[
            daily["symbol"].astype(str).str.casefold().str.contains(needle, regex=False)
            | daily["name"].astype(str).str.casefold().str.contains(needle, regex=False)
            | daily["industry_level1"].astype(str).str.casefold().str.contains(needle, regex=False)
            | daily["industry_level2"].astype(str).str.casefold().str.contains(needle, regex=False)
            | daily["industry_level3"].astype(str).str.casefold().str.contains(needle, regex=False)
        ]
    groups = build_stock_industry_groups(daily, level, sort)
    return {
        "date": latest.date(),
        "market": market,
        "level": level,
        "rows": groups.head(limit).to_dict("records"),
    }


def api_rolling_plans(state: DashboardState, params: dict[str, list[str]]) -> dict:
    market = first(params, "market", "ALL")
    asset_class = first(params, "asset_class", "ALL")
    days = int(first(params, "days", "365"))
    latest = latest_etf_date(state)
    if latest is None:
        return {"date": None, "rows": []}
    class_filter = asset_classes_for_market(market)
    if asset_class != "ALL":
        class_filter = [asset_class]
    cache_key = (latest.date().isoformat(), market, asset_class, days)
    with state.score_cache_lock:
        cached = state.rolling_plan_cache.get(cache_key)
        if cached is not None:
            return copy.deepcopy(cached)
    scored = scored_window(state, latest, class_filter, simulation_days=days)
    if scored.empty:
        payload = {"date": latest.date(), "rows": []}
        with state.score_cache_lock:
            state.rolling_plan_cache[cache_key] = copy.deepcopy(payload)
        return payload

    prepared = prepare_rolling_simulation(scored, days, state.config)
    if not prepared["dates"]:
        payload = {"date": latest.date(), "rows": []}
        with state.score_cache_lock:
            state.rolling_plan_cache[cache_key] = copy.deepcopy(payload)
        return payload

    initial_cash = float(state.config.get("strategy", {}).get("initial_cash", 1_000_000))
    exec_cfg = state.config.get("execution", {})
    cost_rate = float(exec_cfg.get("commission_rate", 0.0003)) + float(exec_cfg.get("slippage_rate", 0.0005))
    rebalance_days = [5, 10, 20, 40]
    holding_counts = [3, 5, 8, 10]

    rows = []
    for interval in rebalance_days:
        for count in holding_counts:
            rows.append(
                simulate_rolling_plan(
                    prepared,
                    plan_id=f"{market or 'ALL'}-D{days}-R{interval}-N{count}",
                    title=f"{marketName_backend(market)} · {interval}日换仓 · {count}只等权",
                    rebalance_days=interval,
                    holdings_count=count,
                    initial_cash=initial_cash,
                    cost_rate=cost_rate,
                )
            )
    rows = sorted(rows, key=lambda row: row.get("total_return") or -999, reverse=True)
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    payload = {"date": latest.date(), "rows": rows}
    with state.score_cache_lock:
        state.rolling_plan_cache[cache_key] = copy.deepcopy(payload)
    return payload


def api_update_data(state: DashboardState) -> dict:
    summary = database_summary(state.db_path)
    freshness = data_freshness(summary.get("end_date"))
    if freshness["is_current"]:
        return {"updated": False, "freshness": freshness, "message": "数据已经是最新"}

    command = [
        sys.executable,
        str(ROOT / "scripts" / "update_etf_data.py"),
        "--incremental",
        "--db",
        str(state.db_path),
        "--end-date",
        freshness["expected_date"].isoformat(),
        "--retries",
        "1",
        "--sleep",
        "0",
        "--max-live-failure-streak",
        "5",
        "--carry-forward-missing",
        "--no-csv",
        "--summary-output",
        str(ROOT / "data" / "raw" / "update_summary.md"),
    ]
    result = subprocess.run(
        command,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    if result.returncode != 0:
        output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
        raise RuntimeError(output or "更新数据失败")

    state.score_cache.clear()
    state.rolling_plan_cache.clear()
    next_summary = database_summary(state.db_path)
    return {
        "updated": True,
        "freshness": data_freshness(next_summary.get("end_date")),
        "output": result.stdout.strip().splitlines()[-8:],
    }


def prepare_rolling_simulation(scored: pd.DataFrame, simulation_days: int, config: dict | None = None) -> dict:
    columns = ["date", "symbol", "name", "asset_class", "close", "total_score", "daily_rank"]
    candidate_cfg = (config or {}).get("candidate", {})
    data = scored[[column for column in columns if column in scored.columns]].copy()
    data["date"] = pd.to_datetime(data["date"])
    data["symbol"] = data["symbol"].astype(str)
    data["close"] = pd.to_numeric(data["close"], errors="coerce")
    data["total_score"] = pd.to_numeric(data["total_score"], errors="coerce")
    data["daily_rank"] = pd.to_numeric(data["daily_rank"], errors="coerce")
    data = data.dropna(subset=["date", "symbol", "close", "total_score"])
    if data.empty:
        return {"dates": [], "returns_by_date": {}, "candidates_by_date": {}, "meta_by_date": {}}

    data = data.sort_values(["symbol", "date"])
    data["daily_return"] = data.groupby("symbol")["close"].pct_change()
    end_date = data["date"].max()
    start_date = end_date - pd.Timedelta(days=simulation_days - 1)
    dates = sorted(data.loc[data["date"] >= start_date, "date"].drop_duplicates())
    data = data[data["date"].isin(dates)].copy()
    data = data.sort_values(["date", "daily_rank", "total_score"], ascending=[True, True, False])

    returns_by_date = {}
    candidates_by_date = {}
    meta_by_date = {}
    for date, frame in data.groupby("date", sort=True):
        candidate_frame = deduplicate_by_theme(
            frame,
            max_per_theme=candidate_cfg.get("max_per_theme", 1),
            max_per_asset_class=candidate_cfg.get("max_per_asset_class", 2),
        )
        candidates_by_date[date] = candidate_frame["symbol"].tolist()
        return_frame = frame.dropna(subset=["daily_return"])
        returns_by_date[date] = dict(zip(return_frame["symbol"], return_frame["daily_return"].astype(float), strict=False))
        meta_by_date[date] = frame.set_index("symbol")[["date", "name", "asset_class", "close", "daily_rank", "total_score"]].to_dict("index")
    return {
        "dates": dates,
        "returns_by_date": returns_by_date,
        "candidates_by_date": candidates_by_date,
        "meta_by_date": meta_by_date,
    }


def simulate_rolling_plan(
    prepared: dict,
    plan_id: str,
    title: str,
    rebalance_days: int,
    holdings_count: int,
    initial_cash: float,
    cost_rate: float,
) -> dict:
    dates = prepared["dates"]
    if not dates:
        return empty_plan(plan_id, title, rebalance_days, holdings_count, initial_cash)

    equity = initial_cash
    holdings: list[str] = []
    entry_dates: dict[str, pd.Timestamp] = {}
    open_periods: dict[str, dict] = {}
    holding_periods: dict[str, list[dict]] = {}
    contribution: dict[str, dict] = {}
    daily_rows: list[dict] = []
    events: list[dict] = []
    previous_equity = initial_cash

    for index, date in enumerate(dates):
        returns = prepared["returns_by_date"].get(date, {})
        meta = prepared["meta_by_date"].get(date, {})
        if holdings and returns:
            active_returns = []
            for symbol in holdings:
                value = returns.get(symbol)
                if value is not None and pd.notna(value):
                    active_returns.append((symbol, float(value)))
            if active_returns:
                weight = 1 / len(active_returns)
                before_return_equity = equity
                portfolio_return = sum(ret * weight for _, ret in active_returns)
                equity *= 1 + portfolio_return
                for symbol, ret in active_returns:
                    row = meta.get(symbol, {})
                    item = contribution.setdefault(
                        symbol,
                        {
                            "symbol": symbol,
                            "name": row.get("name", symbol),
                            "asset_class": row.get("asset_class", "OTHER"),
                            "pnl": 0.0,
                            "days": 0,
                        },
                    )
                    item["pnl"] += before_return_equity * weight * ret
                    item["days"] += 1

        should_rebalance = index == 0 or index % rebalance_days == 0 or not holdings
        if should_rebalance:
            target = prepared["candidates_by_date"].get(date, [])[:holdings_count]
            if target:
                old_set = set(holdings)
                new_set = set(target)
                changed = old_set != new_set
                if changed:
                    turnover = turnover_between(holdings, target)
                    trade_cost = equity * turnover * cost_rate
                    old_weight = 1 / len(holdings) if holdings else 0
                    new_weight = 1 / len(target) if target else 0
                    equity -= trade_cost
                    for symbol in new_set - old_set:
                        entry_dates[symbol] = date
                        row = meta.get(symbol, {})
                        buy_price = row.get("close")
                        buy_value = equity * new_weight
                        period = {
                            "symbol": symbol,
                            "name": row.get("name", symbol),
                            "asset_class": row.get("asset_class", "OTHER"),
                            "buy_date": date.date(),
                            "buy_rank": row.get("daily_rank"),
                            "buy_score": row.get("total_score"),
                            "buy_price": buy_price,
                            "buy_quantity": trade_quantity(buy_value, buy_price),
                            "buy_value": buy_value,
                            "buy_weight": new_weight,
                            "buy_reason": buy_reason(row, holdings_count),
                            "sell_date": None,
                            "sell_rank": None,
                            "sell_score": None,
                            "sell_price": None,
                            "sell_quantity": None,
                            "sell_value": None,
                            "sell_weight": None,
                            "sell_reason": "当前仍在组合目标持仓内",
                            "sell_return": None,
                            "holding_days": 0,
                            "status": "OPEN",
                        }
                        holding_periods.setdefault(symbol, []).append(period)
                        open_periods[symbol] = period
                    for symbol in old_set - new_set:
                        row = meta.get(symbol, {})
                        sell_price = row.get("close")
                        sell_value = equity * old_weight
                        period = open_periods.pop(symbol, None)
                        if period is not None:
                            period["sell_date"] = date.date()
                            period["sell_rank"] = row.get("daily_rank")
                            period["sell_score"] = row.get("total_score")
                            period["sell_price"] = sell_price
                            period["sell_quantity"] = trade_quantity(sell_value, sell_price)
                            period["sell_value"] = sell_value
                            period["sell_weight"] = old_weight
                            period["sell_reason"] = sell_reason(row, holdings_count)
                            period["sell_return"] = period_return(period.get("buy_price"), period.get("sell_price"))
                            period["holding_days"] = (date - pd.to_datetime(period["buy_date"])).days
                            period["status"] = "CLOSED"
                        entry_dates.pop(symbol, None)
                    events.append(
                        {
                            "date": date.date(),
                            "added": symbol_names_from_meta(meta, sorted(new_set - old_set)),
                            "removed": symbol_names_from_meta(meta, sorted(old_set - new_set)),
                            "holdings": symbol_names_from_meta(meta, target),
                            "turnover": turnover,
                            "cost": trade_cost,
                            "equity": equity,
                        }
                    )
                    holdings = target

        daily_return = equity / previous_equity - 1 if previous_equity else 0
        peak = max([row["equity"] for row in daily_rows], default=initial_cash)
        peak = max(peak, equity)
        drawdown = equity / peak - 1 if peak else 0
        daily_rows.append(
            {
                "date": date.date(),
                "equity": equity,
                "daily_return": daily_return,
                "drawdown": drawdown,
                "holdings": len(holdings),
            }
        )
        previous_equity = equity

    last_date = dates[-1]
    for symbol, period in open_periods.items():
        period["holding_days"] = (last_date - pd.to_datetime(period["buy_date"])).days
        period["sell_return"] = period_return(period.get("buy_price"), period.get("sell_price"))
    current_holdings = current_holding_payload(prepared["meta_by_date"].get(last_date, {}), holdings, entry_dates, equity, holding_periods)
    returns = pd.Series([row["daily_return"] for row in daily_rows], dtype="float64")
    positive_days = returns[returns > 0]
    total_return = equity / initial_cash - 1 if initial_cash else None
    max_drawdown = min((row["drawdown"] for row in daily_rows), default=0.0)
    daily_volatility = float(returns.std()) if len(returns) > 1 else 0.0
    attach_contribution_periods(contribution, holding_periods)
    contribution_rows = sorted(contribution.values(), key=lambda row: row["pnl"], reverse=True)
    return {
        "id": plan_id,
        "rank": None,
        "title": title,
        "rebalance_days": rebalance_days,
        "holdings_count": holdings_count,
        "days": len(daily_rows),
        "initial_cash": initial_cash,
        "final_equity": equity,
        "total_return": total_return,
        "max_drawdown": max_drawdown,
        "daily_volatility": daily_volatility,
        "annual_volatility": daily_volatility * (252 ** 0.5),
        "win_rate": float(len(positive_days) / len(returns)) if len(returns) else None,
        "rebalance_count": len(events),
        "difference_summary": plan_difference_summary(rebalance_days, holdings_count),
        "strategy_rules": {
            "rebalance_days": rebalance_days,
            "holdings_count": holdings_count,
            "selection_rule": f"每个调仓日先对同方向 ETF 仅保留最高分标的，再选择排名前 {holdings_count} 只 ETF",
            "weight_rule": "入选基金等权持有，调仓时用新的目标名单替换旧名单",
            "cost_rule": f"按换手扣除交易成本，当前合计成本率约 {cost_rate:.4f}",
        },
        "current_holdings": current_holdings,
        "rebalance_history": events[-8:][::-1],
        "contributions": contribution_rows[:12],
        "daily_returns": daily_rows[-40:],
    }


def period_return(buy_price, sell_price) -> float | None:
    if buy_price is None or sell_price is None or pd.isna(buy_price) or pd.isna(sell_price):
        return None
    buy = float(buy_price)
    if buy == 0:
        return None
    return float(sell_price) / buy - 1


def attach_contribution_periods(contribution: dict[str, dict], holding_periods: dict[str, list[dict]]) -> None:
    for symbol, item in contribution.items():
        periods = holding_periods.get(symbol, [])
        item["holding_periods"] = periods[::-1]
        item["daily_trades"] = daily_trades_from_periods(periods)
        item["periods_count"] = len(periods)


def daily_trades_from_periods(periods: list[dict]) -> list[dict]:
    rows = []
    for index, period in enumerate(periods):
        rows.append(
            {
                "id": f"{period.get('symbol')}-{index}-BUY",
                "date": period.get("buy_date"),
                "side": "BUY",
                "symbol": period.get("symbol"),
                "name": period.get("name"),
                "price": period.get("buy_price"),
                "quantity": period.get("buy_quantity"),
                "value": period.get("buy_value"),
                "weight": period.get("buy_weight"),
                "rank": period.get("buy_rank"),
                "score": period.get("buy_score"),
                "reason": period.get("buy_reason"),
                "paired_date": period.get("sell_date"),
                "holding_days": period.get("holding_days"),
                "period_return": period.get("sell_return"),
            }
        )
        if period.get("sell_date"):
            rows.append(
                {
                    "id": f"{period.get('symbol')}-{index}-SELL",
                    "date": period.get("sell_date"),
                    "side": "SELL",
                    "symbol": period.get("symbol"),
                    "name": period.get("name"),
                    "price": period.get("sell_price"),
                    "quantity": period.get("sell_quantity"),
                    "value": period.get("sell_value"),
                    "weight": period.get("sell_weight"),
                    "rank": period.get("sell_rank"),
                    "score": period.get("sell_score"),
                    "reason": period.get("sell_reason"),
                    "paired_date": period.get("buy_date"),
                    "holding_days": period.get("holding_days"),
                    "period_return": period.get("sell_return"),
                }
            )
    return sorted(rows, key=lambda row: (str(row.get("date") or ""), row.get("side") == "SELL"), reverse=True)


def trade_quantity(value, price) -> float | None:
    if value is None or price is None or pd.isna(value) or pd.isna(price):
        return None
    price = float(price)
    if price == 0:
        return None
    return float(value) / price


def buy_reason(row: dict, holdings_count: int) -> str:
    rank = row.get("daily_rank")
    score = row.get("total_score")
    return f"调仓日进入目标组合前 {holdings_count} 名，排名 #{format_rank(rank)}，综合得分 {format_score(score)}，按等权买入。"


def sell_reason(row: dict, holdings_count: int) -> str:
    rank = row.get("daily_rank")
    score = row.get("total_score")
    if rank is None or pd.isna(rank):
        return f"调仓日已无有效评分，退出前 {holdings_count} 目标组合。"
    if float(rank) > holdings_count:
        return f"调仓日排名降至 #{format_rank(rank)}，未进入前 {holdings_count} 目标组合，卖出并切换到更高排名标的。"
    return f"调仓日目标组合更新，当前标的不再属于前 {holdings_count} 个目标持仓，排名 #{format_rank(rank)}，得分 {format_score(score)}。"


def plan_difference_summary(rebalance_days: int, holdings_count: int) -> str:
    speed = "高频、追随排名变化更快" if rebalance_days <= 10 else "低频、降低换手并容忍排名波动"
    breadth = "集中持仓，收益和回撤更依赖少数强势方向" if holdings_count <= 3 else "分散持仓，单一基金影响更低但可能稀释强势收益"
    return f"{rebalance_days} 日换仓代表{speed}；{holdings_count} 只等权代表{breadth}。"


def format_rank(value) -> str:
    if value is None or pd.isna(value):
        return "--"
    return str(int(float(value)))


def format_score(value) -> str:
    if value is None or pd.isna(value):
        return "--"
    return f"{float(value):.1f}"


def empty_plan(plan_id: str, title: str, rebalance_days: int, holdings_count: int, initial_cash: float) -> dict:
    return {
        "id": plan_id,
        "rank": None,
        "title": title,
        "rebalance_days": rebalance_days,
        "holdings_count": holdings_count,
        "days": 0,
        "initial_cash": initial_cash,
        "final_equity": initial_cash,
        "total_return": 0.0,
        "max_drawdown": 0.0,
        "daily_volatility": 0.0,
        "annual_volatility": 0.0,
        "win_rate": None,
        "rebalance_count": 0,
        "difference_summary": plan_difference_summary(rebalance_days, holdings_count),
        "strategy_rules": {},
        "current_holdings": [],
        "rebalance_history": [],
        "contributions": [],
        "daily_returns": [],
    }


def turnover_between(old: list[str], new: list[str]) -> float:
    if not old:
        return 1.0 if new else 0.0
    symbols = set(old) | set(new)
    old_weight = {symbol: 1 / len(old) for symbol in old}
    new_weight = {symbol: 1 / len(new) for symbol in new} if new else {}
    return 0.5 * sum(abs(new_weight.get(symbol, 0) - old_weight.get(symbol, 0)) for symbol in symbols)


def symbol_names_from_meta(meta: dict[str, dict], symbols: list[str]) -> list[dict]:
    if not meta or not symbols:
        return [{"symbol": symbol, "name": symbol} for symbol in symbols]
    out = []
    for symbol in symbols:
        row = meta.get(symbol)
        if row:
            out.append({"symbol": symbol, "name": row.get("name", symbol)})
        else:
            out.append({"symbol": symbol, "name": symbol})
    return out


def current_holding_payload(
    meta: dict[str, dict],
    holdings: list[str],
    entry_dates: dict[str, pd.Timestamp],
    equity: float,
    holding_periods: dict[str, list[dict]],
) -> list[dict]:
    if not meta or not holdings:
        return []
    value = equity / len(holdings) if holdings else 0
    rows = []
    for symbol in holdings:
        row = meta.get(symbol)
        if not row:
            continue
        entry_date = entry_dates.get(symbol)
        rows.append(
            {
                "symbol": symbol,
                "name": row.get("name", symbol),
                "asset_class": row.get("asset_class", "OTHER"),
                "value": value,
                "weight": 1 / len(holdings),
                "close": row.get("close"),
                "daily_rank": row.get("daily_rank"),
                "total_score": row.get("total_score"),
                "holding_days": (pd.to_datetime(row.get("date")) - entry_date).days if entry_date is not None else None,
                "holding_periods": holding_periods.get(symbol, [])[-10:][::-1],
            }
        )
    return rows


def marketName_backend(market: str | None) -> str:
    labels = {
        "ALL": "全市场",
        "A_SHARE": "A股",
        "HK": "港股",
        "US": "美股",
        "COMMODITY": "商品",
        "INCOME": "固收红利",
        "OTHER": "其他",
    }
    return labels.get(str(market or "ALL"), str(market or "ALL"))


def stock_market_counts(db_path: Path) -> pd.DataFrame:
    if not Path(db_path).exists():
        return pd.DataFrame(columns=["market", "stock_count"])
    try:
        with connect_market_db(db_path, read_only=True) as con:
            return con.execute(
                """
                SELECT market, count(DISTINCT symbol) AS stock_count
                FROM market_ohlcv
                WHERE timeframe = '1d'
                  AND market IN ('a_share', 'hk', 'us')
                GROUP BY market
                ORDER BY stock_count DESC
                """
            ).fetch_df()
    except Exception:  # noqa: BLE001 - keep the ETF dashboard usable if the optional stock DB is unavailable.
        return pd.DataFrame(columns=["market", "stock_count"])


def latest_stock_date(state: DashboardState, market: str | None) -> pd.Timestamp | None:
    if not Path(state.market_db_path).exists():
        return None
    clauses = ["timeframe = '1d'"]
    params: list = []
    if market and market != "ALL":
        clauses.append("market = ?")
        params.append(market)
    with connect_market_db(state.market_db_path, read_only=True) as con:
        value = con.execute(
            f"SELECT max(trade_date) FROM market_ohlcv WHERE {' AND '.join(clauses)}",
            params,
        ).fetchone()[0]
    return pd.to_datetime(value) if value is not None else None


def stock_scored_window(state: DashboardState, latest: pd.Timestamp, market: str | None) -> pd.DataFrame:
    key = (latest.date().isoformat(), market or "ALL")
    while True:
        with state.score_cache_lock:
            cached = state.stock_score_cache.get(key)
            if cached is not None:
                return cached.copy()
            inflight = state.stock_score_inflight.get(key)
            if inflight is None:
                inflight = threading.Event()
                state.stock_score_inflight[key] = inflight
                break
        inflight.wait()

    try:
        start = (latest - pd.Timedelta(days=stock_score_calendar_days(state))).date()
        data = load_stock_latest_indicator_data(state.market_db_path, market, start, latest.date())
        if data.empty:
            scored = pd.DataFrame()
        else:
            scored = calculate_score(data, state.config)
            scored = attach_stock_industries(scored)
        with state.score_cache_lock:
            state.stock_score_cache[key] = scored.copy()
        return scored
    finally:
        with state.score_cache_lock:
            inflight = state.stock_score_inflight.pop(key, None)
            if inflight is not None:
                inflight.set()


def stock_score_calendar_days(state: DashboardState) -> int:
    cfg = state.config.get("scoring", {})
    lookbacks = [
        cfg.get("lookback_short", 20),
        cfg.get("lookback_mid", 60),
        cfg.get("lookback_long", 120),
        cfg.get("ma_mid_window", 60),
        cfg.get("atr_window", 20),
        cfg.get("effective_move_window", 20),
        cfg.get("gap_stability_window", 20),
        cfg.get("close_position_window", 10),
        cfg.get("rsi_window", 14),
        20,
    ]
    max_lookback = max(int(value or 0) for value in lookbacks)
    return max(190, int(max_lookback * 1.7) + 20)


def load_stock_latest_indicator_data(db_path: Path, market: str | None, start_date, end_date) -> pd.DataFrame:
    if not Path(db_path).exists():
        return pd.DataFrame()
    market_columns = stock_universe_columns(db_path)
    industry_columns = industry_select_columns(market_columns)
    clauses = ["bars.timeframe = '1d'", "bars.trade_date BETWEEN ? AND ?", "bars.market IN ('a_share', 'hk', 'us')"]
    params: list = [start_date, end_date]
    if market and market != "ALL":
        clauses.append("bars.market = ?")
        params.append(market)
    select_industries = ",\n                   ".join(f"{expr} AS {alias}" for alias, expr in industry_columns.items())
    with connect_market_db(db_path, read_only=True) as con:
        data = con.execute(
            f"""
            WITH base AS (
                SELECT bars.trade_date AS date,
                       bars.market,
                       bars.symbol,
                       coalesce(universe.name, bars.symbol) AS name,
                       bars.open,
                       bars.high,
                       bars.low,
                       bars.close,
                       bars.volume,
                       coalesce(bars.amount, bars.close * bars.volume) AS amount,
                       bars.market AS asset_class,
                       {select_industries},
                       lag(bars.close) OVER symbol_window AS prev_close,
                       lag(bars.close, 20) OVER symbol_window AS close_20d,
                       lag(bars.close, 60) OVER symbol_window AS close_60d,
                       lag(bars.close, 120) OVER symbol_window AS close_120d
                FROM market_ohlcv AS bars
                LEFT JOIN market_universe AS universe
                  ON bars.market = universe.market
                 AND bars.symbol = universe.symbol
                WHERE {' AND '.join(clauses)}
                WINDOW symbol_window AS (PARTITION BY bars.market, bars.symbol ORDER BY bars.trade_date)
            ),
            features AS (
                SELECT *,
                       close / nullif(close_20d, 0) - 1 AS return_20d,
                       close / nullif(close_60d, 0) - 1 AS return_60d,
                       close / nullif(close_120d, 0) - 1 AS return_120d,
                       greatest(
                           high - low,
                           abs(high - prev_close),
                           abs(low - prev_close)
                       ) AS true_range,
                       abs(close - prev_close) AS abs_move,
                       greatest(close - prev_close, 0) AS rsi_gain,
                       greatest(prev_close - close, 0) AS rsi_loss,
                       (close - low) / nullif(high - low, 0) AS daily_close_position,
                       avg(close) OVER symbol_window_20 AS ema20,
                       avg(close) OVER symbol_window_60 AS ma60,
                       avg(coalesce(amount, close * volume)) OVER symbol_window_20 AS avg_amount_20d,
                       avg(greatest(
                           high - low,
                           abs(high - prev_close),
                           abs(low - prev_close)
                       )) OVER symbol_window_20 AS atr20,
                       sum(abs(close - prev_close)) OVER symbol_window_20 AS path_20d,
                       avg((close - low) / nullif(high - low, 0)) OVER symbol_window_10 AS close_position_quality,
                       avg(greatest(close - prev_close, 0)) OVER symbol_window_14 AS avg_rsi_gain,
                       avg(greatest(prev_close - close, 0)) OVER symbol_window_14 AS avg_rsi_loss
                FROM base
                WINDOW symbol_window_10 AS (PARTITION BY market, symbol ORDER BY date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW),
                       symbol_window_14 AS (PARTITION BY market, symbol ORDER BY date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW),
                       symbol_window_20 AS (PARTITION BY market, symbol ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),
                       symbol_window_60 AS (PARTITION BY market, symbol ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)
            ),
            scored_inputs AS (
                SELECT *,
                       atr20 / nullif(close, 0) AS atr20_pct,
                       return_20d / nullif(atr20 / nullif(close, 0), 0) AS return_atr_20d,
                       abs(close - close_20d) / nullif(path_20d, 0) AS effective_move_20d,
                       close / nullif(ema20, 0) - 1 AS ma20_gap,
                       -stddev_samp(close / nullif(ema20, 0) - 1) OVER (
                           PARTITION BY market, symbol ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                       ) AS ma20_gap_stability,
                       CASE
                           WHEN avg_rsi_loss = 0 THEN 100
                           WHEN avg_rsi_loss IS NULL THEN NULL
                           ELSE 100 - 100 / (1 + avg_rsi_gain / avg_rsi_loss)
                       END AS rsi14,
                       CASE WHEN avg_amount_20d > 0 THEN ln(avg_amount_20d) END AS liquidity,
                       row_number() OVER (PARTITION BY market, symbol ORDER BY date DESC) AS latest_rank
                FROM features
            )
            SELECT date,
                   market,
                   symbol,
                   name,
                   asset_class,
                   open,
                   high,
                   low,
                   close,
                   volume,
                   amount,
                   industry_level1,
                   industry_level2,
                   industry_level3,
                   return_20d,
                   return_60d,
                   return_120d,
                   ema20,
                   ma60,
                   atr20,
                   atr20_pct,
                   return_atr_20d,
                   effective_move_20d,
                   ma20_gap,
                   ma20_gap_stability,
                   daily_close_position,
                   close_position_quality,
                   rsi14,
                   avg_amount_20d,
                   liquidity
            FROM scored_inputs
            WHERE latest_rank = 1
            ORDER BY market, symbol
            """,
            params,
        ).fetch_df()
    if data.empty:
        return data
    data["date"] = pd.to_datetime(data["date"])
    for column in [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "return_20d",
        "return_60d",
        "return_120d",
        "ema20",
        "ma60",
        "atr20",
        "atr20_pct",
        "return_atr_20d",
        "effective_move_20d",
        "ma20_gap",
        "ma20_gap_stability",
        "daily_close_position",
        "close_position_quality",
        "rsi14",
        "avg_amount_20d",
        "liquidity",
    ]:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    return data


def latest_stock_rows(scored: pd.DataFrame) -> pd.DataFrame:
    if scored.empty:
        return scored.copy()
    data = scored.copy()
    data["date"] = pd.to_datetime(data["date"])
    data = data.sort_values(["symbol", "date"])
    return data.groupby("symbol", as_index=False, sort=False).tail(1).reset_index(drop=True)


def load_stock_market_data(db_path: Path, market: str | None, start_date, end_date) -> pd.DataFrame:
    if not Path(db_path).exists():
        return pd.DataFrame()
    market_columns = stock_universe_columns(db_path)
    industry_columns = industry_select_columns(market_columns)
    clauses = ["bars.timeframe = '1d'", "bars.trade_date BETWEEN ? AND ?", "bars.market IN ('a_share', 'hk', 'us')"]
    params: list = [start_date, end_date]
    if market and market != "ALL":
        clauses.append("bars.market = ?")
        params.append(market)
    select_industries = ",\n                   ".join(f"{expr} AS {alias}" for alias, expr in industry_columns.items())
    with connect_market_db(db_path, read_only=True) as con:
        data = con.execute(
            f"""
            SELECT bars.trade_date AS date,
                   bars.market,
                   bars.symbol,
                   coalesce(universe.name, bars.symbol) AS name,
                   bars.open,
                   bars.high,
                   bars.low,
                   bars.close,
                   bars.volume,
                   bars.amount,
                   bars.market AS asset_class,
                   {select_industries}
            FROM market_ohlcv AS bars
            LEFT JOIN market_universe AS universe
              ON bars.market = universe.market
             AND bars.symbol = universe.symbol
            WHERE {' AND '.join(clauses)}
            ORDER BY bars.symbol, bars.trade_date
            """,
            params,
        ).fetch_df()
    if data.empty:
        return data
    data["date"] = pd.to_datetime(data["date"])
    for column in ["open", "high", "low", "close", "volume", "amount"]:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data["amount"] = data["amount"].fillna(data["close"] * data["volume"])
    return data


def load_stock_latest_snapshot(db_path: Path, market: str | None) -> pd.DataFrame:
    if not Path(db_path).exists():
        return pd.DataFrame()
    market_columns = stock_universe_columns(db_path)
    industry_columns = industry_select_columns(market_columns)
    clauses = ["bars.timeframe = '1d'", "bars.market IN ('a_share', 'hk', 'us')"]
    params: list = []
    if market and market != "ALL":
        clauses.append("bars.market = ?")
        params.append(market)
    select_industries = ",\n                   ".join(f"{expr} AS {alias}" for alias, expr in industry_columns.items())
    with connect_market_db(db_path, read_only=True) as con:
        data = con.execute(
            f"""
            WITH latest AS (
                SELECT market, symbol, max(trade_date) AS trade_date
                FROM market_ohlcv
                WHERE timeframe = '1d'
                  AND market IN ('a_share', 'hk', 'us')
                GROUP BY market, symbol
            )
            SELECT bars.trade_date AS date,
                   bars.market,
                   bars.symbol,
                   coalesce(universe.name, bars.symbol) AS name,
                   bars.open,
                   bars.high,
                   bars.low,
                   bars.close,
                   bars.volume,
                   bars.amount,
                   bars.market AS asset_class,
                   {select_industries}
            FROM market_ohlcv AS bars
            JOIN latest
              ON bars.market = latest.market
             AND bars.symbol = latest.symbol
             AND bars.trade_date = latest.trade_date
            LEFT JOIN market_universe AS universe
              ON bars.market = universe.market
             AND bars.symbol = universe.symbol
            WHERE {' AND '.join(clauses)}
            ORDER BY bars.market, bars.symbol
            """,
            params,
        ).fetch_df()
    if data.empty:
        return data
    data["date"] = pd.to_datetime(data["date"])
    for column in ["open", "high", "low", "close", "volume", "amount"]:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data["amount"] = data["amount"].fillna(data["close"] * data["volume"])
    return attach_stock_industries(data)


def stock_universe_columns(db_path: Path) -> set[str]:
    with connect_market_db(db_path, read_only=True) as con:
        info = con.execute("PRAGMA table_info('market_universe')").fetch_df()
    return set(info["name"].astype(str).tolist())


def industry_select_columns(columns: set[str]) -> dict[str, str]:
    candidates = {
        "industry_level1": ["industry_level1", "sector_level1", "sector", "industry", "sw_l1", "citic_l1"],
        "industry_level2": ["industry_level2", "sector_level2", "subsector", "sw_l2", "citic_l2"],
        "industry_level3": ["industry_level3", "sector_level3", "segment", "sw_l3", "citic_l3"],
    }
    out = {}
    for alias, names in candidates.items():
        matched = next((name for name in names if name in columns), None)
        out[alias] = f"universe.{matched}" if matched else "NULL"
    return out


def attach_stock_industries(scored: pd.DataFrame) -> pd.DataFrame:
    out = scored.copy()
    for column in ["industry_level1", "industry_level2", "industry_level3"]:
        if column not in out:
            out[column] = pd.NA
    inferred = out.apply(lambda row: infer_stock_industry(row.get("name"), row.get("symbol"), row.get("market")), axis=1)
    for index, column in enumerate(["industry_level1", "industry_level2", "industry_level3"]):
        fallback = inferred.map(lambda item: item[index])
        values = out[column].astype("string")
        out[column] = values.where(values.notna() & values.str.strip().ne(""), fallback)
    return out


def infer_stock_industry(name, symbol, market) -> tuple[str, str, str]:
    text = f"{name or ''} {symbol or ''}".casefold()
    rules = [
        ("金融", "银行", "银行", ["银行", "bank"]),
        ("金融", "证券保险", "券商保险", ["证券", "券", "保险", "期货", "信托", "capital"]),
        ("房地产", "地产开发", "房地产", ["地产", "置业", "物业", "房", "land", "properties"]),
        ("医药生物", "医药制造", "医药", ["药", "医", "生物", "医疗", "health", "pharma", "bio", "amgen"]),
        ("科技", "半导体电子", "芯片电子", ["半导体", "芯片", "电子", "光电", "micro", "semiconductor"]),
        ("科技", "互联网平台", "互联网软件", ["腾讯", "阿里", "美团", "京东", "百度", "网易", "互联网", "游戏", "控股", "holdings", "alphabet", "google", "meta platforms", "amazon"]),
        ("科技", "软件通信", "软件通信", ["微软", "思科", "软件", "信息", "通信", "网络", "数据", "科技", "tech", "technology", "software", "cloud", "internet", "microsoft", "cisco"]),
        ("科技", "半导体电子", "芯片电子", ["nvidia", "英伟达", "英特尔", "intel"]),
        ("科技", "消费电子", "消费电子", ["apple", "iphone", "consumer electronics"]),
        ("新能源", "电池光伏", "新能源设备", ["新能源", "电池", "锂", "光伏", "太阳", "solar", "battery"]),
        ("汽车", "整车零部件", "汽车链", ["汽车", "汽配", "轮胎", "auto", "motor", "vehicle", "tesla"]),
        ("消费", "食品饮料", "食品饮料", ["星巴克", "食品", "酒", "饮", "乳", "味", "food", "beverage", "starbucks"]),
        ("消费", "商贸家电", "可选消费", ["百货", "商贸", "家电", "旅游", "酒店", "免税", "retail"]),
        ("能源化工", "石油煤炭", "传统能源", ["石油", "煤", "能源", "油气", "petro", "energy", "exxon"]),
        ("能源化工", "基础化工", "化工材料", ["化工", "化学", "橡胶", "塑料", "chemical"]),
        ("材料", "钢铁有色", "金属材料", ["钢", "铝", "铜", "钨", "矿", "有色", "metal", "mining"]),
        ("工业制造", "机械设备", "高端制造", ["机械", "机电", "重工", "装备", "制造", "machine"]),
        ("工业制造", "国防军工", "军工航空", ["航天", "航空", "军", "船舶", "defense", "aero"]),
        ("交通运输", "交运物流", "运输物流", ["港", "机场", "航空", "高速", "物流", "铁路", "shipping", "airlines"]),
        ("公用事业", "电力环保", "公共服务", ["电力", "水务", "燃气", "环保", "utility", "utilities", "power"]),
        ("建筑建材", "建筑材料", "工程建材", ["建筑", "建材", "水泥", "工程", "construction"]),
        ("农业", "农林牧渔", "农业", ["农业", "农", "种业", "牧", "渔", "agri"]),
    ]
    for level1, level2, level3, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return level1, level2, level3
    market_label = {"a_share": "A股", "hk": "港股", "us": "美股"}.get(str(market or ""), "股票")
    prefix = str(symbol or "")[:2] or "其他"
    return "其他", f"{market_label}其他", f"{market_label}其他-{prefix}"


def build_stock_industry_groups(rows: pd.DataFrame, level: str, sort: str) -> pd.DataFrame:
    if rows.empty:
        return pd.DataFrame()
    level_columns = {"level1": ["industry_level1"], "level2": ["industry_level1", "industry_level2"], "level3": ["industry_level1", "industry_level2", "industry_level3"]}[level]
    data = rows.copy()
    data["score_for_sort"] = pd.to_numeric(data["total_score"], errors="coerce")
    grouped_rows = []
    for key, group in data.groupby(level_columns, dropna=False, sort=False):
        key_values = key if isinstance(key, tuple) else (key,)
        ordered = group.sort_values(["daily_rank", "total_score"], ascending=[True, False], na_position="last")
        leader = ordered.iloc[0]
        top_scores = pd.to_numeric(ordered["total_score"], errors="coerce").dropna().head(5)
        category_score = float(top_scores.mean()) if not top_scores.empty else None
        label_parts = [str(value) for value in key_values if pd.notna(value) and str(value)]
        grouped_rows.append(
            {
                "group_key": " / ".join(label_parts),
                "group_name": label_parts[-1] if label_parts else "未分类",
                "level1": label_parts[0] if len(label_parts) > 0 else "未分类",
                "level2": label_parts[1] if len(label_parts) > 1 else None,
                "level3": label_parts[2] if len(label_parts) > 2 else None,
                "market": leader.get("market"),
                "group_rank": None,
                "stock_count": int(len(group)),
                "category_score": category_score,
                "top_score": best_numeric(group, "total_score"),
                "return_20d": mean_numeric(group, "return_20d"),
                "return_60d": mean_numeric(group, "return_60d"),
                "return_120d": mean_numeric(group, "return_120d"),
                "amount": float(pd.to_numeric(group["amount"], errors="coerce").sum()) if "amount" in group else None,
                "avg_amount_20d": float(pd.to_numeric(group["avg_amount_20d"], errors="coerce").sum()) if "avg_amount_20d" in group else None,
                "leader_symbol": leader.get("symbol"),
                "leader_name": leader.get("name"),
                "members": [stock_member_payload(row) for _, row in ordered.head(80).iterrows()],
            }
        )
    out = pd.DataFrame(grouped_rows)
    if out.empty:
        return out
    sort_columns = {
        "group_rank": "category_score",
        "category_score": "category_score",
        "top_score": "top_score",
        "return_20d": "return_20d",
        "return_60d": "return_60d",
        "return_120d": "return_120d",
        "amount": "amount",
    }
    sort_col = sort_columns.get(sort, "category_score")
    out = out.sort_values(sort_col, ascending=False, na_position="last").reset_index(drop=True)
    out["group_rank"] = range(1, len(out) + 1)
    return out


def mean_numeric(group: pd.DataFrame, column: str, default=None):
    if column not in group:
        return default
    values = pd.to_numeric(group[column], errors="coerce").dropna()
    if values.empty:
        return default
    return float(values.mean())


def stock_member_payload(row: pd.Series) -> dict:
    columns = [
        "daily_rank",
        "date",
        "symbol",
        "name",
        "market",
        "industry_level1",
        "industry_level2",
        "industry_level3",
        "close",
        "total_score",
        "return_20d",
        "return_60d",
        "return_120d",
        "amount",
        "avg_amount_20d",
        "overheat_penalty",
        "return_atr_20d",
    ]
    return {column: row.get(column) for column in columns if column in row}


def latest_etf_date(state: DashboardState) -> pd.Timestamp | None:
    with connect(state.db_path) as con:
        value = con.execute("SELECT max(date) FROM etf_daily").fetchone()[0]
    return pd.to_datetime(value) if value is not None else None


def scored_window(
    state: DashboardState,
    latest: pd.Timestamp,
    asset_classes: list[str] | None = None,
    simulation_days: int | None = None,
) -> pd.DataFrame:
    class_key = tuple(sorted(asset_classes or []))
    calendar_days = max(420, int((simulation_days or 0) * 1.45) + 260)
    key = (latest.date().isoformat(), class_key, calendar_days)
    with state.score_cache_lock:
        cached = state.score_cache.get(key)
        if cached is not None:
            return cached.copy()
        start = (latest - pd.Timedelta(days=calendar_days)).date()
        data = load_market_data_from_db(
            state.db_path,
            start_date=str(start),
            end_date=str(latest.date()),
            asset_classes=asset_classes,
        )
        if data.empty:
            scored = pd.DataFrame()
        else:
            indicators = add_indicators(data, state.config)
            scored = calculate_score(indicators, state.config)
        state.score_cache[key] = scored.copy()
        return scored


def data_freshness(latest_date) -> dict:
    latest = pd.to_datetime(latest_date).date() if latest_date is not None else None
    expected = expected_etf_trade_date()
    return {
        "latest_date": latest,
        "expected_date": expected,
        "is_current": bool(latest and latest >= expected),
    }


def expected_etf_trade_date() -> datetime.date:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    target = now.date()
    if now.weekday() >= 5 or now.time() < time(16, 0):
        target -= timedelta(days=1)
    while target.weekday() >= 5:
        target -= timedelta(days=1)
    return target


def market_group(asset_class: str | None) -> str:
    value = str(asset_class or "OTHER")
    if value.startswith("A_SHARE"):
        return "A_SHARE"
    if value.startswith("HK"):
        return "HK"
    if value.startswith("US"):
        return "US"
    if value.startswith("COMMODITY"):
        return "COMMODITY"
    if value in {"BOND", "DIVIDEND", "LOW_VOL"}:
        return "INCOME"
    return "OTHER"


def market_group_counts(by_class: pd.DataFrame) -> pd.DataFrame:
    if by_class.empty:
        return pd.DataFrame(columns=["market", "etf_count"])
    out = by_class.copy()
    out["market"] = out["asset_class"].map(market_group)
    return out.groupby("market", as_index=False)["etf_count"].sum().sort_values("etf_count", ascending=False)


def asset_classes_for_market(market: str | None) -> list[str] | None:
    groups = {
        "A_SHARE": ["A_SHARE_BROAD", "A_SHARE_INDUSTRY"],
        "HK": ["HK_BROAD", "HK_TECH"],
        "US": ["US_BROAD", "US_TECH"],
        "COMMODITY": ["COMMODITY_GOLD", "COMMODITY_OIL", "COMMODITY_METAL"],
        "INCOME": ["BOND", "DIVIDEND", "LOW_VOL"],
        "OTHER": ["OTHER"],
    }
    return groups.get(str(market or "ALL"), None)


def build_theme_groups(rows: pd.DataFrame, sort_key: str, ascending: bool) -> pd.DataFrame:
    data = rows.copy()
    data["market"] = data["asset_class"].map(market_group)
    data["theme"] = data.apply(lambda row: classify_etf_theme(row.get("name"), row.get("asset_class")), axis=1)
    data["theme_key"] = data["market"] + ":" + data["theme"]

    grouped = []
    for _, group in data.groupby("theme_key", sort=False):
        ordered = group.sort_values("daily_rank", ascending=True, na_position="last")
        leader = ordered.iloc[0]
        members = ordered.head(3)
        grouped.append(
            {
                "group_rank": int(leader["daily_rank"]) if pd.notna(leader["daily_rank"]) else None,
                "theme": leader["theme"],
                "market": leader["market"],
                "asset_class": leader["asset_class"],
                "symbol": leader["symbol"],
                "name": leader["name"],
                "member_count": int(len(group)),
                "total_score": float(group["total_score"].max()),
                "avg_score": float(group["total_score"].mean()),
                "return_20d": best_numeric(group, "return_20d"),
                "return_60d": best_numeric(group, "return_60d"),
                "return_120d": best_numeric(group, "return_120d"),
                "amount": float(group["amount"].sum()) if "amount" in group else None,
                "fund_size": best_numeric(group, "fund_size"),
                "overheat_penalty": best_numeric(group, "overheat_penalty", default=0.0),
                "members": [member_payload(row) for _, row in members.iterrows()],
            }
        )

    out = pd.DataFrame(grouped)
    if out.empty:
        return out
    if sort_key == "daily_rank":
        sort_col = "group_rank"
    elif sort_key == "total_score":
        sort_col = "total_score"
    else:
        sort_col = sort_key if sort_key in out.columns else "group_rank"
    return out.sort_values(sort_col, ascending=ascending, na_position="last").reset_index(drop=True)


def best_numeric(group: pd.DataFrame, column: str, default=None):
    if column not in group:
        return default
    values = pd.to_numeric(group[column], errors="coerce").dropna()
    if values.empty:
        return default
    return float(values.max())


def member_payload(row: pd.Series) -> dict:
    columns = [
        "daily_rank",
        "symbol",
        "name",
        "asset_class",
        "close",
        "total_score",
        "return_20d",
        "return_60d",
        "return_120d",
        "return_atr_20d",
        "effective_move_20d",
        "ma20_gap_stability",
        "close_position_quality",
        "liquidity",
        "overheat_penalty",
        "return_20d_score",
        "return_60d_score",
        "return_120d_score",
        "return_atr_20d_score",
        "effective_move_20d_score",
        "ma20_gap_stability_score",
        "close_position_quality_score",
        "liquidity_score",
    ]
    return {column: row.get(column) for column in columns}


def safe_return(series: pd.Series, window: int) -> float | None:
    if len(series) <= window or series.iloc[-window - 1] == 0:
        return None
    return float(series.iloc[-1] / series.iloc[-window - 1] - 1)


def first(params: dict[str, list[str]], key: str, default: str | None = None) -> str | None:
    values = params.get(key)
    return values[0] if values else default


def to_jsonable(value):
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_jsonable(v) for v in value]
    if isinstance(value, pd.DataFrame):
        return to_jsonable(value.to_dict("records"))
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if pd.isna(value):
        return None
    return value


if __name__ == "__main__":
    main()
