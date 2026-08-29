from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .market_data_providers import AkshareProvider, FetchRequest
from .market_data_store import TIMEFRAMES, connect_market_db
from .market_data_updater import Instrument


EQUITY_MARKETS = {"a_share", "hk", "us"}


@dataclass(frozen=True)
class ValidationWindow:
    start: pd.Timestamp
    end: pd.Timestamp


def validate_database(
    db_path: str | Path,
    instruments: list[Instrument],
    timeframes: list[str],
    window: ValidationWindow,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    expected = pd.DataFrame(
        [
            {"market": instrument.market, "symbol": instrument.symbol, "timeframe": timeframe}
            for instrument in instruments
            for timeframe in timeframes
        ]
    )
    with connect_market_db(db_path) as con:
        coverage = con.execute(
            """
            SELECT market, symbol, timeframe,
                   count(*) AS rows,
                   min(ts) AS start_ts,
                   max(ts) AS end_ts,
                   string_agg(DISTINCT source, ', ' ORDER BY source) AS sources,
                   string_agg(DISTINCT adjustment, ', ' ORDER BY adjustment) AS adjustments,
                   sum(CASE WHEN open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL THEN 1 ELSE 0 END) AS null_ohlc_rows,
                   sum(CASE WHEN close <= 0 OR open <= 0 OR high <= 0 OR low <= 0 THEN 1 ELSE 0 END) AS non_positive_price_rows,
                   sum(CASE WHEN high < low OR high < open OR high < close OR low > open OR low > close THEN 1 ELSE 0 END) AS invalid_ohlc_rows,
                   sum(CASE WHEN volume < 0 THEN 1 ELSE 0 END) AS negative_volume_rows
            FROM market_ohlcv
            GROUP BY market, symbol, timeframe
            """
        ).fetch_df()

    report = expected.merge(coverage, how="left", on=["market", "symbol", "timeframe"])
    if report.empty:
        return report, pd.DataFrame()

    report["rows"] = report["rows"].fillna(0).astype(int)
    for column in ["null_ohlc_rows", "non_positive_price_rows", "invalid_ohlc_rows", "negative_volume_rows"]:
        report[column] = report[column].fillna(0).astype(int)
    report["start_ts"] = pd.to_datetime(report["start_ts"])
    report["end_ts"] = pd.to_datetime(report["end_ts"])
    report["expected_adjustment"] = report["market"].map(lambda market: "qfq" if market in EQUITY_MARKETS else "none")
    report["adjustment_ok"] = report.apply(
        lambda row: bool(row["rows"]) and row["expected_adjustment"] in str(row.get("adjustments") or ""),
        axis=1,
    )
    report["coverage_ok"] = report.apply(lambda row: _coverage_ok(row, window), axis=1)
    report["price_quality_ok"] = (
        (report["rows"] > 0)
        & (report["null_ohlc_rows"] == 0)
        & (report["non_positive_price_rows"] == 0)
        & (report["invalid_ohlc_rows"] == 0)
        & (report["negative_volume_rows"] == 0)
    )
    report["status"] = report.apply(_status, axis=1)

    problems = report[report["status"] != "ok"].copy()
    return report.sort_values(["market", "symbol", "timeframe"]).reset_index(drop=True), problems.reset_index(drop=True)


def verify_qfq_against_unadjusted(
    instruments: list[Instrument],
    window: ValidationWindow,
) -> pd.DataFrame:
    rows = []
    provider = AkshareProvider()
    for instrument in instruments:
        if instrument.market not in EQUITY_MARKETS:
            continue
        options = dict(instrument.options or {})
        try:
            qfq = provider.fetch_ohlcv(
                FetchRequest(
                    market=instrument.market,
                    symbol=instrument.symbol,
                    source_symbol=instrument.source_symbol,
                    timeframe="1d",
                    start=window.start,
                    end=window.end,
                    options={**options, "adjust": "qfq"},
                )
            )
            raw = provider.fetch_ohlcv(
                FetchRequest(
                    market=instrument.market,
                    symbol=instrument.symbol,
                    source_symbol=instrument.source_symbol,
                    timeframe="1d",
                    start=window.start,
                    end=window.end,
                    options={**options, "adjust": "none"},
                )
            )
            merged = qfq[["ts", "close"]].rename(columns={"close": "qfq_close"}).merge(
                raw[["ts", "close"]].rename(columns={"close": "raw_close"}),
                on="ts",
                how="inner",
            )
            if merged.empty:
                status = "failed_empty_comparison"
                max_abs_diff = pd.NA
                compared_rows = 0
            else:
                max_abs_diff = float((merged["qfq_close"] - merged["raw_close"]).abs().max())
                compared_rows = int(len(merged))
                status = "verified_qfq_differs_from_raw" if max_abs_diff > 1e-8 else "same_as_raw_no_detected_adjustment"
            rows.append(
                {
                    "market": instrument.market,
                    "symbol": instrument.symbol,
                    "compared_rows": compared_rows,
                    "max_abs_close_diff": max_abs_diff,
                    "status": status,
                }
            )
        except Exception as exc:  # noqa: BLE001 - validation report should include source failures.
            rows.append(
                {
                    "market": instrument.market,
                    "symbol": instrument.symbol,
                    "compared_rows": 0,
                    "max_abs_close_diff": pd.NA,
                    "status": f"failed: {exc}",
                }
            )
    return pd.DataFrame(rows)


def write_validation_report(
    path: str | Path,
    coverage: pd.DataFrame,
    qfq_checks: pd.DataFrame,
    window: ValidationWindow,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok_count = int((coverage["status"] == "ok").sum()) if not coverage.empty else 0
    lines = [
        "# Market Data Validation Report",
        "",
        f"- Expected window: {window.start.date()} to {window.end.date()}",
        f"- Coverage rows checked: {len(coverage)}",
        f"- OK rows: {ok_count}",
        "",
        "## Coverage And Quality",
        "",
        _to_markdown(coverage),
        "",
        "## QFQ Verification",
        "",
        _to_markdown(qfq_checks),
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _coverage_ok(row: pd.Series, window: ValidationWindow) -> bool:
    if not row["rows"] or pd.isna(row["start_ts"]) or pd.isna(row["end_ts"]):
        return False
    tolerance = TIMEFRAMES[str(row["timeframe"])]
    return row["start_ts"] <= window.start + tolerance and row["end_ts"] >= window.end - tolerance


def _status(row: pd.Series) -> str:
    if row["rows"] == 0:
        return "missing"
    if not row["coverage_ok"]:
        return "coverage_gap"
    if not row["adjustment_ok"]:
        return "adjustment_mismatch"
    if not row["price_quality_ok"]:
        return "price_quality_error"
    return "ok"


def _to_markdown(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"
    return df.to_markdown(index=False)
