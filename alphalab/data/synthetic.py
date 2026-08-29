"""确定性合成行情生成器：相同输入必然产生相同输出，用于本地启动验证。"""

from __future__ import annotations

import zlib
from datetime import date

import numpy as np
import pandas as pd

from ..utils import parse_date, symbol_code, trading_days

ASSET_CLASS_POOL = [
    "A_SHARE_BROAD",
    "A_SHARE_INDUSTRY",
    "HK_BROAD",
    "HK_TECH",
    "US_BROAD",
    "COMMODITY_GOLD",
    "BOND",
]


def _symbol_seed(symbol: str, seed: int) -> int:
    return int(zlib.crc32(symbol_code(symbol).encode("utf-8"))) + int(seed) * 1000003


def generate_synthetic_market_data(
    symbols: list[str],
    start_date: str | date,
    end_date: str | date,
    seed: int = 42,
    include_warmup: bool = True,
) -> pd.DataFrame:
    """为每个标的生成确定性的日线 OHLCV。

    生成区间为 ``warmup_start..end_date``，warmup 前置 400 个自然日，
    保证策略指标（60 日动量、MA60 等）与上市天数过滤有足够历史。
    """
    s = parse_date(start_date)
    e = parse_date(end_date)
    warm_start = s - pd.Timedelta(days=400) if include_warmup else s
    days = trading_days(warm_start, e)
    rows: list[dict] = []
    for i, sym in enumerate(symbols):
        code = symbol_code(sym)
        rng = np.random.RandomState(_symbol_seed(sym, seed))
        n = len(days)
        base = 0.8 + (rng.rand() * 4.5)
        drift = rng.normal(0.00035, 0.0009)
        vol = rng.uniform(0.006, 0.020)
        rets = rng.normal(drift, vol, size=n)
        # 叠加弱周期项，使横截面排序存在但不过度
        cycle = 0.0006 * np.sin(np.arange(n) / 12.0 + rng.rand() * 6.28)
        rets = rets + cycle
        closes = base * np.cumprod(1.0 + rets)
        opens = closes / (1.0 + rng.normal(0.0, vol * 0.35, size=n))
        highs = np.maximum(opens, closes) * (1.0 + np.abs(rng.normal(0.0, vol * 0.4, size=n)))
        lows = np.minimum(opens, closes) * (1.0 - np.abs(rng.normal(0.0, vol * 0.4, size=n)))
        volumes = rng.lognormal(mean=16.5, sigma=0.8, size=n)
        amounts = volumes * (opens + closes) / 2.0
        fund_size = 2e9 + rng.rand() * 3e10
        listing_date = warm_start
        for j, d in enumerate(days):
            rows.append(
                {
                    "date": d,
                    "symbol": sym,
                    "name": f"SYN-{code}",
                    "open": round(float(opens[j]), 4),
                    "high": round(float(highs[j]), 4),
                    "low": round(float(lows[j]), 4),
                    "close": round(float(closes[j]), 4),
                    "volume": float(volumes[j]),
                    "amount": float(amounts[j]),
                    "fund_size": fund_size,
                    "premium_discount_rate": 0.0,
                    "asset_class": ASSET_CLASS_POOL[i % len(ASSET_CLASS_POOL)],
                    "is_leverage": False,
                    "is_inverse": False,
                    "is_active": False,
                    "is_single_stock": False,
                    "listing_date": listing_date,
                }
            )
    return pd.DataFrame(rows)

