"""Publication must retain good cache data when source or storage fails."""
import json

import pandas as pd
import pytest

from alphalab.research import data_repair
from etf_strategy.src.market_data_store import load_bars


def bars():
    return pd.DataFrame([dict(market='a_share', symbol='600000.SH', timeframe='1d',
        ts='2026-08-03', open=10., high=12., low=9., close=11., volume=100.,
        adjustment='hfq', adjusted=True)])


def publish(frame, path):
    return data_repair.validate_and_publish(frame, path, '600000.SH', '2026-08-01', '2026-08-10')


@pytest.mark.parametrize('column,value', [('symbol', 'wrong'), ('timeframe', '1h'),
    ('ts', '2026-09-01'), ('ts', None), ('open', float('inf')), ('close', 0),
    ('high', 8), ('low', 12), ('adjustment', 'qfq'), ('adjustment', None)])
def test_invalid_response_preserves_existing_cache(tmp_path, column, value):
    path = tmp_path / 'bars.duckdb'
    publish(bars(), path)
    invalid = bars()
    invalid[column] = value
    with pytest.raises(ValueError):
        publish(invalid, path)
    assert load_bars(path)['close'].tolist() == [11.]


def test_empty_and_duplicate_days_rejected(tmp_path):
    for frame in [bars().iloc[:0], pd.concat([bars(), bars().assign(ts='2026-08-03 15:00')])]:
        with pytest.raises(ValueError):
            publish(frame, tmp_path / 'bars.duckdb')
    assert not (tmp_path / 'bars.duckdb').exists()


@pytest.mark.parametrize('volume', [0, -1, None])
def test_volume_alone_does_not_prevent_publishing(tmp_path, volume):
    path = tmp_path / 'bars.duckdb'
    result = publish(bars().assign(volume=volume), path)
    assert result['rows'] == 1
    assert load_bars(path)['close'].tolist() == [11.]


def test_missing_volume_column_is_preserved_as_unknown(tmp_path):
    path = tmp_path / 'bars.duckdb'
    publish(bars().drop(columns='volume'), path)
    assert load_bars(path)['volume'].isna().all()


def test_failed_storage_write_does_not_replace_cache(tmp_path, monkeypatch):
    path = tmp_path / 'bars.duckdb'
    publish(bars(), path)
    def fail(frame, target):
        target.write_bytes(b'partial broken database')
        raise OSError('disk failed')
    monkeypatch.setattr(data_repair, 'upsert_bars', fail)
    with pytest.raises(OSError):
        publish(bars().assign(close=10.5), path)
    assert load_bars(path)['close'].tolist() == [11.]


def test_attempts_are_distinct_durable_records_with_fingerprints(tmp_path):
    record = {'query': {'symbol': '600000.SH'}, 'result': {'rows': 0}, 'outcome': 'unresolved'}
    first = data_repair.save_attempt(tmp_path, record)
    second = data_repair.save_attempt(tmp_path, record)
    assert first != second
    one, two = json.loads(first.read_text()), json.loads(second.read_text())
    assert one['query_fingerprint'] == two['query_fingerprint']
    assert one['result_fingerprint'] == two['result_fingerprint']
    assert one['result'] == {'rows': 0}
    assert one['attempt_id'] != two['attempt_id']


@pytest.mark.parametrize('error', [TimeoutError('timeout'), ConnectionError('offline'), RuntimeError('HTTPS Connection refused')])
def test_transient_errors_retry_and_return_result(error):
    calls, messages = [], []
    def operation():
        calls.append(1)
        if len(calls) < 3:
            raise error
        return 'ok'
    assert data_repair.retry_transient(operation, lambda: False, messages.append) == 'ok'
    assert len(calls) == 3
    assert len(messages) == 2


def test_invalid_content_does_not_retry():
    calls = []
    def operation():
        calls.append(1)
        raise ValueError('来源未返回行情')
    with pytest.raises(ValueError):
        data_repair.retry_transient(operation, lambda: False, lambda _: None)
    assert len(calls) == 1


def test_retry_limit_and_cancellation():
    calls = []
    def operation():
        calls.append(1)
        raise TimeoutError('network timeout')
    with pytest.raises(TimeoutError):
        data_repair.retry_transient(operation, lambda: False, lambda _: None)
    assert len(calls) == 3
    calls.clear()
    with pytest.raises(InterruptedError):
        data_repair.retry_transient(operation, lambda: bool(calls), lambda _: None)
    assert len(calls) == 1


def test_cancel_before_publish_preserves_existing_cache(tmp_path, monkeypatch):
    path = tmp_path / 'bars.duckdb'
    publish(bars(), path)
    original = data_repair.upsert_bars
    cancelled = []
    def finish_staging(frame, target):
        result = original(frame, target)
        cancelled.append(True)
        return result
    monkeypatch.setattr(data_repair, 'upsert_bars', finish_staging)
    with pytest.raises(InterruptedError):
        data_repair.validate_and_publish(bars().assign(close=10.5), path,
            '600000.SH', '2026-08-01', '2026-08-10', cancelled=lambda: bool(cancelled))
    assert load_bars(path)['close'].tolist() == [11.]


def test_nullable_unknown_adjustment_rejected(tmp_path):
    frame = bars().assign(adjustment=pd.Series([pd.NA], dtype='string'))
    with pytest.raises(ValueError):
        publish(frame, tmp_path / 'bars.duckdb')


def test_legacy_false_adjusted_flag_uses_explicit_hfq_metadata(tmp_path):
    path = tmp_path / 'bars.duckdb'
    publish(bars().assign(adjusted=False), path)
    saved = load_bars(path)
    assert saved['adjustment'].tolist() == ['hfq']
    assert saved['adjusted'].tolist() == [True]


def test_wrapped_timeout_retries():
    calls = []
    def operation():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('subprocess failed: TimeoutError: provider unavailable')
        return 42
    assert data_repair.retry_transient(operation, lambda: False, lambda _: None) == 42
    assert len(calls) == 2


def test_publishing_new_shard_preserves_successful_prior_days(tmp_path):
    path = tmp_path / 'bars.duckdb'
    publish(bars(), path)
    publish(bars().assign(ts='2026-08-04', close=10.5), path)
    assert load_bars(path)['close'].tolist() == [11., 10.5]


def test_open_database_wal_is_never_copied(tmp_path):
    path = tmp_path / 'bars.duckdb'
    publish(bars(), path)
    wal = tmp_path / 'bars.duckdb.wal'
    wal.write_bytes(b'uncheckpointed')
    try:
        with pytest.raises(OSError):
            publish(bars().assign(close=10.5), path)
    finally:
        wal.unlink()
    assert load_bars(path)['close'].tolist() == [11.]


@pytest.mark.parametrize('missing', [None, float('nan'), float('inf'), -float('inf')])
def test_missing_quantities_do_not_erase_valid_cached_values(tmp_path, missing):
    path = tmp_path / 'bars.duckdb'
    publish(bars().assign(amount=1000.), path)
    publish(bars().assign(close=10.5, volume=missing, amount=missing), path)
    saved = load_bars(path)
    assert saved['close'].tolist() == [10.5]
    assert saved['volume'].tolist() == [100.]
    assert saved['amount'].tolist() == [1000.]


def test_omitted_quantities_retain_existing_values(tmp_path):
    path = tmp_path / 'bars.duckdb'
    publish(bars().assign(amount=1000.), path)
    publish(bars().drop(columns='volume'), path)
    saved = load_bars(path)
    assert saved['volume'].tolist() == [100.]
    assert saved['amount'].tolist() == [1000.]


def test_authentic_zero_quantities_replace_previous_values(tmp_path):
    path = tmp_path / 'bars.duckdb'
    publish(bars().assign(amount=1000.), path)
    publish(bars().assign(volume=0., amount=0.), path)
    saved = load_bars(path)
    assert saved['volume'].tolist() == [0.]
    assert saved['amount'].tolist() == [0.]


def test_missing_quantities_do_not_borrow_other_dates(tmp_path):
    path = tmp_path / 'bars.duckdb'
    publish(bars().assign(amount=1000.), path)
    publish(bars().assign(ts='2026-08-04', volume=None, amount=None), path)
    saved = load_bars(path)
    assert pd.isna(saved.iloc[1]['volume'])
    assert pd.isna(saved.iloc[1]['amount'])


@pytest.mark.parametrize('returned', ['none', 'qfq'])
def test_adjustment_unavailable_carries_query_and_response_without_overwriting(tmp_path, returned):
    path = tmp_path / 'bars.duckdb'
    publish(bars(), path)
    with pytest.raises(ValueError) as caught:
        publish(bars().assign(adjustment=returned), path)
    error = caught.value
    assert type(error).__name__ == 'AdjustmentUnavailable'
    assert error.requested_adjustment == 'hfq'
    assert error.returned_adjustment == [returned]
    assert error.query == dict(symbol='600000.SH', start='2026-08-01', end='2026-08-10', requested_adjustment='hfq')
    assert error.response_evidence == [dict(date='2026-08-03', returned_adjustment=returned)]
    assert load_bars(path)['adjustment'].tolist() == ['hfq']


@pytest.mark.parametrize('frame', [bars().assign(adjustment=None), bars().assign(adjustment='unknown'),
    bars().assign(adjustment='none', close=-1), bars().assign(adjustment='none', ts='bad-date')])
def test_invalid_response_is_not_persistent_adjustment_limitation(tmp_path, frame):
    with pytest.raises(ValueError) as caught:
        publish(frame, tmp_path / 'bars.duckdb')
    assert type(caught.value).__name__ != 'AdjustmentUnavailable'


def test_adjustment_mismatch_does_not_retry_even_with_old_transport_context(tmp_path):
    calls = []
    def operation():
        calls.append(1)
        try:
            raise TimeoutError('previous independent request timed out')
        except TimeoutError:
            publish(bars().assign(adjustment='none'), tmp_path / 'bars.duckdb')
    with pytest.raises(data_repair.AdjustmentUnavailable):
        data_repair.retry_transient(operation, lambda: False, lambda _: None)
    assert len(calls) == 1


def test_adjusted_return_must_agree_with_source_reported_return_before_publication(tmp_path):
    path=tmp_path/'bars.duckdb';publish(bars(),path)
    inconsistent=pd.concat([bars().assign(close=10.,ts='2026-08-03',provider_pct_change=0.),bars().assign(close=11.,ts='2026-08-04',provider_pct_change=1.)],ignore_index=True)
    with pytest.raises(ValueError,match='涨跌幅'):
        publish(inconsistent,path)
    assert load_bars(path)['close'].tolist()==[11.]
    consistent=inconsistent.copy();consistent.loc[1,'provider_pct_change']=10.
    publish(consistent,path)
    assert len(load_bars(path))==2


def test_baostock_daily_response_retains_reported_change_for_validation(monkeypatch):
    import sys
    from types import SimpleNamespace
    from etf_strategy.src.market_data_providers import BaoStockProvider,FetchRequest
    class Result:
        error_code='0';error_msg=''
        def __init__(self,fields):self.fields=fields.split(',');self.remaining=True
        def next(self):
            value=self.remaining;self.remaining=False;return value
        def get_row_data(self):
            row=dict(date='2025-01-09',code='sz.000002',open='6.93',high='7',low='6.9',close='6.95',volume='10000',amount='69500',adjustflag='1',pctChg='-0.143700')
            return [row[f] for f in self.fields]
    fake=SimpleNamespace(login=lambda:SimpleNamespace(error_code='0',error_msg=''),logout=lambda:None,query_history_k_data_plus=lambda symbol,fields,**kwargs:Result(fields))
    monkeypatch.setitem(sys.modules,'baostock',fake)
    frame=BaoStockProvider().fetch_ohlcv(FetchRequest('a_share','000002','1d',pd.Timestamp('2025-01-09'),pd.Timestamp('2025-01-09'),options={'adjust':'hfq'}))
    assert frame.provider_pct_change.iloc[0]==pytest.approx(-.1437)
