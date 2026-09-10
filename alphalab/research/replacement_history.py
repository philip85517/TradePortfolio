"""Bounded BaoStock HFQ requests with a separate durable replacement-history cache."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import numpy as np
import pandas as pd

SCHEMA_VERSION = 'baostock-replacement-hfq-v1'
FIELDS = 'date,code,open,high,low,close,volume,amount,adjustflag,tradestatus,pctChg'
BATCH_SIZE = 25
BATCH_TIMEOUT = 120
PRICE_SOURCE = 'baostock.query_history_k_data_plus:adjustflag=1'


def _code(symbol):
    return ('sh.' if symbol.startswith(('5', '6', '9')) else 'sz.') + symbol


def _normalize(raw, request):
    symbol, start, end = request
    data = raw.copy() if isinstance(raw, pd.DataFrame) else pd.DataFrame(raw)
    if data.empty:
        raise ValueError(f'{symbol} {start}..{end}: empty response')
    if not set(FIELDS.split(',')).issubset(data.columns):
        raise ValueError(f'{symbol} {start}..{end}: missing response fields')
    if not data.code.astype(str).eq(_code(symbol)).all():
        raise ValueError(f'{symbol}: response symbol mismatch')
    days = pd.to_datetime(data.date, errors='coerce')
    if days.isna().any() or getattr(days.dt, 'tz', None) is not None or not days.eq(days.dt.normalize()).all() or not days.between(pd.Timestamp(start), pd.Timestamp(end)).all():
        raise ValueError(f'{symbol} {start}..{end}: invalid response date')
    if days.duplicated().any():
        raise ValueError(f'{symbol}: duplicate symbol/date response')
    if not data.adjustflag.astype(str).isin(['1', '1.0', 'hfq']).all():
        raise ValueError(f'{symbol}: response adjustment must be HFQ / flag 1')
    status = pd.to_numeric(data.tradestatus, errors='coerce')
    if not status.isin([0, 1]).all():
        raise ValueError(f'{symbol}: unknown trading status')
    prices = data[['open', 'high', 'low', 'close']].apply(pd.to_numeric, errors='coerce')
    from .price_corrections import apply_verified_price_corrections
    corrected = apply_verified_price_corrections(prices.assign(symbol=symbol, date=days, adjustment='hfq', source='baostock'))
    prices = corrected[['open', 'high', 'low', 'close']]
    invalid = status.eq(1) & (~np.isfinite(prices).all(axis=1) | ~prices.gt(0).all(axis=1))
    if invalid.any():
        raise ValueError(f'{symbol} {days.loc[invalid].iloc[0].date()}: invalid OHLC')
    geometry = status.eq(1) & (prices.high.lt(prices[['open', 'close', 'low']].max(axis=1)) |
                                prices.low.gt(prices[['open', 'close', 'high']].min(axis=1)))
    if geometry.any():
        raise ValueError(f'{symbol} {days.loc[geometry].iloc[0].date()}: OHLC 价格关系不一致')
    ordered = pd.DataFrame({'date': days, 'close': prices.close, 'status': status,
                            'reported': pd.to_numeric(data.pctChg, errors='coerce') / 100}).sort_values('date')
    # Blank confirmed suspensions retain the last finite positive close only for
    # this consistency comparison; the returned source prices remain untouched.
    reference = ordered.close.where(np.isfinite(ordered.close) & ordered.close.gt(0)).ffill()
    implied = ordered.close / reference.shift(1) - 1
    conflict = ordered.status.eq(1) & ordered.reported.notna() & implied.notna() & (implied-ordered.reported).abs().gt(.0005)
    if conflict.any():
        bad = ordered.loc[conflict].iloc[0]
        raise ValueError(f'{symbol} {bad.date.date()}: 复权价格与来源涨跌幅不一致；'
                         f'价格收益 {implied.loc[bad.name]:.6%}，来源涨跌幅 {bad.reported:.6%}')
    volume = pd.to_numeric(data.volume, errors='coerce')
    amount = pd.to_numeric(data.amount, errors='coerce')
    turnover = (np.isfinite(volume) & volume.gt(0)) | (np.isfinite(amount) & amount.gt(0))
    if (status.eq(1) & ~turnover).any():
        raise ValueError(f'{symbol}: trading response requires positive turnover')
    result = corrected.assign(symbol=symbol, date=days, volume=volume, amount=amount,
                           adjustment='hfq', tradestatus=status.astype(int), price_source=PRICE_SOURCE)
    columns = ['symbol', 'date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'adjustment', 'tradestatus', 'price_source', 'source']
    if 'source_correction_id' in result:
        columns.append('source_correction_id')
    return result[columns].sort_values('date').reset_index(drop=True)


def _atomic_json(path, payload):
    fd, temporary = tempfile.mkstemp(prefix=path.name+'.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(payload, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _records(raw):
    frame = raw.copy() if isinstance(raw, pd.DataFrame) else pd.DataFrame(raw)
    # JSON must never persist NaN tokens, including legitimate blank suspension prices.
    if 'date' in frame:
        frame['date'] = frame['date'].astype(str)
    return frame.astype(object).where(pd.notna(frame), None).to_dict('records')


_WORKER = r'''
import json, os, sys
from pathlib import Path
import baostock as bs
requests = json.loads(sys.argv[1])
folder = Path(sys.argv[2])
login = bs.login()
if login.error_code != '0':
    raise RuntimeError('BaoStock login: '+login.error_msg)
try:
    for index, (symbol, start, end) in enumerate(requests):
        code = ('sh.' if symbol.startswith(('5','6','9')) else 'sz.')+symbol
        for attempt in range(2):
            try:
                result = bs.query_history_k_data_plus(code, sys.argv[3], start_date=start, end_date=end, frequency='d', adjustflag='1')
                if result.error_code != '0':
                    raise RuntimeError(result.error_msg)
                rows = []
                while result.next():
                    rows.append(dict(zip(result.fields, result.get_row_data())))
                if result.error_code != '0':
                    raise RuntimeError(result.error_msg)
                payload = {'rows': rows}
                break
            except Exception as exc:
                payload = {'error': str(exc)}
                if attempt == 0 and '用户未登录' in str(exc):
                    login = bs.login()
                    if login.error_code == '0':
                        continue
                    payload = {'error': 'BaoStock重新登录失败: '+login.error_msg}
                break
        target = folder/(str(index)+'.json')
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(payload))
        os.replace(temporary, target)
finally:
    bs.logout()
'''


def _fetch_batch(requests):
    """One isolated login per batch; preserve completed responses on timeout."""
    with tempfile.TemporaryDirectory(prefix='replacement-history-') as folder:
        failure = None
        try:
            result = subprocess.run([sys.executable, '-c', _WORKER, json.dumps(requests), folder, FIELDS],
                                    capture_output=True, text=True, timeout=BATCH_TIMEOUT)
            if result.returncode:
                failure = f'BaoStock batch failed: {result.stderr[-1000:]}'
        except subprocess.TimeoutExpired:
            failure = f'BaoStock batch exceeded {BATCH_TIMEOUT}s'
        responses = {}
        for index, request in enumerate(requests):
            path = Path(folder)/f'{index}.json'
            if path.exists():
                payload = json.loads(path.read_text())
                responses[request[0]] = payload.get('rows', {'error': payload.get('error', 'unknown provider error')})
            else:
                responses[request[0]] = {'error': failure or 'missing batch response'}
        return responses


def _locked_fetch_batch(requests, lock_path):
    # BaoStock anonymous sessions can invalidate each other's login/logout even
    # in isolated interpreters. All callers sharing this cache use one lock.
    with lock_path.open('a+b') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        return _fetch_batch(requests)


class _IncompleteCoverage(ValueError):
    pass


def _check_coverage(frame, symbol, expected_dates):
    required = pd.DatetimeIndex(pd.to_datetime((expected_dates or {}).get(symbol, []))).normalize()
    missing = required.difference(pd.DatetimeIndex(frame.date)).sort_values()
    if len(missing):
        raise _IncompleteCoverage(f'{symbol} {missing[0].date()}: 来源响应缺少必需交易日，共缺 {len(missing)} 日')


def fetch_replacement_history(requests, cache_dir, *, fetch_batch=None, progress=None, expected_dates=None):
    """Fetch (symbol, start, end) requests; reuse each verified successful response.

    ``cache_dir`` is the research cache root; this function writes only its
    ``replacement_history`` subdirectory. ``fetch_batch`` is an optional test
    seam returning {symbol: raw BaoStock records/DataFrame} for one batch.
    Successes are cached before aggregate errors are raised, so retries progress.
    ``expected_dates`` maps symbols to trusted required sessions; incomplete
    responses are saved as .partial.json and never reused as complete results.
    Production logins are serialized across processes sharing this cache root.
    One request per symbol is required to keep the returned mapping unambiguous.
    """
    normalized = []
    for symbol, start, end in requests:
        symbol = str(symbol)
        if not re.fullmatch(r'\d{6}', symbol):
            raise ValueError(f'invalid requested symbol: {symbol}')
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        if pd.isna(start) or pd.isna(end) or start > end:
            raise ValueError(f'{symbol}: invalid requested date interval')
        request = (symbol, str(start.date()), str(end.date()))
        if request not in normalized:
            normalized.append(request)
    if len({r[0] for r in normalized}) != len(normalized):
        raise ValueError('only one requested date interval per symbol is supported')
    directory = Path(cache_dir)/'replacement_history'
    directory.mkdir(parents=True, exist_ok=True)
    paths, missing, results, errors = {}, [], {}, []
    for request in normalized:
        key = dict(schema=SCHEMA_VERSION, symbol=request[0], start=request[1], end=request[2], fields=FIELDS)
        digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()
        path = directory/f'{request[0]}-{digest}.json'
        paths[request] = (path, key)
        if path.exists():
            try:
                payload = json.loads(path.read_text())
                if payload.get('request') != key:
                    raise ValueError('cache request/schema mismatch')
                frame = _normalize(payload['rows'], request)
                _check_coverage(frame, request[0], expected_dates)
                results[request[0]] = frame
                continue
            except _IncompleteCoverage:
                _atomic_json(path.with_suffix('.partial.json'), payload)
            except (ValueError, KeyError, TypeError):
                # An invalid local cache is not data evidence; replace it only
                # after a new valid response has passed the same checks.
                pass
        missing.append(request)
    cached = len(results)
    completed = 0
    def report():
        if progress:
            progress(f'后复权补数：已缓存 {cached}/{len(normalized)}，本轮完成 {completed}/{len(missing)}')
    report()
    batches = [missing[i:i+BATCH_SIZE] for i in range(0, len(missing), BATCH_SIZE)]
    provider = fetch_batch or (lambda batch: _locked_fetch_batch(batch, directory/'.baostock-session.lock'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(provider, batch): batch for batch in batches}
        for future in as_completed(futures):
            batch = futures[future]
            try:
                responses = future.result()
            except Exception as exc:
                errors.extend(f'{r[0]}: {exc}' for r in batch)
                continue
            for request in batch:
                symbol = request[0]
                try:
                    raw = responses.get(symbol)
                    if raw is None or isinstance(raw, dict) and 'error' in raw:
                        raise ValueError(f'{symbol}: {raw.get("error") if isinstance(raw, dict) else "missing batch response"}')
                    frame = _normalize(raw, request)
                    path, key = paths[request]
                    payload = dict(request=key, rows=_records(raw))
                    try:
                        _check_coverage(frame, symbol, expected_dates)
                    except _IncompleteCoverage:
                        _atomic_json(path.with_suffix('.partial.json'), payload)
                        raise
                    _atomic_json(path, payload)
                    path.with_suffix('.partial.json').unlink(missing_ok=True)
                    results[symbol] = frame
                    completed += 1
                except (ValueError, KeyError, TypeError) as exc:
                    errors.append(f'{symbol}: {exc}')
            report()
    if errors:
        raise ValueError('REPLACEMENT_HISTORY_FETCH_FAILED: '+'; '.join(errors[:20]))
    return {request[0]: results[request[0]] for request in normalized}
