from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd
import yaml

from .market_data_providers import FetchRequest, MarketDataError, provider_for
from .market_data_store import TIMEFRAMES, latest_bar_ts, market_database_summary, upsert_bars


@dataclass(frozen=True)
class Instrument:
    market: str
    symbol: str
    provider: str | None = None
    source_symbol: str | None = None
    options: dict | None = None


def load_market_universe(path: str | Path) -> tuple[list[Instrument], list[str]]:
    with Path(path).open("r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh) or {}
    timeframes = list(config.get("timeframes") or TIMEFRAMES.keys())
    instruments = [
        Instrument(
            market=str(item["market"]),
            symbol=str(item["symbol"]),
            provider=item.get("provider"),
            source_symbol=item.get("source_symbol"),
            options={key: value for key, value in item.items() if key not in {"market", "symbol", "name", "provider", "source_symbol"}},
        )
        for item in config.get("symbols", [])
    ]
    if not instruments:
        raise ValueError(f"No symbols configured in {path}")
    return instruments, timeframes


def build_requests(
    instruments: Iterable[Instrument],
    timeframes: Iterable[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
    db_path: str | Path,
    incremental: bool = False,
) -> list[FetchRequest]:
    requests: list[FetchRequest] = []
    for instrument in instruments:
        for timeframe in timeframes:
            if timeframe not in TIMEFRAMES:
                raise ValueError(f"Unsupported timeframe: {timeframe}")
            request_start = pd.Timestamp(start)
            if incremental:
                latest = latest_bar_ts(db_path, instrument.market, instrument.symbol, timeframe)
                if latest is not None:
                    request_start = latest + TIMEFRAMES[timeframe]
            if request_start > end:
                continue
            requests.append(
                FetchRequest(
                    market=instrument.market,
                    symbol=instrument.symbol,
                    source_symbol=instrument.source_symbol,
                    provider=instrument.provider,
                    timeframe=timeframe,
                    start=request_start,
                    end=pd.Timestamp(end),
                    options=instrument.options or {},
                )
            )
    return requests


def update_market_data(
    requests: Iterable[FetchRequest],
    db_path: str | Path,
    provider_name: str = "auto",
    chunk_days: int | None = None,
    sleep_seconds: float = 0.0,
) -> dict:
    import time

    total_rows = 0
    completed = 0
    failures: list[dict] = []
    for request in requests:
        provider = provider_for(request, provider_name)
        try:
            chunks = _request_chunks(request, chunk_days)
            for chunk in chunks:
                data = provider.fetch_ohlcv(chunk)
                if data.empty:
                    continue
                result = upsert_bars(
                    data,
                    db_path=db_path,
                    source=data["source"].iloc[-1],
                    note=f"provider={provider.name}, chunk={chunk.start.isoformat()}..{chunk.end.isoformat()}",
                )
                total_rows += result["rows"]
                if sleep_seconds:
                    time.sleep(sleep_seconds)
            completed += 1
        except Exception as exc:  # noqa: BLE001 - batch updates should continue across symbols.
            failures.append(
                {
                    "market": request.market,
                    "symbol": request.symbol,
                    "timeframe": request.timeframe,
                    "reason": str(exc),
                }
            )
    summary = market_database_summary(db_path)
    return {
        "requests": completed + len(failures),
        "completed": completed,
        "failures": failures,
        "rows_written": total_rows,
        "db_summary": summary,
    }


def _request_chunks(request: FetchRequest, chunk_days: int | None) -> list[FetchRequest]:
    if not chunk_days or chunk_days <= 0:
        return [request]
    chunks = []
    start = request.start
    while start <= request.end:
        end = min(start + timedelta(days=chunk_days) - timedelta(seconds=1), request.end)
        chunks.append(
            FetchRequest(
                market=request.market,
                symbol=request.symbol,
                timeframe=request.timeframe,
                start=start,
                end=end,
                source_symbol=request.source_symbol,
                provider=request.provider,
                options=request.options,
            )
        )
        start = end + timedelta(seconds=1)
    return chunks


def parse_instrument_filters(values: list[str] | None) -> set[tuple[str, str]] | None:
    if not values:
        return None
    parsed = set()
    for value in values:
        if ":" not in value:
            raise MarketDataError("Instrument filters must use market:symbol, for example us:AAPL")
        market, symbol = value.split(":", 1)
        parsed.add((market, symbol))
    return parsed


def filter_instruments(
    instruments: Iterable[Instrument],
    markets: list[str] | None = None,
    symbols: list[str] | None = None,
) -> list[Instrument]:
    market_filter = set(markets or [])
    instrument_filter = parse_instrument_filters(symbols)
    output = []
    for instrument in instruments:
        if market_filter and instrument.market not in market_filter:
            continue
        if instrument_filter and (instrument.market, instrument.symbol) not in instrument_filter:
            continue
        output.append(instrument)
    return output
