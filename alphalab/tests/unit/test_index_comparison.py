from __future__ import annotations

from datetime import date
import json
import subprocess

import pytest

from alphalab.research.index_comparison import load_index_comparisons
import alphalab.research.index_comparison as index_comparison


PORTFOLIO_DATES = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]


def _source_rows(symbol: str) -> list[dict[str, object]]:
    if symbol == "^GSPC":
        return [
            {"date": "2025-01-01", "close": 100},
            {"date": "2025-01-02", "close": 110},
            {"date": "2025-01-03", "close": 120},
            # This is the US session closing after the first 2025-01-06
            # portfolio valuation and must not be used for that date.
            {"date": "2025-01-06", "close": 999},
        ]
    if symbol == "000300.SS":
        return [
            {"date": "2025-01-01", "close": 200},
            {"date": "2025-01-02", "close": 210},
            {"date": "2025-01-03", "close": 220},
            {"date": "2025-01-06", "close": 230},
        ]
    return [
        {"date": "2025-01-01", "close": 300},
        {"date": "2025-01-02", "close": 310},
        {"date": "2025-01-03", "close": 320},
        # The 2025-01-06 HSI close is after the 15:00 Asia/Shanghai cutoff.
        {"date": "2025-01-06", "close": 999},
    ]


def test_comparisons_use_exchange_cutoff_and_rebase_on_first_portfolio_date(tmp_path) -> None:
    calls: list[tuple[str, date, date, float]] = []

    def fetcher(symbol: str, start: date, end: date, timeout: float):
        calls.append((symbol, start, end, timeout))
        return _source_rows(symbol)

    result = load_index_comparisons(
        tmp_path,
        PORTFOLIO_DATES,
        fetcher=fetcher,
        timeout=3.5,
    )

    assert [item["id"] for item in result["series"]] == ["sp500", "csi300", "hsi"]
    assert all(item["status"] == "ready" for item in result["series"])
    assert all(item["currency"] in {"USD", "CNY", "HKD"} for item in result["series"])
    assert {item[1] for item in calls} == {date(2024, 12, 23)}
    assert {item[2] for item in calls} == {date(2025, 1, 6)}
    assert {item[3] for item in calls} == {3.5}

    sp500, csi300, hsi = result["series"]
    assert [row["observed_date"] for row in sp500["rows"]] == ["2025-01-01", "2025-01-02", "2025-01-03"]
    assert [row["close"] for row in sp500["rows"]] == [100.0, 110.0, 120.0]
    assert [row["unit_nav"] for row in sp500["rows"]] == pytest.approx([1.0, 1.1, 1.2])
    assert [row["cumulative_return"] for row in sp500["rows"]] == pytest.approx([0.0, 0.1, 0.2])

    # CSI 300 closes at 15:00 China time, so its same-day close is usable.
    assert [row["observed_date"] for row in csi300["rows"]] == ["2025-01-02", "2025-01-03", "2025-01-06"]
    assert [row["unit_nav"] for row in csi300["rows"]] == pytest.approx([1.0, 220 / 210, 230 / 210])

    # HSI closes at 16:00 Hong Kong time, so it follows the prior session just
    # like the US index at the 15:00 Asia/Shanghai portfolio cutoff.
    assert [row["observed_date"] for row in hsi["rows"]] == ["2025-01-01", "2025-01-02", "2025-01-03"]
    assert [row["close"] for row in hsi["rows"]] == [300.0, 310.0, 320.0]
    assert [row["unit_nav"] for row in hsi["rows"]] == pytest.approx([1.0, 310 / 300, 320 / 300])


def test_missing_source_is_isolated_and_later_rows_do_not_look_forward(tmp_path) -> None:
    def fetcher(symbol: str, start: date, end: date, timeout: float):
        if symbol == "000300.SS":
            raise TimeoutError("CSI provider timed out")
        if symbol == "^GSPC":
            return [
                {"date": "2025-01-01", "close": 100},
                {"date": "2025-01-03", "close": 120},
                {"date": "2025-01-06", "close": 999},
            ]
        return [
            # No prior anchor exists. The first displayed date must remain
            # unavailable until an observed close arrives; a future close may
            # not be used to fill it.
            {"date": "2025-01-06", "close": 999},
        ]

    result = load_index_comparisons(tmp_path, PORTFOLIO_DATES, fetcher=fetcher)
    sp500, csi300, hsi = result["series"]

    assert sp500["status"] == "ready"
    assert [row["observed_date"] for row in sp500["rows"]] == ["2025-01-01", "2025-01-01", "2025-01-03"]
    assert [row["close"] for row in sp500["rows"]] == [100.0, 100.0, 120.0]
    assert sp500["rows"][2]["close"] != 999.0
    assert csi300["status"] == "unavailable"
    assert "timed out" in csi300["error"]
    assert hsi["status"] == "unavailable"
    assert all(row["unit_nav"] is None for row in hsi["rows"])


def test_cache_is_reused_and_contains_source_metadata(tmp_path) -> None:
    calls: list[str] = []

    def fetcher(symbol: str, start: date, end: date, timeout: float):
        calls.append(symbol)
        return [{"date": "2025-01-01", "close": 100}]

    first = load_index_comparisons(tmp_path, [date(2025, 1, 2)], fetcher=fetcher)
    assert len(calls) == 3
    assert list(tmp_path.iterdir())
    assert all(item["source"] == "Yahoo Finance Chart API" for item in first["series"])
    assert all(item["source_url"].startswith("https://query1.finance.yahoo.com/v8/finance/chart/") for item in first["series"])

    # A later invocation can serve the same requested range without touching
    # the provider again. The JSON cache is outside any immutable run files.
    second = load_index_comparisons(tmp_path, [date(2025, 1, 2)], fetcher=lambda *args: pytest.fail("cache miss"))
    assert second == first
    cache_files = list(tmp_path.rglob("*.json"))
    assert cache_files
    assert all("series" in json.loads(path.read_text(encoding="utf-8")) for path in cache_files)


def test_sina_akshare_provider_is_used_when_yahoo_is_unavailable(tmp_path, monkeypatch) -> None:
    source_rows = {
        "sp500": [{"date": "2025-01-01", "close": 100}],
        "csi300": [{"date": "2025-01-01", "close": 200}],
        "hsi": [{"date": "2025-01-01", "close": 300}],
    }

    def sina_rows(spec, timeout):
        return source_rows[spec.id]

    monkeypatch.setattr(index_comparison, "_fetch_sina_akshare_rows", sina_rows)

    def yahoo_unavailable(*args, **kwargs):
        raise RuntimeError("Yahoo HTTP 429")

    monkeypatch.setattr(index_comparison, "_fetch_yahoo_rows", yahoo_unavailable)
    result = load_index_comparisons(tmp_path, [date(2025, 1, 2)])

    assert all(item["status"] == "ready" for item in result["series"])
    assert all(item["source"] == "Sina Finance via AKShare" for item in result["series"])
    assert all(item["price_basis"] == "price_index" for item in result["series"])
    assert all(item["fetched_at"].endswith("Z") for item in result["series"])
    assert result["series"][0]["source_url"] == "https://finance.sina.com.cn/staticdata/us/.INX"
    assert result["series"][1]["source_url"].endswith("/sh000300/nc.shtml")
    assert result["series"][2]["source_url"].endswith("/HSI/klc2_kl.js")


def test_stale_source_close_becomes_gap_instead_of_flat_forward_fill(tmp_path) -> None:
    def fetcher(symbol: str, start: date, end: date, timeout: float):
        return [
            {"date": "2025-01-01", "close": 100},
            {"date": "2025-01-20", "close": 120},
        ]

    dates = [date(2025, 1, 2), date(2025, 1, 10), date(2025, 1, 20)]
    result = load_index_comparisons(tmp_path, dates, fetcher=fetcher)
    csi300 = next(item for item in result["series"] if item["id"] == "csi300")

    assert csi300["status"] == "ready"
    assert csi300["rows"][0]["unit_nav"] == pytest.approx(1.0)
    assert csi300["rows"][1]["close"] is None
    assert csi300["rows"][1]["unit_nav"] is None
    assert csi300["rows"][2]["close"] == 120.0
    assert csi300["rows"][2]["unit_nav"] == pytest.approx(1.2)
    assert csi300["warnings"]


def test_sina_subprocess_timeout_is_reported_as_bounded_failure(monkeypatch) -> None:
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=0.01)

    monkeypatch.setattr(index_comparison.subprocess, "run", timeout)
    with pytest.raises(TimeoutError, match="timeout"):
        index_comparison._fetch_sina_akshare_rows(index_comparison.INDEX_SPECS[0], 0.01)


def test_gapped_provider_history_is_not_cached_as_complete(tmp_path):
    from alphalab.research.index_comparison import load_index_comparisons
    calls = []
    def truncated(symbol, start, end, timeout):
        return [{'date': '2025-01-01', 'close': 100}]
    first = load_index_comparisons(tmp_path, ['2025-01-02', '2025-01-20'], fetcher=truncated)
    assert all(item['rows'][-1]['unit_nav'] is None for item in first['series'])
    def recovered(symbol, start, end, timeout):
        calls.append(symbol)
        return [{'date': '2025-01-01', 'close': 100}, {'date': '2025-01-19', 'close': 110}]
    second = load_index_comparisons(tmp_path, ['2025-01-02', '2025-01-20'], fetcher=recovered)
    assert len(calls) == 3
    assert all(item['rows'][-1]['unit_nav'] == 1.1 for item in second['series'])
