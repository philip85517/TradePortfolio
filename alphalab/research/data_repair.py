"""Validated cache publication and durable evidence for explicit repair attempts."""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import time
from datetime import datetime, timezone
from uuid import uuid4

import pandas as pd

from etf_strategy.src.market_data_store import load_bars, upsert_bars


def _checkpoint(cancelled):
    if cancelled():
        raise InterruptedError('已取消；已验证数据保留')


def _sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def validate_and_publish(frame, target_path, symbol, start, end, *, cancelled=lambda: False):
    """Publish a complete validated hfq daily response; return upsert statistics.

    Volume/amount gaps remain diagnostic evidence, not a reason to discard bars.
    The store's DELETE/INSERT are not transactional. Perform them on a sibling
    database and atomically replace the cache only once the entire write succeeds.
    Cooperative writers serialize with a file lock to retain previous shards.
    """
    _checkpoint(cancelled)
    if frame is None or frame.empty:
        raise ValueError('来源未返回行情')
    data = frame.copy(deep=True)
    required = {'market', 'symbol', 'timeframe', 'ts', 'open', 'high', 'low', 'close', 'adjustment'}
    if required - set(data.columns):
        raise ValueError(f'行情缺少字段：{sorted(required - set(data.columns))}')
    if not data['market'].eq('a_share').fillna(False).all() or not data['symbol'].eq(symbol).fillna(False).all():
        raise ValueError('行情标的与请求不一致')
    if not data['timeframe'].eq('1d').fillna(False).all():
        raise ValueError('来源未返回日线行情')
    if not data['adjustment'].eq('hfq').fillna(False).all():
        raise ValueError('行情必须使用一致的后复权口径 hfq')
    # Legacy BaoStock marks only qfq as adjusted; the explicit hfq metadata
    # above is authoritative for this response, so normalize its boolean flag.
    data['adjusted'] = True
    dates = pd.to_datetime(data['ts'], errors='coerce')
    if dates.isna().any():
        raise ValueError('行情日期无效')
    dates = dates.dt.tz_localize(None)
    days = dates.dt.normalize()
    if days.duplicated().any():
        raise ValueError('来源返回重复交易日')
    if not days.between(pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()).all():
        raise ValueError('行情日期超出请求范围')
    data['ts'] = dates
    for name in ['open', 'high', 'low', 'close']:
        data[name] = pd.to_numeric(data[name], errors='coerce')
        if not data[name].map(lambda value: pd.notna(value) and math.isfinite(value) and value > 0).all():
            raise ValueError(f'行情 {name} 必须为有限正数')
    if ((data['high'] < data[['open', 'close', 'low']].max(axis=1)) |
            (data['low'] > data[['open', 'close', 'high']].min(axis=1))).any():
        raise ValueError('行情 OHLC 价格关系不一致')
    if 'volume' not in data:
        data['volume'] = float('nan')
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.with_suffix(target.suffix + '.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _checkpoint(cancelled)
        with tempfile.TemporaryDirectory(prefix='.repair-', dir=target.parent) as directory:
            staging = Path(directory) / target.name
            if target.exists():
                # A WAL means another non-cooperating writer has not checkpointed.
                # Never copy an incomplete database snapshot.
                if Path(str(target) + '.wal').exists():
                    raise OSError('缓存仍有未提交的数据库日志，请稍后重试')
                shutil.copy2(target, staging)
                existing = load_bars(staging, markets=['a_share'], symbols=[symbol],
                                     timeframes=['1d'], start_ts=days.min(),
                                     end_ts=days.max() + pd.Timedelta(days=1))
                # Quantities are unadjusted facts. A response with a missing
                # quantity must not erase a known value on the same trading day.
                # Finite new values, including authentic zero, remain authoritative.
                for field in ['volume', 'amount']:
                    incoming = pd.to_numeric(data.get(field, pd.Series(float('nan'), index=data.index)),
                                             errors='coerce')
                    valid_old = pd.to_numeric(existing[field], errors='coerce')
                    finite_old = valid_old.map(lambda value: pd.notna(value) and math.isfinite(value))
                    previous = pd.Series(valid_old[finite_old].to_numpy(),
                                         index=existing.loc[finite_old, 'ts'].dt.normalize())
                    previous = previous.groupby(level=0).last()
                    finite_new = incoming.map(lambda value: pd.notna(value) and math.isfinite(value))
                    data[field] = incoming.where(finite_new, days.map(previous))
            result = upsert_bars(data, staging)
            with staging.open('rb') as saved:
                os.fsync(saved.fileno())
            _checkpoint(cancelled)
            os.replace(staging, target)
            _sync_directory(target.parent)
    return {**result, 'db_path': str(target)}


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def save_attempt(cache_dir, record):
    """Append an immutable, fsynced attempt JSON; return its unique Path.

    Preserve supplied evidence fingerprints; otherwise hash query/result payloads.
    Each explicit attempt receives a new identity, even for identical content.
    """
    directory = Path(cache_dir) / 'repair_attempts'
    directory.mkdir(parents=True, exist_ok=True)
    payload = dict(record)
    payload['attempt_id'] = uuid4().hex
    payload.setdefault('recorded_at', datetime.now(timezone.utc).isoformat())
    payload.setdefault('query_fingerprint', hashlib.sha256(_encoded(payload.get('query', {}))).hexdigest())
    payload.setdefault('result_fingerprint', hashlib.sha256(_encoded(payload.get('result', {}))).hexdigest())
    encoded = _encoded(payload)
    target = directory / f"{payload['attempt_id']}.json"
    fd, name = tempfile.mkstemp(prefix='.attempt-', dir=directory)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        # Hard-link publication is atomic and refuses to overwrite existing files.
        os.link(temporary, target)
        _sync_directory(directory)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _transient(error):
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, (TimeoutError, ConnectionError)):
            return True
        # HTTP libraries and subprocess providers wrap transport errors differently.
        description = f'{type(error).__name__}: {error}'.lower()
        if any(token in description for token in (
            'connecttimeout', 'readtimeout', 'timeouterror', 'connectionerror', 'timed out',
            'connection refused', 'connection reset', 'connection aborted',
            'connection failed', 'failed to establish a new connection',
            'network is unreachable', 'temporary failure in name resolution',
            'network timeout', 'request timeout', 'remote disconnected')):
            return True
        error = error.__cause__ or error.__context__
    return False


def retry_transient(operation, cancelled, progress, max_attempts=3):
    """Retry transport failures only; cancellation interrupts short backoff waits."""
    if not isinstance(max_attempts, int) or not 1 <= max_attempts <= 3:
        raise ValueError('max_attempts must be between 1 and 3')
    for attempt in range(1, max_attempts + 1):
        _checkpoint(cancelled)
        try:
            return operation()
        except Exception as error:
            if isinstance(error, InterruptedError) or not _transient(error) or attempt == max_attempts:
                raise
            progress(f'来源连接暂时失败，正在重试（{attempt + 1}/{max_attempts}）')
            deadline = time.monotonic() + 0.1 * attempt
            while time.monotonic() < deadline:
                _checkpoint(cancelled)
                time.sleep(min(0.025, max(0, deadline - time.monotonic())))
    raise AssertionError('unreachable')
