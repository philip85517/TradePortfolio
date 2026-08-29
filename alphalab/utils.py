"""通用工具：符号归一化、整手、金额精度、日期、哈希。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Sequence


def normalize_symbol(symbol: str) -> str:
    """将 ETF 代码归一化为 ``510300.SH`` / ``159915.SZ`` 形式。

    规则：带交易所后缀的原样保留；纯数字按首码推断（5 开头→SH，其余→SZ）。
    """
    raw = str(symbol).strip().upper()
    if not raw:
        raise ValueError("symbol 不能为空")
    if "." in raw:
        code, exchange = raw.split(".", 1)
        exchange = exchange.upper()
        if exchange not in {"SH", "SZ"}:
            raise ValueError(f"未知交易所后缀: {raw}")
        return f"{code}.{exchange}"
    if not re.fullmatch(r"\d{6}", raw):
        raise ValueError(f"ETF 代码格式无效: {symbol}")
    exchange = "SH" if raw.startswith("5") else "SZ"
    return f"{raw}.{exchange}"


def symbol_code(symbol: str) -> str:
    return normalize_symbol(symbol).split(".")[0]


def floor_to_lot(quantity: float, lot_size: int = 100) -> int:
    """向下取整至整手。"""
    lot_size = int(lot_size)
    if lot_size <= 0:
        raise ValueError("lot_size 必须为正数")
    if quantity is None or quantity <= 0:
        return 0
    return int(quantity // lot_size) * lot_size


def money_round(value: float, digits: int = 2) -> float:
    """金额按四舍五入（ROUND_HALF_UP）保留指定位数。"""
    if value is None:
        return 0.0
    q = Decimal(str(value)).quantize(Decimal("1." + "0" * digits), rounding=ROUND_HALF_UP)
    return float(q)


def parse_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


def parse_dates(values: Sequence[str | date]) -> list[date]:
    return [parse_date(v) for v in values]


def trading_days(start: str | date, end: str | date) -> list[date]:
    """交易日序列：当前按工作日近似，节假日列表可在配置中扩展。

    说明：A 股休市日（法定节假日/调休）暂以工作日近似，属于已知简化，
    后续接入交易日历后替换此函数即可。
    """
    s, e = parse_date(start), parse_date(end)
    if e < s:
        raise ValueError(f"end 必须不早于 start: {s} > {e}")
    days: list[date] = []
    d = s
    while d <= e:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def next_trading_day(value: str | date) -> date:
    d = parse_date(value) + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def canonical_json(obj) -> str:
    """配置/快照的规范 JSON 字符串（键排序），用于哈希。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def json_hash(obj) -> str:
    """sha256 十六进制摘要。"""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def code_commit() -> str:
    """当前代码版本：优先 git HEAD，否则取包版本。"""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if out.returncode == 0:
            return out.stdout.strip()[:12]
    except Exception:
        pass
    try:
        from alphalab import __version__

        return f"pkg-{__version__}"
    except Exception:
        return "unknown"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def iter_unique(seq: Iterable[str]) -> list[str]:
    """去重但保持顺序。"""
    seen: set[str] = set()
    out: list[str] = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out

