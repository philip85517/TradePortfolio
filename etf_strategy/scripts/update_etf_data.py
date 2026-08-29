from __future__ import annotations

import argparse
import json
import sys
import subprocess
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data_store import DEFAULT_DB_PATH, connect, latest_data_date, upsert_market_data

LIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

FIELDS = [
    "date",
    "symbol",
    "name",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "fund_size",
    "premium_discount_rate",
    "asset_class",
    "is_leverage",
    "is_inverse",
    "is_active",
    "is_single_stock",
    "listing_date",
]


@dataclass
class EtfInfo:
    symbol: str
    market: int
    name: str
    fund_size: float
    amount: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update ETF daily bars from Eastmoney")
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--output", default="data/raw/etf_daily_5y.csv")
    parser.add_argument("--meta-output", default="data/raw/etf_meta_latest.csv")
    parser.add_argument("--summary-output", default="data/raw/update_summary.md")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="DuckDB database path")
    parser.add_argument("--no-db", action="store_true", help="Skip DuckDB write")
    parser.add_argument("--no-csv", action="store_true", help="Skip CSV export")
    parser.add_argument("--incremental", action="store_true", help="Fetch from the database latest date + 1 day")
    parser.add_argument("--replace-db", action="store_true", help="Delete the DuckDB file before writing")
    parser.add_argument("--limit", type=int, default=None, help="Limit symbols for smoke tests")
    parser.add_argument("--sleep", type=float, default=0.03)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--min-amount", type=float, default=0.0, help="Filter current amount before fetching")
    parser.add_argument("--min-fund-size", type=float, default=0.0, help="Filter current fund size before fetching")
    parser.add_argument(
        "--max-live-failure-streak",
        type=int,
        default=0,
        help="Stop live fetching after N consecutive failures. 0 means keep trying all symbols.",
    )
    parser.add_argument(
        "--carry-forward-missing",
        action="store_true",
        help="If live data cannot be fetched, copy latest known bars forward to the requested end date.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    end = pd.to_datetime(args.end_date).date() if args.end_date else date.today()
    if args.incremental and not args.no_db:
        latest = latest_data_date(args.db)
        start = (latest.date() + timedelta(days=1)) if latest is not None else None
    else:
        start = pd.to_datetime(args.start_date).date() if args.start_date else None
    if start is None:
        start = end - timedelta(days=365 * args.years + 2)
    if start > end:
        print(f"No update needed: database latest date is already >= {end.isoformat()}", flush=True)
        return
    start_text = start.strftime("%Y%m%d")
    end_text = end.strftime("%Y%m%d")
    db_path = Path(args.db)
    if args.replace_db and db_path.exists():
        db_path.unlink()

    try:
        infos = fetch_etf_list()
        list_source = "eastmoney_list"
    except Exception as exc:  # noqa: BLE001 - local metadata keeps incremental updates usable.
        infos = load_etf_list_from_db(db_path)
        list_source = f"local_db_fallback:{exc}"
        print(f"ETF list fallback: using {len(infos)} symbols from DuckDB because Eastmoney list failed: {exc}", flush=True)
    infos = [
        info
        for info in infos
        if info.amount >= args.min_amount and info.fund_size >= args.min_fund_size
    ]
    if args.limit:
        infos = infos[: args.limit]

    rows: list[pd.DataFrame] = []
    failures: list[dict] = []
    failure_streak = 0
    for idx, info in enumerate(infos, start=1):
        try:
            df = fetch_kline(info, start_text, end_text, args.retries)
            if df.empty:
                failures.append({"symbol": info.symbol, "name": info.name, "reason": "empty"})
                failure_streak += 1
            else:
                rows.append(df)
                failure_streak = 0
        except Exception as exc:  # noqa: BLE001 - update script should keep going.
            failures.append({"symbol": info.symbol, "name": info.name, "reason": str(exc)})
            failure_streak += 1
        if idx % 50 == 0:
            print(
                f"Fetched {idx}/{len(infos)} symbols, rows={sum(len(x) for x in rows)}, failures={len(failures)}",
                flush=True,
            )
        if args.max_live_failure_streak and failure_streak >= args.max_live_failure_streak and not rows:
            print(f"Stopping live fetch after {failure_streak} consecutive failures; no rows fetched.", flush=True)
            break
        if args.sleep:
            time.sleep(args.sleep)

    if not rows:
        summary_output = Path(args.summary_output)
        summary_output.parent.mkdir(parents=True, exist_ok=True)
        if args.carry_forward_missing and not args.no_db:
            carried = carry_forward_missing_dates(db_path, start, end)
            if not carried.empty:
                db_result = upsert_market_data(
                    carried,
                    db_path,
                    source="carry_forward",
                    failures=len(failures),
                    note=f"live update failed; copied latest available bars through {end.isoformat()}",
                )
                meta = build_meta(carried)
                write_summary(summary_output, carried, meta, failures, start, end, db_path, mode="carry_forward")
                print(
                    f"Carry-forward filled {db_result['rows']:,} rows for {db_result['symbols']:,} ETFs through {db_result['end_date']}",
                    flush=True,
                )
                print(f"Wrote summary to {summary_output}", flush=True)
                return
        write_empty_summary(summary_output, failures, start, end, db_path if not args.no_db else None)
        print(f"No ETF kline data fetched for {start.isoformat()} to {end.isoformat()}", flush=True)
        print(f"Wrote summary to {summary_output}", flush=True)
        return

    data = pd.concat(rows, ignore_index=True)
    data = data[FIELDS].sort_values(["date", "symbol"]).reset_index(drop=True)
    meta = build_meta(data)

    output = Path(args.output)
    meta_output = Path(args.meta_output)
    summary_output = Path(args.summary_output)
    if not args.no_csv:
        output.parent.mkdir(parents=True, exist_ok=True)
        meta_output.parent.mkdir(parents=True, exist_ok=True)
    summary_output.parent.mkdir(parents=True, exist_ok=True)

    db_result = None
    if not args.no_db:
        db_result = upsert_market_data(
            data,
            db_path,
            source="eastmoney",
            failures=len(failures),
            note=f"min_amount={args.min_amount}, min_fund_size={args.min_fund_size}, incremental={args.incremental}, list_source={list_source}",
        )
        print(f"Upserted {db_result['rows']:,} rows into DuckDB {db_path}", flush=True)

    if not args.no_csv:
        data.to_csv(output, index=False)
        meta.to_csv(meta_output, index=False)
        print(f"Wrote {len(data):,} rows for {data['symbol'].nunique():,} ETFs to {output}", flush=True)
        print(f"Wrote metadata for {len(meta):,} ETFs to {meta_output}", flush=True)

    write_summary(summary_output, data, meta, failures, start, end, db_path if not args.no_db else None)
    print(f"Wrote summary to {summary_output}", flush=True)


def fetch_etf_list() -> list[EtfInfo]:
    all_items = []
    page = 1
    page_size = 500
    while True:
        params = {
            "pn": page,
            "pz": page_size,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f6",
            "fs": "b:MK0021,b:MK0022,b:MK0023,b:MK0024",
            "fields": "f12,f13,f14,f6,f20,f21",
        }
        payload = request_json(LIST_URL, params)
        data = payload.get("data") or {}
        items = data.get("diff") or []
        if not items:
            break
        all_items.extend(items)
        if len(all_items) >= int(data.get("total") or 0):
            break
        page += 1

    infos = []
    for item in all_items:
        symbol = str(item.get("f12", "")).strip()
        name = str(item.get("f14", "")).strip()
        if not symbol or not name:
            continue
        infos.append(
            EtfInfo(
                symbol=symbol,
                market=int(item.get("f13", 0)),
                name=name,
                fund_size=to_float(item.get("f20")) or to_float(item.get("f21")),
                amount=to_float(item.get("f6")),
            )
        )
    return sorted(infos, key=lambda x: x.amount, reverse=True)


def load_etf_list_from_db(db_path: Path) -> list[EtfInfo]:
    if not db_path.exists():
        raise RuntimeError(f"No DuckDB file found for ETF list fallback: {db_path}")
    with connect(db_path) as con:
        df = con.execute(
            """
            SELECT symbol, any_value(name) AS name, any_value(fund_size) AS fund_size,
                   any_value(amount) AS amount
            FROM etf_daily
            WHERE date = (SELECT max(date) FROM etf_daily)
            GROUP BY symbol
            ORDER BY amount DESC NULLS LAST
            """
        ).fetch_df()
    if df.empty:
        raise RuntimeError("No ETF symbols found in DuckDB for ETF list fallback.")
    infos = [
        EtfInfo(
            symbol=str(row.symbol),
            market=eastmoney_market_code(str(row.symbol)),
            name=str(row.name),
            fund_size=to_float(row.fund_size),
            amount=to_float(row.amount),
        )
        for row in df.itertuples(index=False)
    ]
    return infos


def eastmoney_market_code(symbol: str) -> int:
    return 1 if symbol.startswith(("5", "6", "9")) else 0


def fetch_kline(info: EtfInfo, start: str, end: str, retries: int) -> pd.DataFrame:
    params = {
        "secid": f"{info.market}.{info.symbol}",
        "klt": 101,
        "fqt": 1,
        "beg": start,
        "end": end,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    payload = request_json(KLINE_URL, params, retries=retries)
    klines = ((payload.get("data") or {}).get("klines")) or []
    parsed = []
    for line in klines:
        parts = line.split(",")
        if len(parts) < 7:
            continue
        parsed.append(
            {
                "date": parts[0],
                "symbol": info.symbol,
                "name": info.name,
                "open": to_float(parts[1]),
                "close": to_float(parts[2]),
                "high": to_float(parts[3]),
                "low": to_float(parts[4]),
                "volume": to_float(parts[5]) * 100,
                "amount": to_float(parts[6]),
                "fund_size": info.fund_size,
                "premium_discount_rate": 0.0,
                "asset_class": classify_asset(info.name),
                "is_leverage": is_leverage(info.name),
                "is_inverse": is_inverse(info.name),
                "is_active": is_active(info.name),
                "is_single_stock": is_single_stock(info.name),
            }
        )
    df = pd.DataFrame(parsed)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df["listing_date"] = df["date"].min()
    return df


def build_meta(data: pd.DataFrame) -> pd.DataFrame:
    meta_cols = [
        "symbol",
        "name",
        "asset_class",
        "fund_size",
        "is_leverage",
        "is_inverse",
        "is_active",
        "is_single_stock",
        "listing_date",
    ]
    return data.sort_values("date").groupby("symbol", as_index=False).tail(1)[meta_cols].sort_values("symbol")


def carry_forward_missing_dates(db_path: Path, start: date, end: date) -> pd.DataFrame:
    if start > end:
        return pd.DataFrame(columns=FIELDS)
    with connect(db_path) as con:
        latest = con.execute("SELECT max(date) FROM etf_daily WHERE date < ?", [start]).fetchone()[0]
        if latest is None:
            return pd.DataFrame(columns=FIELDS)
        base = con.execute(
            """
            SELECT date, symbol, name, open, high, low, close, volume, amount,
                   fund_size, premium_discount_rate, asset_class, is_leverage,
                   is_inverse, is_active, is_single_stock, listing_date
            FROM etf_daily
            WHERE date = ?
            ORDER BY symbol
            """,
            [latest],
        ).fetch_df()
    if base.empty:
        return pd.DataFrame(columns=FIELDS)
    frames = []
    for target in pd.date_range(start, end, freq="B"):
        daily = base.copy()
        daily["date"] = target
        frames.append(daily)
    if not frames:
        return pd.DataFrame(columns=FIELDS)
    return pd.concat(frames, ignore_index=True)[FIELDS]


def write_summary(
    path: Path,
    data: pd.DataFrame,
    meta: pd.DataFrame,
    failures: list[dict],
    start: date,
    end: date,
    db_path: Path | None = None,
    mode: str = "live",
) -> None:
    latest = data["date"].max()
    earliest = data["date"].min()
    by_class = meta["asset_class"].value_counts().rename_axis("asset_class").reset_index(name="etf_count")
    content = [
        "# ETF 数据更新摘要",
        "",
        f"- 请求区间：{start.isoformat()} 至 {end.isoformat()}",
        f"- 实际数据区间：{earliest.date().isoformat()} 至 {latest.date().isoformat()}",
        f"- ETF 数量：{data['symbol'].nunique()}",
        f"- 日线行数：{len(data)}",
        f"- 失败数量：{len(failures)}",
        f"- 更新模式：{mode}",
        f"- DuckDB：{db_path if db_path is not None else '未写入'}",
        "",
        "## 资产类别分布",
        "",
        by_class.to_markdown(index=False),
        "",
    ]
    if failures:
        content.extend(
            [
                "## 抓取失败",
                "",
                pd.DataFrame(failures).head(50).to_markdown(index=False),
                "",
            ]
        )
    path.write_text("\n".join(content), encoding="utf-8")


def write_empty_summary(path: Path, failures: list[dict], start: date, end: date, db_path: Path | None = None) -> None:
    content = [
        "# ETF 数据更新摘要",
        "",
        f"- 请求区间：{start.isoformat()} 至 {end.isoformat()}",
        "- 实际数据区间：无新增数据",
        "- ETF 数量：0",
        "- 日线行数：0",
        f"- 失败数量：{len(failures)}",
        f"- DuckDB：{db_path if db_path is not None else '未写入'}",
        "",
    ]
    if failures:
        content.extend(
            [
                "## 抓取失败",
                "",
                pd.DataFrame(failures).head(50).to_markdown(index=False),
                "",
            ]
        )
    path.write_text("\n".join(content), encoding="utf-8")


def request_json(url: str, params: dict, retries: int = 3) -> dict:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(
                url,
                params=params,
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0 ETFStrategy/1.0"},
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001 - retry helper.
            last_error = exc
            try:
                return request_json_with_curl(url, params)
            except Exception as curl_exc:  # noqa: BLE001 - keep retrying requests/curl pair.
                last_error = curl_exc
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"request failed: {last_error}")


def request_json_with_curl(url: str, params: dict) -> dict:
    full_url = f"{url}?{urlencode(params, safe=',')}"
    result = subprocess.run(
        [
            "curl",
            "--noproxy",
            "*",
            "-sS",
            "-L",
            "--connect-timeout",
            "10",
            "--max-time",
            "25",
            "-A",
            "Mozilla/5.0 ETFStrategy/1.0",
            full_url,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"curl exited {result.returncode}")
    return json.loads(result.stdout)


def classify_asset(name: str) -> str:
    text = name.upper()
    if any(key in name for key in ["黄金", "金ETF"]):
        return "COMMODITY_GOLD"
    if any(key in name for key in ["油", "能源化工"]):
        return "COMMODITY_OIL"
    if any(key in name for key in ["有色", "铜", "铝", "金属", "稀土"]):
        return "COMMODITY_METAL"
    if any(key in name for key in ["债", "国开", "政金", "信用", "可转债"]):
        return "BOND"
    if any(key in name for key in ["红利", "股息"]):
        return "DIVIDEND"
    if any(key in name for key in ["低波", "低波动"]):
        return "LOW_VOL"
    if any(key in name for key in ["沪深300", "中证500", "中证1000", "上证50", "创业板", "科创", "A50", "宽基", "A股"]):
        return "A_SHARE_BROAD"
    if any(key in name for key in ["恒生科技", "港股通互联网", "中概", "港股互联网", "港股通科技", "港股科技", "恒生互联网"]):
        return "HK_TECH"
    if any(key in name for key in ["恒生", "港股", "H股", "香港"]):
        return "HK_BROAD"
    if any(key in text for key in ["NASDAQ", "纳指", "标普", "S&P", "美国"]):
        return "US_TECH" if any(key in name for key in ["纳指", "科技"]) else "US_BROAD"
    if "ETF" in text:
        return "A_SHARE_INDUSTRY"
    return "OTHER"


def is_leverage(name: str) -> bool:
    return any(key in name for key in ["杠杆", "两倍", "2倍", "2X", "双倍"])


def is_inverse(name: str) -> bool:
    return any(key in name for key in ["反向", "做空", "空头", "熊"])


def is_active(name: str) -> bool:
    return any(key in name for key in ["主动", "增强"])


def is_single_stock(name: str) -> bool:
    return "单票" in name or "个股" in name


def to_float(value) -> float:
    try:
        if value in {"-", None, ""}:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


if __name__ == "__main__":
    main()
