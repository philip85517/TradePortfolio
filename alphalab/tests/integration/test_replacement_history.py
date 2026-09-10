import pandas as pd
import pytest


def row(symbol='600000', **changes):
    value = dict(date='2026-01-05', code='sh.'+symbol, open='10', high='11', low='9', close='10', volume='100', amount='1000', adjustflag='1', tradestatus='1', pctChg='0')
    value.update(changes)
    return value


def fetch(requests, cache, provider):
    from alphalab.research.replacement_history import fetch_replacement_history
    return fetch_replacement_history(requests, cache, fetch_batch=provider)


def test_success_cache_reused_and_normalized(tmp_path):
    request = [('600000', '2026-01-05', '2026-01-06')]
    first = fetch(request, tmp_path, lambda batch: {'600000': [row()]})
    assert first['600000'].adjustment.tolist() == ['hfq']
    assert first['600000'].date.iloc[0] == pd.Timestamp('2026-01-05')
    assert first['600000'].close.iloc[0] == 10
    def forbidden(batch):
        pytest.fail('cached success must not fetch again')
    pd.testing.assert_frame_equal(first['600000'], fetch(request, tmp_path, forbidden)['600000'])
    assert len(list((tmp_path/'replacement_history').glob('*.json'))) == 1


@pytest.mark.parametrize('changes,match', [({'code':'sh.600001'}, 'symbol'), ({'date':'2026-01-07'}, 'date'), ({'adjustflag':'2'}, 'adjust'), ({'tradestatus':''}, 'status'), ({'close':'nan'}, 'OHLC'), ({'amount':'0','volume':'0'}, 'turnover')])
def test_invalid_provider_response_not_cached(tmp_path, changes, match):
    with pytest.raises(ValueError, match=match):
        fetch([('600000','2026-01-05','2026-01-06')], tmp_path, lambda batch: {'600000':[row(**changes)]})
    assert not list((tmp_path/'replacement_history').glob('*.json'))


def test_suspension_allows_missing_ohlc_but_duplicates_rejected(tmp_path):
    frame = fetch([('600000','2026-01-05','2026-01-06')], tmp_path, lambda batch: {'600000':[row(tradestatus='0',open='',high='',low='',close='',volume='0',amount='0')]})['600000']
    assert frame.tradestatus.iloc[0] == 0
    assert pd.isna(frame.close.iloc[0])
    with pytest.raises(ValueError, match='duplicate'):
        fetch([('600000','2026-01-05','2026-01-07')], tmp_path, lambda batch: {'600000':[row(),row()]})


def test_partial_success_survives_failure_and_retry_only_fetches_missing(tmp_path):
    requests = [('600000','2026-01-05','2026-01-06'),('600001','2026-01-05','2026-01-06')]
    with pytest.raises(ValueError, match='600001'):
        fetch(requests, tmp_path, lambda batch: {'600000':[row()]})
    def retry(batch):
        assert batch == [requests[1]]
        return {'600001':[row('600001')]}
    assert set(fetch(requests,tmp_path,retry)) == {'600000','600001'}


def test_batches_have_at_most_25_requests(tmp_path):
    requests = [(str(600000+i),'2026-01-05','2026-01-06') for i in range(53)]
    def provider(batch):
        assert len(batch) <= 25
        return {symbol:[row(symbol)] for symbol,_,_ in batch}
    assert len(fetch(requests,tmp_path,provider)) == 53


def test_progress_reports_cached_and_current_completed_counts(tmp_path):
    from alphalab.research.replacement_history import fetch_replacement_history
    requests = [('600000','2026-01-05','2026-01-06')]
    fetch(requests,tmp_path,lambda batch:{'600000':[row()]})
    messages=[]
    fetch_replacement_history(requests+[('600001','2026-01-05','2026-01-06')],tmp_path,
                              fetch_batch=lambda batch:{'600001':[row('600001')]},progress=messages.append)
    assert any('已缓存 1/2' in message for message in messages)
    assert '本轮完成 1/1' in messages[-1]


def test_batch_timeout_preserves_completed_responses(tmp_path, monkeypatch):
    import json
    import subprocess
    from pathlib import Path
    from alphalab.research import replacement_history as module
    def timeout(command, **kwargs):
        assert kwargs['timeout'] == 120
        Path(command[4], '0.json').write_text(json.dumps({'rows':[row()]}))
        raise subprocess.TimeoutExpired(command,120)
    monkeypatch.setattr(module.subprocess,'run',timeout)
    requests=[('600000','2026-01-05','2026-01-06'),('600001','2026-01-05','2026-01-06')]
    with pytest.raises(ValueError,match='600001.*120s'):
        module.fetch_replacement_history(requests,tmp_path)
    assert len(list((tmp_path/'replacement_history').glob('*.json'))) == 1


def test_corrupt_cache_is_revalidated_and_repaired(tmp_path):
    import json
    requests=[('600000','2026-01-05','2026-01-06')]
    fetch(requests,tmp_path,lambda batch:{'600000':[row()]})
    path=next((tmp_path/'replacement_history').glob('*.json'))
    payload=json.loads(path.read_text())
    payload['rows'][0]['tradestatus']='unknown'
    path.write_text(json.dumps(payload))
    result=fetch(requests,tmp_path,lambda batch:{'600000':[row(close='12',high='13')]})
    assert result['600000'].close.iloc[0] == 12
    assert json.loads(path.read_text())['rows'][0]['tradestatus'] == '1'


def test_changed_date_interval_uses_distinct_cache_key(tmp_path):
    fetch([('600000','2026-01-05','2026-01-06')],tmp_path,lambda batch:{'600000':[row()]})
    result=fetch([('600000','2026-01-05','2026-01-07')],tmp_path,lambda batch:{'600000':[row(),row(date='2026-01-07')]})
    assert len(result['600000']) == 2
    assert len(list((tmp_path/'replacement_history').glob('*.json'))) == 2


@pytest.mark.parametrize('changes', [{'high':'9'}, {'low':'11'}])
def test_inconsistent_ohlc_geometry_rejected_with_symbol_date(tmp_path, changes):
    with pytest.raises(ValueError,match='600000.*2026-01-05.*OHLC'):
        fetch([('600000','2026-01-05','2026-01-06')],tmp_path,lambda batch:{'600000':[row(**changes)]})
    assert not list((tmp_path/'replacement_history').glob('*.json'))


def test_hfq_source_pct_change_conflict_rejected_across_suspension(tmp_path):
    rows=[row(),row(date='2026-01-06',tradestatus='0',open='',high='',low='',close='',amount='0',volume='0',pctChg=''),
          row(date='2026-01-07',open='11',high='12',low='10',close='11',pctChg='0')]
    with pytest.raises(ValueError,match='600000.*2026-01-07.*涨跌幅'):
        fetch([('600000','2026-01-05','2026-01-07')],tmp_path,lambda batch:{'600000':list(reversed(rows))})
    assert not list((tmp_path/'replacement_history').glob('*.json'))


def test_five_basis_point_rounding_and_first_row_have_no_false_conflict(tmp_path):
    rows=[row(pctChg='25'),row(date='2026-01-06',open='11',high='12',low='10',close='11',pctChg='9.96')]
    result=fetch([('600000','2026-01-05','2026-01-06')],tmp_path,lambda batch:{'600000':rows})
    assert result['600000'].close.tolist() == [10,11]


def test_expected_dates_rejects_truncated_cache_and_preserves_partial(tmp_path):
    from alphalab.research.replacement_history import fetch_replacement_history
    request=[('600000','2026-01-05','2026-01-06')]
    fetch(request,tmp_path,lambda batch:{'600000':[row()]})
    with pytest.raises(ValueError,match='600000.*2026-01-06'):
        fetch_replacement_history(request,tmp_path,fetch_batch=lambda batch:{'600000':[row()]},
                                  expected_dates={'600000':['2026-01-05','2026-01-06']})
    assert len(list((tmp_path/'replacement_history').glob('*.partial.json'))) == 1
    result=fetch_replacement_history(request,tmp_path,fetch_batch=lambda batch:{'600000':[row(),row(date='2026-01-06')]},
                                     expected_dates={'600000':['2026-01-05','2026-01-06']})
    assert len(result['600000']) == 2


def test_production_fetch_holds_cross_process_file_lock(tmp_path,monkeypatch):
    import fcntl
    from alphalab.research import replacement_history as module
    def provider(batch):
        with (tmp_path/'replacement_history'/'.baostock-session.lock').open('a+b') as stream:
            with pytest.raises(BlockingIOError):
                fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return {'600000':[row()]}
    monkeypatch.setattr(module,'_fetch_batch',provider)
    module.fetch_replacement_history([('600000','2026-01-05','2026-01-06')],tmp_path)


@pytest.mark.parametrize('always_failed', [False,True])
def test_worker_relogs_once_only_for_failed_request(tmp_path,monkeypatch,always_failed):
    import json,sys,types
    from alphalab.research import replacement_history as module
    calls=[]
    counts={'login':0,'query':0}
    def login():
        counts['login']+=1
        return types.SimpleNamespace(error_code='0',error_msg='')
    def query(code,*args,**kwargs):
        calls.append(code)
        counts['query']+=1
        failed=code.endswith('600001') and (always_failed or counts['query']==2)
        result=types.SimpleNamespace(error_code='10001001' if failed else '0',error_msg='用户未登录' if failed else '',fields=list(row()))
        data=iter([True,False])
        result.next=lambda:next(data)
        result.get_row_data=lambda:list(row(code[-6:]).values())
        return result
    monkeypatch.setitem(sys.modules,'baostock',types.SimpleNamespace(login=login,logout=lambda:None,query_history_k_data_plus=query))
    monkeypatch.setattr(sys,'argv',['worker',json.dumps([('600000','2026-01-05','2026-01-06'),('600001','2026-01-05','2026-01-06')]),str(tmp_path),module.FIELDS])
    exec(module._WORKER,{'__name__':'__main__'})
    assert calls == ['sh.600000','sh.600001','sh.600001']
    assert counts['login'] == 2
    assert ('error' in json.loads((tmp_path/'1.json').read_text())) == always_failed


def test_incomplete_new_response_does_not_prevent_other_success_cache(tmp_path):
    from alphalab.research.replacement_history import fetch_replacement_history
    requests=[('600000','2026-01-05','2026-01-06'),('600001','2026-01-05','2026-01-06')]
    required={s:['2026-01-05','2026-01-06'] for s,_,_ in requests}
    with pytest.raises(ValueError,match='600001.*2026-01-06'):
        fetch_replacement_history(requests,tmp_path,expected_dates=required,fetch_batch=lambda batch:{
            '600000':[row(),row(date='2026-01-06')],'600001':[row('600001')]})
    paths=list((tmp_path/'replacement_history').glob('*.json'))
    assert len([p for p in paths if '.partial.' not in p.name]) == 1
    assert len([p for p in paths if '.partial.' in p.name]) == 1
    def retry(batch):
        assert batch == [requests[1]]
        return {'600001':[row('600001'),row('600001',date='2026-01-06')]}
    assert len(fetch_replacement_history(requests,tmp_path,expected_dates=required,fetch_batch=retry)) == 2
