from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.market_data_store import DEFAULT_MARKET_DB_PATH, connect_market_db  # noqa: E402


CSRC_LEVEL1 = {
    "A": "农、林、牧、渔业",
    "B": "采矿业",
    "C": "制造业",
    "D": "电力、热力、燃气及水生产和供应业",
    "E": "建筑业",
    "F": "批发和零售业",
    "G": "交通运输、仓储和邮政业",
    "H": "住宿和餐饮业",
    "I": "信息传输、软件和信息技术服务业",
    "J": "金融业",
    "K": "房地产业",
    "L": "租赁和商务服务业",
    "M": "科学研究和技术服务业",
    "N": "水利、环境和公共设施管理业",
    "O": "居民服务、修理和其他服务业",
    "P": "教育",
    "Q": "卫生和社会工作",
    "R": "文化、体育和娱乐业",
    "S": "综合",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Update A-share industry metadata in market_universe from BaoStock")
    parser.add_argument("--db", default=str(DEFAULT_MARKET_DB_PATH))
    parser.add_argument("--csv", default=None, help="Optional CSV output for the fetched industry mapping")
    args = parser.parse_args()

    industries = fetch_baostock_industries()
    if industries.empty:
        raise SystemExit("BaoStock returned no industry rows")
    result = update_industries(industries, Path(args.db))
    if args.csv:
        Path(args.csv).parent.mkdir(parents=True, exist_ok=True)
        industries.to_csv(args.csv, index=False)
    print(f"Updated {result['matched']:,} A-share universe rows from {len(industries):,} industry rows")


def fetch_baostock_industries() -> pd.DataFrame:
    try:
        import baostock as bs
    except ImportError as exc:
        raise RuntimeError("BaoStock support requires `pip install baostock`.") from exc

    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_msg}")
    try:
        result = bs.query_stock_industry()
        if result.error_code != "0":
            raise RuntimeError(f"BaoStock industry query failed: {result.error_msg}")
        rows = []
        while result.next():
            rows.append(dict(zip(result.fields, result.get_row_data(), strict=False)))
    finally:
        bs.logout()

    raw = pd.DataFrame(rows)
    if raw.empty:
        return pd.DataFrame(columns=["market", "symbol", "industry_level1", "industry_level2", "industry_level3"])
    raw["symbol"] = raw["code"].astype(str).str.extract(r"(\d{6})", expand=False)
    parsed = raw["industry"].astype(str).map(parse_csrc_industry)
    out = pd.DataFrame(parsed.tolist(), columns=["industry_level1", "industry_level2", "industry_level3"])
    out.insert(0, "symbol", raw["symbol"])
    out.insert(0, "market", "a_share")
    out = out.dropna(subset=["symbol"])
    out = out[out["industry_level2"].astype(str).str.strip().ne("")]
    return out.drop_duplicates(["market", "symbol"], keep="last").reset_index(drop=True)


def parse_csrc_industry(value: str) -> tuple[str | None, str | None, str | None]:
    text = str(value or "").strip()
    if not text:
        return None, None, None
    match = re.match(r"^([A-Z])(\d{2})?(.+)$", text)
    if not match:
        return None, text, text
    letter, digits, name = match.groups()
    level1 = CSRC_LEVEL1.get(letter, letter)
    code = f"{letter}{digits}" if digits else letter
    industry_name = name.strip() or text
    return level1, f"{code} {industry_name}", industry_name


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
            WHERE market = 'a_share'
              AND industry_level1 IS NOT NULL
              AND industry_level2 IS NOT NULL
            """
        ).fetchone()[0]
        con.unregister("incoming_industries")
    return {"matched": int(matched or 0)}


if __name__ == "__main__":
    main()
