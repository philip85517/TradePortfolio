"""技术指标：动量、均线、ATR、有效移动、流动性。"""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_indicators(market_data: pd.DataFrame, config: dict) -> pd.DataFrame:
    """按 (symbol, date) 计算指标，返回带指标列的 DataFrame。

    market_data 必须已截断到 signal_date（未来数据防护在策略入口执行）。
    """
    alpha = config.get("alpha", {})
    mom = int(alpha.get("momentum_window", 60))
    short = int(alpha.get("short_window", 20))
    atr_w = int(alpha.get("atr_window", 20))
    eff_w = int(alpha.get("effective_move_window", 20))
    ema_w = int(config.get("entry", {}).get("ema_window", 20))
    ma_w = int(config.get("trend_filter", {}).get("moving_average_window", 60))

    out = market_data.sort_values(["symbol", "date"]).copy()
    g = out.groupby("symbol", group_keys=False)

    out["return_60d"] = g["close"].transform(lambda s: s.pct_change(mom))
    out["return_20d"] = g["close"].transform(lambda s: s.pct_change(short))
    out["ma60"] = g["close"].transform(lambda s: s.rolling(ma_w).mean())
    out["ema20"] = g["close"].transform(lambda s: s.ewm(span=ema_w, adjust=False).mean())

    prev_close = g["close"].shift(1)
    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr20"] = g["tr"].transform(lambda s: s.rolling(atr_w).mean()) if "tr" in out else pd.Series(np.nan, index=out.index)
    out["atr20"] = tr.groupby(out["symbol"]).transform(lambda s: s.rolling(atr_w).mean())
    out["atr20_pct"] = out["atr20"] / out["close"].replace(0, np.nan)
    out["return_atr_20d"] = out["return_20d"] / out["atr20_pct"].replace(0, np.nan)
    out["effective_move_20d"] = g["close"].transform(lambda s: s / s.shift(eff_w) - 1.0)
    out["avg_amount_20d"] = g["amount"].transform(lambda s: s.rolling(short).mean())
    return out

