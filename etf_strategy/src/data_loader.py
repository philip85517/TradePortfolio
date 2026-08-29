from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = {
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
}


def load_market_data(path: str | Path | None = None) -> pd.DataFrame:
    if path is None:
        return generate_sample_data()

    path = Path(path)
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path, dtype={"symbol": str})
    elif path.suffix.lower() in {".parquet", ".pq"}:
        df = pd.read_parquet(path)
    elif path.suffix.lower() in {".duckdb", ".db"}:
        from .data_store import load_market_data_from_db

        return load_market_data_from_db(path)
    else:
        raise ValueError(f"Unsupported data file: {path}")

    return normalize_market_data(df)


def normalize_market_data(df: pd.DataFrame) -> pd.DataFrame:
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    out = df.copy()
    out["date"] = pd.to_datetime(out["date"])
    out["listing_date"] = pd.to_datetime(out["listing_date"])
    out = out.sort_values(["date", "symbol"]).reset_index(drop=True)
    return out


def split_meta_and_prices(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
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
    meta = df.sort_values("date").groupby("symbol", as_index=False).tail(1)[meta_cols]
    return meta.reset_index(drop=True), df.copy()


def generate_sample_data(symbols: int = 8, periods: int = 260) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=periods)
    asset_classes = [
        "A_SHARE_BROAD",
        "US_TECH",
        "HK_TECH",
        "COMMODITY_GOLD",
        "BOND",
        "DIVIDEND",
        "A_SHARE_INDUSTRY",
        "US_BROAD",
    ]
    rows = []
    for idx in range(symbols):
        symbol = f"ETF{idx + 1:03d}"
        base = 1.0 + idx * 0.1
        trend = 0.0002 + idx * 0.00008
        noise = rng.normal(0, 0.008 + idx * 0.0005, size=periods)
        close = base * np.cumprod(1 + trend + noise)
        open_ = close * (1 + rng.normal(0, 0.003, size=periods))
        high = np.maximum(open_, close) * (1 + rng.uniform(0.001, 0.012, size=periods))
        low = np.minimum(open_, close) * (1 - rng.uniform(0.001, 0.012, size=periods))
        amount = rng.uniform(4e7, 1.2e8, size=periods)
        listing = dates[0] - pd.Timedelta(days=365)
        for i, date in enumerate(dates):
            rows.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "name": f"Sample ETF {idx + 1}",
                    "open": open_[i],
                    "high": high[i],
                    "low": low[i],
                    "close": close[i],
                    "volume": amount[i] / close[i],
                    "amount": amount[i],
                    "fund_size": 8e8 + idx * 1e8,
                    "premium_discount_rate": rng.normal(0, 0.005),
                    "asset_class": asset_classes[idx % len(asset_classes)],
                    "is_leverage": False,
                    "is_inverse": False,
                    "is_active": False,
                    "is_single_stock": False,
                    "listing_date": listing,
                }
            )
    return normalize_market_data(pd.DataFrame(rows))
