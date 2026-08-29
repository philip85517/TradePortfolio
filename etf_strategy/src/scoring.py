from __future__ import annotations

import pandas as pd

from .universe import deduplicate_by_correlation, deduplicate_by_theme

KEY_INDICATORS = [
    "return_20d",
    "return_60d",
    "return_120d",
    "atr20_pct",
    "effective_move_20d",
    "ema20",
    "ma60",
    "avg_amount_20d",
]


def calculate_score(indicator_df: pd.DataFrame, config: dict) -> pd.DataFrame:
    scoring_cfg = config.get("scoring", {})
    weights = scoring_cfg.get("weights", {})
    overheat = scoring_cfg.get("overheat", {})
    out = indicator_df.copy()
    out["valid_score_data"] = out[KEY_INDICATORS].notna().all(axis=1)

    for factor in weights:
        source = "liquidity" if factor == "liquidity" else factor
        out[f"{factor}_score"] = out.groupby("date")[source].rank(pct=True) * 100

    out["overheat_penalty"] = 0.0
    out.loc[out["close"] / out["ema20"] - 1 > overheat.get("ma20_gap_penalty_threshold", 0.12), "overheat_penalty"] += overheat.get("penalty_score", 20)
    out.loc[out["close"] / out["ma60"] - 1 > overheat.get("ma60_gap_penalty_threshold", 0.25), "overheat_penalty"] += overheat.get("penalty_score", 20)
    out.loc[out["rsi14"] > overheat.get("rsi_penalty_threshold", 75), "overheat_penalty"] += overheat.get("penalty_score", 20)

    total = pd.Series(0.0, index=out.index)
    for factor, weight in weights.items():
        total += out[f"{factor}_score"].fillna(0) * weight
    out["total_score"] = (total - out["overheat_penalty"]).where(out["valid_score_data"])
    out["daily_rank"] = out.groupby("date")["total_score"].rank(ascending=False, method="min")
    return out


def build_watchlist(
    scored_df: pd.DataFrame,
    date,
    config: dict,
    returns_matrix: pd.DataFrame | None = None,
) -> pd.DataFrame:
    candidate_cfg = config.get("candidate", {})
    date = pd.to_datetime(date)
    daily = scored_df[pd.to_datetime(scored_df["date"]) == date].dropna(subset=["total_score"])
    daily = daily.sort_values("total_score", ascending=False)
    if returns_matrix is not None:
        daily = deduplicate_by_correlation(
            daily,
            returns_matrix,
            threshold=candidate_cfg.get("correlation_threshold", 0.8),
            max_per_asset_class=candidate_cfg.get("max_per_asset_class", 2),
            max_per_theme=candidate_cfg.get("max_per_theme", 1),
        )
    else:
        daily = deduplicate_by_theme(
            daily,
            max_per_theme=candidate_cfg.get("max_per_theme", 1),
            max_per_asset_class=candidate_cfg.get("max_per_asset_class", 2),
        )

    top_n = candidate_cfg.get("top_n_watchlist", 10)
    return daily.head(top_n).assign(watch_rank=lambda x: range(1, len(x) + 1)).reset_index(drop=True)


def trailing_returns_matrix(indicator_df: pd.DataFrame, date, lookback: int = 60) -> pd.DataFrame:
    hist = indicator_df[pd.to_datetime(indicator_df["date"]) <= pd.to_datetime(date)]
    prices = hist.pivot(index="date", columns="symbol", values="close").sort_index().tail(lookback + 1)
    return prices.pct_change().dropna(how="all")
