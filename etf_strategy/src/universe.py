from __future__ import annotations

import pandas as pd


THEME_RULES = [
    ("半导体/芯片", ["半导体", "芯片", "集成电路", "科创芯片"]),
    ("通信/光通信", ["通信", "5G", "光通信", "CPO"]),
    ("港股科技/互联网", ["恒生科技", "港股通互联网", "港股互联网", "港股通科技", "港股科技", "恒生互联网", "中概"]),
    ("美股科技", ["NASDAQ", "纳指", "美国科技", "标普科技"]),
    ("日本/亚太", ["日经", "日本", "东证", "TOPIX", "亚太"]),
    ("AI/软件/科技", ["人工智能", "AI", "软件", "云计算", "大数据", "数据", "计算机", "信息技术", "信创", "科技"]),
    ("机器人/工业母机", ["机器人", "工业母机", "机床", "高端制造"]),
    ("电力设备/电网", ["电网", "电力", "绿电", "储能", "电池"]),
    ("新能源车/智能车", ["新能源车", "汽车", "智能车", "车联网"]),
    ("光伏/新能源", ["光伏", "新能源", "碳中和"]),
    ("军工/国防", ["军工", "国防", "航天", "航空"]),
    ("医药/医疗", ["医药", "医疗", "创新药", "生物", "疫苗"]),
    ("金融/证券", ["证券", "券商", "金融", "银行", "保险", "非银"]),
    ("消费", ["消费", "食品", "酒", "农业", "家电", "旅游"]),
    ("红利/低波", ["红利", "股息", "低波"]),
    ("债券", ["债", "国开", "政金", "信用", "可转债"]),
    ("黄金/贵金属", ["黄金", "金ETF", "有色", "铜", "铝", "金属", "稀土"]),
    ("能源/油气", ["油", "煤", "能源化工", "油气"]),
    ("港股金融/地产", ["港股通金融", "香港证券", "港股通非银", "恒生金融", "恒生地产"]),
    ("宽基指数", ["沪深300", "中证500", "中证1000", "上证50", "创业板", "科创50", "A50", "宽基", "标普", "S&P", "恒生"]),
]

ASSET_CLASS_THEMES = {
    "A_SHARE_BROAD": "A股宽基",
    "A_SHARE_INDUSTRY": "A股行业其他",
    "HK_BROAD": "港股宽基",
    "HK_TECH": "港股科技/互联网",
    "US_BROAD": "美股宽基",
    "US_TECH": "美股科技",
    "COMMODITY_GOLD": "黄金/贵金属",
    "COMMODITY_OIL": "能源/油气",
    "COMMODITY_METAL": "黄金/贵金属",
    "BOND": "债券",
    "DIVIDEND": "红利/低波",
    "LOW_VOL": "红利/低波",
}


def filter_universe(etf_meta: pd.DataFrame, market_data: pd.DataFrame, config: dict) -> pd.DataFrame:
    cfg = config.get("universe_filter", {})
    latest_date = pd.to_datetime(market_data["date"]).max()
    sorted_data = market_data.sort_values(["symbol", "date"])
    latest_rows = sorted_data.groupby("symbol", as_index=False).tail(1)
    amount_20d = sorted_data.groupby("symbol").tail(20).groupby("symbol")["amount"].mean()

    last_120_dates = sorted(pd.to_datetime(market_data["date"]).drop_duplicates())[-120:]
    data_120 = market_data[market_data["date"].isin(last_120_dates)]
    counts_120 = data_120.groupby("symbol")["date"].nunique()
    expected_count = max(len(last_120_dates), 1)
    missing_ratio = 1 - counts_120 / expected_count

    meta = etf_meta.copy()
    meta["listing_date"] = pd.to_datetime(meta["listing_date"])
    enriched = meta.merge(latest_rows[["symbol", "premium_discount_rate"]], on="symbol", how="left")
    enriched["avg_amount_20d"] = enriched["symbol"].map(amount_20d)
    enriched["missing_ratio_120d"] = enriched["symbol"].map(missing_ratio).fillna(1.0)
    enriched["listing_days"] = (latest_date - enriched["listing_date"]).dt.days

    mask = enriched["listing_days"] >= cfg.get("min_listing_days", 180)
    mask &= enriched["avg_amount_20d"] >= cfg.get("min_avg_amount_20d", 30_000_000)
    mask &= enriched["fund_size"] >= cfg.get("min_fund_size", 500_000_000)
    mask &= enriched["premium_discount_rate"].abs() <= cfg.get("max_premium_abs", 0.03)
    mask &= enriched["missing_ratio_120d"] <= cfg.get("max_missing_ratio_120d", 0.1)

    if cfg.get("exclude_leverage", True):
        mask &= ~enriched["is_leverage"].astype(bool)
    if cfg.get("exclude_inverse", True):
        mask &= ~enriched["is_inverse"].astype(bool)
    if cfg.get("exclude_active", True):
        mask &= ~enriched["is_active"].astype(bool)
    if cfg.get("exclude_single_stock", True):
        mask &= ~enriched["is_single_stock"].astype(bool)

    return enriched.loc[mask].reset_index(drop=True)


def classify_etf_theme(name: str | None, asset_class: str | None) -> str:
    text = str(name or "").upper()
    for label, keywords in THEME_RULES:
        if any(keyword.upper() in text for keyword in keywords):
            return label
    return ASSET_CLASS_THEMES.get(str(asset_class or "OTHER"), "其他")


def deduplicate_by_theme(
    scored_etfs: pd.DataFrame,
    max_per_theme: int = 1,
    max_per_asset_class: int | None = None,
) -> pd.DataFrame:
    selected_rows = []
    theme_counts: dict[str, int] = {}
    class_counts: dict[str, int] = {}
    scored = scored_etfs.sort_values("total_score", ascending=False)

    for _, row in scored.iterrows():
        asset_class = row.get("asset_class", "OTHER")
        if max_per_asset_class is not None and class_counts.get(asset_class, 0) >= max_per_asset_class:
            continue

        theme = classify_etf_theme(row.get("name"), asset_class)
        if max_per_theme > 0 and theme_counts.get(theme, 0) >= max_per_theme:
            continue

        selected_rows.append(row)
        theme_counts[theme] = theme_counts.get(theme, 0) + 1
        class_counts[asset_class] = class_counts.get(asset_class, 0) + 1

    return pd.DataFrame(selected_rows).reset_index(drop=True)


def deduplicate_by_correlation(
    scored_etfs: pd.DataFrame,
    returns_matrix: pd.DataFrame,
    threshold: float = 0.8,
    max_per_asset_class: int = 2,
    max_per_theme: int = 1,
) -> pd.DataFrame:
    selected_rows = []
    class_counts: dict[str, int] = {}
    theme_counts: dict[str, int] = {}
    scored = scored_etfs.sort_values("total_score", ascending=False)

    for _, row in scored.iterrows():
        asset_class = row.get("asset_class", "OTHER")
        if class_counts.get(asset_class, 0) >= max_per_asset_class:
            continue

        theme = classify_etf_theme(row.get("name"), asset_class)
        if max_per_theme > 0 and theme_counts.get(theme, 0) >= max_per_theme:
            continue

        symbol = row["symbol"]
        too_correlated = False
        for selected in selected_rows:
            selected_symbol = selected["symbol"]
            if symbol in returns_matrix and selected_symbol in returns_matrix:
                corr = returns_matrix[symbol].corr(returns_matrix[selected_symbol])
                if pd.notna(corr) and corr > threshold:
                    too_correlated = True
                    break
        if too_correlated:
            continue

        selected_rows.append(row)
        class_counts[asset_class] = class_counts.get(asset_class, 0) + 1
        theme_counts[theme] = theme_counts.get(theme, 0) + 1

    return pd.DataFrame(selected_rows).reset_index(drop=True)
