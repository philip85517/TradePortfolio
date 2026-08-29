from datetime import date

import pytest

from alphalab.utils import floor_to_lot, money_round, normalize_symbol, trading_days


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("510300", "510300.SH"),
        ("510300.SH", "510300.SH"),
        ("510300.sh", "510300.SH"),
        ("159915", "159915.SZ"),
        ("159915.SZ", "159915.SZ"),
        ("000300.SH", "000300.SH"),
    ],
)
def test_normalize_symbol(raw, expected):
    assert normalize_symbol(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "51030", "ABC123", "510300.XX"],
)
def test_normalize_symbol_invalid(raw):
    with pytest.raises(ValueError):
        normalize_symbol(raw)


@pytest.mark.parametrize(
    "qty,lot,expected",
    [(1234, 100, 1200), (99, 100, 0), (100, 100, 100), (0, 100, 0), (250, 50, 250)],
)
def test_floor_to_lot(qty, lot, expected):
    assert floor_to_lot(qty, lot) == expected


def test_floor_to_lot_invalid():
    with pytest.raises(ValueError):
        floor_to_lot(100, 0)


def test_money_round_half_up():
    assert money_round(1.005, 2) == 1.01  # ROUND_HALF_UP（非银行家舍入）
    assert money_round(1.0049, 2) == 1.0
    assert money_round(0.0) == 0.0


def test_trading_days_skips_weekends():
    days = trading_days("2026-06-05", "2026-06-08")  # 周五 ~ 周一
    assert days == [date(2026, 6, 5), date(2026, 6, 8)]


def test_trading_days_invalid_range():
    with pytest.raises(ValueError):
        trading_days("2026-06-08", "2026-06-05")

