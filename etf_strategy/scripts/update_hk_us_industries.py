from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_dashboard import infer_stock_industry  # noqa: E402
from src.market_data_store import DEFAULT_MARKET_DB_PATH, connect_market_db  # noqa: E402


US_FAMOUS_CATEGORIES = {
    "科技类": ("科技", "美股知名科技", "科技类"),
    "金融类": ("金融", "美股知名金融", "金融类"),
    "医药食品类": ("医药消费", "美股医药食品", "医药食品类"),
    "媒体类": ("传媒娱乐", "美股媒体", "媒体类"),
    "汽车能源类": ("汽车能源", "美股汽车能源", "汽车能源类"),
    "制造零售类": ("工业消费", "美股制造零售", "制造零售类"),
}


HK_LEVEL1_HINTS = [
    ("综合企业", ["综合企业"]),
    ("资讯科技业", ["软件", "互联网", "资讯科技", "半导体", "电子", "电讯", "通信", "云"]),
    ("金融业", ["银行", "证券", "保险", "金融", "信贷", "投资", "资本"]),
    ("地产建筑业", ["地产", "物业", "建筑", "房产", "置业"]),
    ("医疗保健业", ["医药", "医疗", "生物", "制药", "健康"]),
    ("非必需性消费", ["汽车", "零售", "娱乐", "媒体", "旅游", "酒店", "餐饮", "服饰", "家电", "家庭电器", "消费者"]),
    ("必需性消费", ["食品", "食物", "饮品", "饮料", "乳品", "农业", "个人护理", "消费品"]),
    ("能源业", ["石油", "煤", "能源", "燃气"]),
    ("原材料业", ["金属", "矿", "钢", "化工", "材料", "纸", "包装", "林木"]),
    ("工业", ["工业工程", "工用支援", "支援服务", "商业服务", "机械", "运输", "航空", "物流", "设备", "制造"]),
    ("公用事业", ["公用事业", "电力", "环保", "水务", "公共"]),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Update HK/US industry metadata in market_universe")
    parser.add_argument("--db", default=str(DEFAULT_MARKET_DB_PATH))
    parser.add_argument("--markets", nargs="*", default=["hk", "us"], choices=["hk", "us"])
    parser.add_argument(
        "--scope",
        choices=["downloaded", "universe"],
        default="downloaded",
        help="downloaded updates symbols that already have 1d bars; universe may be slow for HK because it uses one profile request per symbol.",
    )
    parser.add_argument("--max-symbols", type=int, default=None)
    parser.add_argument("--only-generic", action="store_true", help="Only update symbols whose existing industry is missing or generic.")
    parser.add_argument("--csv", default=None, help="Optional CSV output for the fetched mapping")
    parser.add_argument("--sleep", type=float, default=0.15)
    parser.add_argument("--no-hk-profile", action="store_true", help="Classify HK symbols locally from names without per-symbol profile requests.")
    args = parser.parse_args()

    symbols = selected_symbols(Path(args.db), args.markets, args.scope, args.max_symbols, only_generic=args.only_generic)
    frames = []
    if "hk" in args.markets:
        frames.append(fetch_hk_industries(symbols.get("hk", []), sleep_seconds=args.sleep, use_profile=not args.no_hk_profile))
    if "us" in args.markets:
        frames.append(fetch_us_industries(symbols.get("us", [])))

    industries = pd.concat([frame for frame in frames if not frame.empty], ignore_index=True) if frames else pd.DataFrame()
    if industries.empty:
        raise SystemExit("No HK/US industry rows produced")

    result = update_industries(industries, Path(args.db))
    if args.csv:
        Path(args.csv).parent.mkdir(parents=True, exist_ok=True)
        industries.to_csv(args.csv, index=False)
    print(f"Updated HK/US industry rows: input={len(industries):,}, matched={result['matched']:,}")


def selected_symbols(db_path: Path, markets: list[str], scope: str, max_symbols: int | None, only_generic: bool = False) -> dict[str, list[dict]]:
    with connect_market_db(db_path) as con:
        if scope == "downloaded":
            query = """
                SELECT universe.market, universe.symbol, universe.name, universe.industry_level1, universe.industry_level2, universe.industry_level3
                FROM market_universe AS universe
                JOIN (
                    SELECT DISTINCT market, symbol
                    FROM market_ohlcv
                    WHERE timeframe = '1d'
                ) AS bars
                  ON universe.market = bars.market
                 AND universe.symbol = bars.symbol
                WHERE universe.market IN (SELECT unnest(?::VARCHAR[]))
                ORDER BY universe.market, universe.symbol
            """
            rows = con.execute(query, [markets]).fetch_df()
        else:
            rows = con.execute(
                """
                SELECT market, symbol, name, industry_level1, industry_level2, industry_level3
                FROM market_universe
                WHERE market IN (SELECT unnest(?::VARCHAR[]))
                ORDER BY market, symbol
                """,
                [markets],
            ).fetch_df()
    if only_generic and not rows.empty:
        industry_text = (
            rows["industry_level1"].fillna("")
            + " "
            + rows["industry_level2"].fillna("")
            + " "
            + rows["industry_level3"].fillna("")
        )
        rows = rows[
            rows["industry_level1"].isna()
            | rows["industry_level1"].astype(str).str.strip().eq("")
            | industry_text.str.contains("其他", regex=False, na=False)
        ].copy()

    out: dict[str, list[dict]] = {}
    for market, frame in rows.groupby("market", sort=False):
        if max_symbols is not None:
            frame = frame.head(max_symbols)
        out[str(market)] = frame.to_dict("records")
    return out


def fetch_hk_industries(symbols: list[dict], sleep_seconds: float = 0.15, use_profile: bool = True) -> pd.DataFrame:
    if not symbols:
        return empty_industry_frame()
    import time

    import akshare as ak

    rows = []
    total = len(symbols)
    for index, item in enumerate(symbols, start=1):
        symbol = str(item["symbol"]).zfill(5)
        name = str(item.get("name") or symbol)
        industry = best_existing_industry(item) if not use_profile else ""
        if use_profile:
            try:
                profile = ak.stock_hk_company_profile_em(symbol=symbol)
                industry = str(profile.iloc[0].get("所属行业") or "").strip() if not profile.empty else ""
            except Exception as exc:  # noqa: BLE001 - keep batch updates best-effort.
                print(f"HK {symbol} profile failed: {exc}", file=sys.stderr)
        level1, level2, level3 = hk_industry_levels(industry, name, symbol)
        rows.append(
            {
                "market": "hk",
                "symbol": symbol,
                "industry_level1": level1,
                "industry_level2": level2,
                "industry_level3": level3,
            }
        )
        if use_profile and (index == 1 or index % 100 == 0 or index == total):
            print(f"HK industry profiles {index:,}/{total:,}", flush=True)
        if use_profile and sleep_seconds > 0:
            time.sleep(sleep_seconds)
    return pd.DataFrame(rows)


def hk_industry_levels(industry: str, name: str, symbol: str) -> tuple[str, str, str]:
    text = f"{industry} {name}".strip()
    for level1, keywords in HK_LEVEL1_HINTS:
        if any(keyword in text for keyword in keywords):
            label = industry or level1
            return level1, label, label
    if industry:
        return "其他", industry, industry
    return infer_stock_industry(name, symbol, "hk")


def best_existing_industry(item: dict) -> str:
    for key in ["industry_level3", "industry_level2", "industry_level1"]:
        value = str(item.get(key) or "").strip()
        if value and "其他" not in value:
            return value
    return ""


def fetch_us_industries(symbols: list[dict]) -> pd.DataFrame:
    if not symbols:
        return empty_industry_frame()
    import akshare as ak

    category_by_symbol: dict[str, tuple[str, str, str]] = {}
    for category, levels in US_FAMOUS_CATEGORIES.items():
        try:
            data = ak.stock_us_famous_spot_em(symbol=category)
        except Exception as exc:  # noqa: BLE001 - keep the rest of the mapping usable.
            print(f"US famous category {category} failed: {exc}", file=sys.stderr)
            continue
        for code in data.get("代码", pd.Series(dtype=str)).dropna().astype(str):
            ticker = code.split(".", 1)[-1].upper()
            category_by_symbol[ticker] = levels

    rows = []
    for item in symbols:
        symbol = str(item["symbol"]).upper()
        name = str(item.get("name") or symbol)
        levels = category_by_symbol.get(symbol) or infer_stock_industry(name, symbol, "us")
        rows.append(
            {
                "market": "us",
                "symbol": symbol,
                "industry_level1": levels[0],
                "industry_level2": levels[1],
                "industry_level3": levels[2],
            }
        )
    return pd.DataFrame(rows)


def update_industries(industry_df: pd.DataFrame, db_path: Path) -> dict:
    with connect_market_db(db_path) as con:
        con.register("incoming_industries", industry_df)
        con.execute(
            """
            UPDATE market_universe
               SET industry_level1 = incoming_industries.industry_level1,
                   industry_level2 = incoming_industries.industry_level2,
                   industry_level3 = incoming_industries.industry_level3
              FROM incoming_industries
             WHERE market_universe.market = incoming_industries.market
               AND market_universe.symbol = incoming_industries.symbol
            """
        )
        matched = con.execute(
            """
            SELECT count(*)
            FROM market_universe
            WHERE market IN ('hk', 'us')
              AND industry_level1 IS NOT NULL
              AND industry_level2 IS NOT NULL
            """
        ).fetchone()[0]
        con.unregister("incoming_industries")
    return {"matched": int(matched or 0)}


def empty_industry_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["market", "symbol", "industry_level1", "industry_level2", "industry_level3"])


if __name__ == "__main__":
    main()
