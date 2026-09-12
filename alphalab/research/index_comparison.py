"""Same-period price comparisons for the portfolio review chart.

The portfolio result is frozen under a research run.  Index data therefore
lives in a separate, small cache and is fetched on demand from a public
provider.  This module only uses index *close* values: adjusted closes and
total-return series are deliberately not substituted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import inspect
import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote
import urllib.request


SCHEMA_VERSION = 2
DEFAULT_TIMEOUT = 8.0
WARMUP_DAYS = 10
MAX_STALE_DAYS = 7
SOURCE_NAME = "Yahoo Finance Chart API"
SINA_SOURCE_NAME = "Sina Finance via AKShare"
SOURCE_BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"


@dataclass(frozen=True)
class _IndexSpec:
    id: str
    name: str
    symbol: str
    currency: str
    # A close from these markets is not known at the portfolio's 15:00 China
    # valuation cutoff until the following Shanghai calendar date.
    prior_day_cutoff: bool = False
    sina_function: str = ""
    sina_symbol: str = ""
    sina_source_url: str = ""

    @property
    def source_url(self) -> str:
        return f"{SOURCE_BASE_URL}{quote(self.symbol, safe='')}"


INDEX_SPECS = (
    _IndexSpec(
        "sp500",
        "标普500",
        "^GSPC",
        "USD",
        True,
        "index_us_stock_sina",
        ".INX",
        "https://finance.sina.com.cn/staticdata/us/.INX",
    ),
    _IndexSpec(
        "csi300",
        "沪深300",
        "000300.SS",
        "CNY",
        False,
        "stock_zh_index_daily",
        "sh000300",
        "https://finance.sina.com.cn/realstock/company/sh000300/nc.shtml",
    ),
    _IndexSpec(
        "hsi",
        "恒生指数",
        "^HSI",
        "HKD",
        True,
        "stock_hk_index_daily_sina",
        "HSI",
        "https://finance.sina.com.cn/stock/hkstock/HSI/klc2_kl.js",
    ),
)

_Fetcher = Callable[[str, date, date, float], Iterable[Any]]


@dataclass(frozen=True)
class _FetchedRows:
    rows: Any
    source: str
    source_url: str
    fetched_at: str


def load_index_comparisons(
    cache_dir: str | Path,
    portfolio_dates: Sequence[date | datetime | str] | date | datetime | str | None = None,
    end_date: date | datetime | str | None = None,
    *,
    start_date: date | datetime | str | None = None,
    fetcher: _Fetcher | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Load index closes aligned to portfolio valuation dates.

    ``portfolio_dates`` is normally the ordered daily date list displayed by
    the portfolio result.  At each date, the value is the latest source close
    known by 15:00 Asia/Shanghai: CSI 300 may use the same date, while S&P 500
    and Hang Seng use the latest source date strictly before it.  The first
    displayed date is the unit NAV anchor (1.0), so all returns are price
    returns relative to that observation.

    For compatibility with callers that only have a date range,
    ``load_index_comparisons(cache_dir, start_date, end_date)`` is also
    accepted and creates an inclusive calendar-date list.  The endpoint should
    prefer the portfolio-date form so weekends and non-portfolio days are not
    introduced into the result.

    The optional ``fetcher`` is a transport seam for deterministic tests.  It
    receives ``(symbol, warmup_start, portfolio_end, timeout)`` and returns
    mappings containing ``date`` and ``close``.  The default transport uses
    Sina's AKShare adapters first and Yahoo's chart endpoint as a fallback;
    both paths use an unadjusted index ``close`` field.
    """

    timeout_value = _validate_timeout(timeout)
    dates = _normalise_portfolio_dates(portfolio_dates, end_date=end_date, start_date=start_date)
    if not dates:
        return _empty_result("组合估值日期不能为空")

    first_date, last_date = dates[0], dates[-1]
    warmup_start = first_date - timedelta(days=WARMUP_DAYS)
    cache_path = _cache_path(cache_dir, dates)
    cached = _read_cache(cache_path, dates)
    if cached is not None:
        return cached

    transport = fetcher or _fetch_yahoo_rows
    series: list[dict[str, Any]] = []
    all_ready = True
    for spec in INDEX_SPECS:
        fetched_at = _utc_now_iso()
        source_url = _request_url(spec.symbol, warmup_start, last_date)
        try:
            if fetcher is None:
                fetched = _fetch_index_rows(spec, warmup_start, last_date, timeout_value)
            else:
                raw_rows = _call_fetcher(transport, spec.symbol, warmup_start, last_date, timeout_value)
                fetched = _FetchedRows(raw_rows, SOURCE_NAME, source_url, fetched_at)
            source_url = fetched.source_url
            source_rows = _normalise_source_rows(fetched.rows, warmup_start, last_date)
            if not source_rows:
                raise ValueError("来源未返回有效收盘价")
            rows, ready, error, warnings = _align_rows(spec, dates, source_rows)
            item: dict[str, Any] = {
                "id": spec.id,
                "name": spec.name,
                "currency": spec.currency,
                "source": fetched.source,
                "source_url": source_url,
                "fetched_at": fetched.fetched_at,
                "price_basis": "price_index",
                "status": "ready" if ready else "unavailable",
                "rows": rows,
            }
            if error:
                item["error"] = error
            if warnings:
                item["warnings"] = warnings
            all_ready = all_ready and ready and not warnings and all(row["unit_nav"] is not None for row in rows)
        except Exception as exc:  # Each benchmark is allowed to fail alone.
            item = {
                "id": spec.id,
                "name": spec.name,
                "currency": spec.currency,
                "source": SOURCE_NAME,
                "source_url": source_url,
                "fetched_at": fetched_at,
                "price_basis": "price_index",
                "status": "unavailable",
                "error": _format_provider_error(exc),
                "rows": _empty_rows(dates),
            }
            all_ready = False
        series.append(item)

    result = {
        "schema_version": SCHEMA_VERSION,
        "requested_start_date": first_date.isoformat(),
        "requested_end_date": last_date.isoformat(),
        "portfolio_dates": [item.isoformat() for item in dates],
        "series": series,
    }
    # A transient outage should not be made sticky.  A complete response is
    # safe to reuse and is kept outside the immutable research run directory.
    if all_ready:
        _write_cache(cache_path, result)
    return result


def _normalise_portfolio_dates(
    portfolio_dates: Sequence[date | datetime | str] | date | datetime | str | None,
    *,
    end_date: date | datetime | str | None,
    start_date: date | datetime | str | None,
) -> list[date]:
    if start_date is not None:
        if portfolio_dates is not None or end_date is None:
            raise ValueError("start_date/end_date 需要作为独立日期范围传入")
        begin = _coerce_date(start_date)
        finish = _coerce_date(end_date)
        if begin > finish:
            raise ValueError("开始日期必须早于结束日期")
        return [begin + timedelta(days=index) for index in range((finish - begin).days + 1)]

    if portfolio_dates is None:
        if end_date is not None:
            raise ValueError("缺少开始日期")
        return []
    if end_date is not None:
        # Two positional dates are the backwards-compatible range form.
        begin = _coerce_date(portfolio_dates)
        finish = _coerce_date(end_date)
        if begin > finish:
            raise ValueError("开始日期必须早于结束日期")
        return [begin + timedelta(days=index) for index in range((finish - begin).days + 1)]

    if isinstance(portfolio_dates, (date, datetime, str)):
        return [_coerce_date(portfolio_dates)]
    try:
        values = [_coerce_date(item) for item in portfolio_dates]
    except TypeError as exc:
        raise ValueError("portfolio_dates 必须是日期序列") from exc
    # Keep output deterministic and do not duplicate a displayed date.
    return sorted(set(values))


def _coerce_date(value: date | datetime | str | Any) -> date:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(float(value)):
            raise ValueError("日期无效")
        return datetime.fromtimestamp(float(value), tz=timezone.utc).date()
    text = str(value).strip()
    if not text:
        raise ValueError("日期不能为空")
    # Date-only values are the normal contract.  Accept ISO timestamps for
    # pandas/JSON callers without importing pandas into this small module.
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError(f"日期无效: {value}") from exc


def _validate_timeout(timeout: float) -> float:
    try:
        value = float(timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout 必须是有限正数") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError("timeout 必须是有限正数")
    return value


def _request_url(symbol: str, start: date, end: date) -> str:
    # Yahoo's period2 is exclusive, so add one day solely for an inclusive
    # requested end.  The caller still passes the actual portfolio end to the
    # fetcher, and alignment filters every returned row by its cutoff.
    period1 = int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    period2 = int(datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).timestamp())
    return (
        f"{SOURCE_BASE_URL}{quote(symbol, safe='')}?period1={period1}&period2={period2}"
        "&interval=1d&events=history&includeAdjustedClose=false"
    )


def _fetch_index_rows(spec: _IndexSpec, start: date, end: date, timeout: float) -> _FetchedRows:
    """Fetch one real index from Sina first, then Yahoo as a fallback."""

    failures: list[str] = []
    try:
        rows = _fetch_sina_akshare_rows(spec, timeout)
        return _FetchedRows(rows, SINA_SOURCE_NAME, spec.sina_source_url, _utc_now_iso())
    except Exception as exc:  # Try the independent public source below.
        failures.append(f"{SINA_SOURCE_NAME}: {_format_provider_error(exc)}")
    try:
        rows = _fetch_yahoo_rows(spec.symbol, start, end, timeout)
        return _FetchedRows(rows, SOURCE_NAME, _request_url(spec.symbol, start, end), _utc_now_iso())
    except Exception as exc:
        failures.append(f"{SOURCE_NAME}: {_format_provider_error(exc)}")
    raise RuntimeError("；".join(failures))


_AKSHARE_SUBPROCESS = r'''
import json
import sys

import akshare as ak


function = getattr(ak, sys.argv[1])
frame = function(symbol=sys.argv[2])
records = frame.to_dict(orient="records") if hasattr(frame, "to_dict") else frame
result = []
for row in records or []:
    if not isinstance(row, dict):
        continue
    value_date = row.get("date", row.get("日期"))
    close = row.get("close", row.get("收盘"))
    try:
        close = float(close)
    except (TypeError, ValueError):
        continue
    if value_date is not None and close == close:
        result.append({"date": str(value_date), "close": close})
print(json.dumps(result, ensure_ascii=False, allow_nan=False))
'''


def _fetch_sina_akshare_rows(spec: _IndexSpec, timeout: float) -> Any:
    """Use an isolated AKShare process so provider timeouts are cancellable."""

    try:
        completed = subprocess.run(
            [sys.executable, "-c", _AKSHARE_SUBPROCESS, spec.sina_function, spec.sina_symbol],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"来源请求超过 {timeout:g}s timeout") from exc
    except OSError as exc:
        raise RuntimeError(f"AKShare 子进程无法启动: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "子进程无错误信息").strip()
        raise RuntimeError(f"AKShare 新浪接口失败: {detail[:240]}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("AKShare 新浪接口返回无效 JSON") from exc
    return payload


def _fetch_yahoo_rows(symbol: str, start: date, end: date, timeout: float) -> list[dict[str, Any]]:
    url = _request_url(symbol, start, end)
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "AlphaLab portfolio research/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Yahoo 行情请求失败: {exc}") from exc

    chart = payload.get("chart") if isinstance(payload, Mapping) else None
    if not isinstance(chart, Mapping):
        raise ValueError("Yahoo 响应缺少 chart")
    errors = chart.get("error")
    if errors:
        message = errors.get("description") if isinstance(errors, Mapping) else str(errors)
        raise RuntimeError(f"Yahoo 返回错误: {message}")
    results = chart.get("result")
    if not isinstance(results, Sequence) or not results:
        raise ValueError("Yahoo 响应缺少 result")
    result = results[0]
    if not isinstance(result, Mapping):
        raise ValueError("Yahoo result 无效")
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    quotes = indicators.get("quote") if isinstance(indicators, Mapping) else None
    quote_rows = quotes[0] if isinstance(quotes, Sequence) and quotes else {}
    closes = quote_rows.get("close") if isinstance(quote_rows, Mapping) else None
    if not isinstance(timestamps, Sequence) or not isinstance(closes, Sequence):
        raise ValueError("Yahoo 响应缺少未复权 close")
    return [
        {"date": datetime.fromtimestamp(float(timestamp), tz=timezone.utc).date(), "close": close}
        for timestamp, close in zip(timestamps, closes)
    ]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _call_fetcher(transport: Callable[..., Any], symbol: str, start: date, end: date, timeout: float) -> Any:
    """Call test transports while retaining the documented positional seam."""

    try:
        signature = inspect.signature(transport)
    except (TypeError, ValueError):
        signature = None
    if signature is not None:
        parameters = signature.parameters
        if "timeout" in parameters and parameters["timeout"].kind is not inspect.Parameter.POSITIONAL_ONLY:
            return transport(symbol, start, end, timeout=timeout)
    return transport(symbol, start, end, timeout)


def _normalise_source_rows(rows: Any, start: date, end: date) -> list[tuple[date, float]]:
    if isinstance(rows, Mapping):
        rows = rows.get("rows", rows.get("data", []))
    if rows is None or isinstance(rows, (str, bytes)):
        return []
    normalised: list[tuple[date, float]] = []
    for row in rows:
        if isinstance(row, Mapping):
            value_date = row.get("date", row.get("observed_date", row.get("timestamp")))
            close = row.get("close")
        elif isinstance(row, Sequence) and not isinstance(row, (str, bytes)) and len(row) >= 2:
            value_date, close = row[0], row[1]
        else:
            continue
        try:
            observed = _coerce_date(value_date)
            number = float(close)
        except (TypeError, ValueError, OverflowError):
            continue
        if not (start <= observed <= end) or not math.isfinite(number) or number <= 0:
            continue
        normalised.append((observed, number))
    normalised.sort(key=lambda item: item[0])
    deduped: dict[date, float] = {}
    for observed, close in normalised:
        deduped[observed] = close
    return list(deduped.items())


def _align_rows(
    spec: _IndexSpec,
    portfolio_dates: Sequence[date],
    source_rows: Sequence[tuple[date, float]],
) -> tuple[list[dict[str, Any]], bool, str | None, list[str]]:
    dates = [item[0] for item in source_rows]
    closes = [item[1] for item in source_rows]
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    previous_index = -1
    for portfolio_date in portfolio_dates:
        cutoff = portfolio_date - timedelta(days=1) if spec.prior_day_cutoff else portfolio_date
        while previous_index + 1 < len(dates) and dates[previous_index + 1] <= cutoff:
            previous_index += 1
        if previous_index >= 0:
            observed_date = dates[previous_index]
            close = closes[previous_index]
            age = (cutoff - observed_date).days
            if age > MAX_STALE_DAYS:
                rows.append({"date": portfolio_date.isoformat(), "observed_date": None, "close": None})
                warnings.append(
                    f"{portfolio_date.isoformat()}: latest close {observed_date.isoformat()} "
                    f"is older than {MAX_STALE_DAYS} days"
                )
            else:
                rows.append(
                    {
                        "date": portfolio_date.isoformat(),
                        "observed_date": observed_date.isoformat(),
                        "close": float(close),
                    }
                )
        else:
            rows.append({"date": portfolio_date.isoformat(), "observed_date": None, "close": None})

    # Rebase only when the first displayed date has an observed source close.
    # If it does not, using a later observation would be look-ahead bias.
    anchor = rows[0]["close"]
    if anchor is None or not math.isfinite(float(anchor)) or float(anchor) <= 0:
        for row in rows:
            row.update(unit_nav=None, cumulative_return=None)
        return rows, False, "首个组合估值日缺少可用的收盘锚点", warnings

    base = float(anchor)
    for row in rows:
        close = row["close"]
        if close is None:
            row.update(unit_nav=None, cumulative_return=None)
            continue
        unit_nav = float(close) / base
        row.update(unit_nav=unit_nav, cumulative_return=unit_nav - 1.0)
    return rows, True, None, warnings


def _empty_rows(portfolio_dates: Sequence[date]) -> list[dict[str, Any]]:
    return [
        {"date": item.isoformat(), "observed_date": None, "close": None, "unit_nav": None, "cumulative_return": None}
        for item in portfolio_dates
    ]


def _empty_result(error: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "requested_start_date": None,
        "requested_end_date": None,
        "portfolio_dates": [],
        "series": [
            {
                "id": spec.id,
                "name": spec.name,
                "currency": spec.currency,
                "source": SOURCE_NAME,
                "source_url": spec.source_url,
                "status": "unavailable",
                "error": error,
                "rows": [],
            }
            for spec in INDEX_SPECS
        ],
    }


def _cache_path(cache_dir: str | Path, portfolio_dates: Sequence[date]) -> Path:
    root = Path(cache_dir).expanduser().resolve() / "index_comparisons"
    payload = {
        "schema_version": SCHEMA_VERSION,
        "portfolio_dates": [item.isoformat() for item in portfolio_dates],
        "provider": SOURCE_NAME,
        "symbols": [spec.symbol for spec in INDEX_SPECS],
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:24]
    return root / f"{digest}.json"


def _read_cache(path: Path, portfolio_dates: Sequence[date]) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping) or payload.get("schema_version") != SCHEMA_VERSION:
        return None
    if payload.get("portfolio_dates") != [item.isoformat() for item in portfolio_dates]:
        return None
    if not isinstance(payload.get("series"), list) or len(payload["series"]) != len(INDEX_SPECS):
        return None
    if any(item.get("status") != "ready" or item.get("warnings") or any(row.get("unit_nav") is None for row in item.get("rows", [])) for item in payload["series"]):
        return None
    return dict(payload)


def _write_cache(path: Path, result: Mapping[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        # Cache persistence is an optimization. A read-only/unwritable cache
        # must not make a valid provider response unavailable.
        return


def _format_provider_error(error: Exception) -> str:
    if isinstance(error, HTTPError):
        return f"来源请求失败（HTTP {error.code}）"
    message = str(error).strip() or error.__class__.__name__
    return message[:240]


__all__ = [
    "DEFAULT_TIMEOUT",
    "INDEX_SPECS",
    "SCHEMA_VERSION",
    "SOURCE_NAME",
    "load_index_comparisons",
]
