from __future__ import annotations

import pandas as pd

from src.scoring import build_watchlist, calculate_score


def _score_frame() -> pd.DataFrame:
    rows = []
    for i, ret in enumerate([0.01, 0.03, 0.02], start=1):
        rows.append(
            {
                "date": pd.Timestamp("2025-01-01"),
                "symbol": f"ETF{i:03d}",
                "name": f"ETF{i}",
                "asset_class": "A_SHARE_BROAD",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10 + i,
                "ema20": 10,
                "ma60": 9,
                "return_20d": ret,
                "return_60d": ret,
                "return_120d": ret,
                "atr20_pct": 0.02,
                "return_atr_20d": ret / 0.02,
                "effective_move_20d": ret,
                "ma20_gap_stability": -0.01 * i,
                "close_position_quality": 0.8,
                "avg_amount_20d": 50_000_000,
                "liquidity": 17,
                "rsi14": 50,
            }
        )
    return pd.DataFrame(rows)


def test_cross_sectional_rank_and_missing_filter(config):
    df = _score_frame()
    df.loc[0, "return_120d"] = None
    out = calculate_score(df, config)
    assert pd.isna(out.loc[0, "total_score"])
    top = out.sort_values("total_score", ascending=False).iloc[0]
    assert top["symbol"] == "ETF002"
    assert top["return_20d_score"] == 100


def test_overheat_penalty(config):
    df = _score_frame()
    df.loc[1, "close"] = 12
    df.loc[1, "ema20"] = 10
    df.loc[1, "ma60"] = 9
    df.loc[1, "rsi14"] = 80
    out = calculate_score(df, config)
    assert out.loc[1, "overheat_penalty"] == 60


def test_watchlist_keeps_best_etf_per_theme(config):
    date = pd.Timestamp("2025-01-01")
    df = pd.DataFrame(
        [
            {"date": date, "symbol": "513520", "name": "日经ETF", "asset_class": "OTHER", "total_score": 98.0},
            {"date": date, "symbol": "159866", "name": "日经225ETF", "asset_class": "OTHER", "total_score": 92.0},
            {"date": date, "symbol": "513880", "name": "日本东证ETF", "asset_class": "OTHER", "total_score": 90.0},
            {"date": date, "symbol": "513100", "name": "纳指科技ETF", "asset_class": "US_TECH", "total_score": 96.0},
            {"date": date, "symbol": "159509", "name": "美国科技ETF", "asset_class": "US_TECH", "total_score": 91.0},
            {"date": date, "symbol": "518880", "name": "黄金ETF", "asset_class": "COMMODITY_GOLD", "total_score": 89.0},
        ]
    )

    out = build_watchlist(df, date, config)

    assert out["symbol"].tolist() == ["513520", "513100", "518880"]
