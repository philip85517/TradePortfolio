"""Recovery exercises real inspection; provider status is explicit evidence."""
import json
import pandas as pd
from alphalab.tests.integration.test_wizard_backend import make_backend, scope, rule_backend


def test_unknown_zero_volume_plans_status_verification(tmp_path):
    backend, adapter = make_backend(tmp_path)
    mask = adapter.bars.symbol.eq('000001') & adapter.bars.date.eq('2023-06-01')
    adapter.bars.loc[mask, 'volume'] = 0
    ready = backend.inspect(scope())
    issue = next(i for i in ready['issues'] if i['symbol'] == '000001')
    assert issue['resolution'] == 'verify'
    assert issue['date_ranges'] == [{'start': '2023-06-01', 'end': '2023-06-01'}]
    assert ready['repair_plan']['actions'][0]['kind'] == 'verify_status'
    assert ready['repair_plan']['plan_id'] == backend.inspect(scope())['repair_plan']['plan_id']


def test_confirmed_suspension_is_one_non_downloadable_event(tmp_path):
    backend, adapter, history, request = rule_backend(tmp_path)
    mask = adapter.bars.symbol.eq('000001') & adapter.bars.date.eq('2020-12-01')
    adapter.bars.loc[mask, ['volume','amount']] = [0, float('nan')]
    adapter.bars.loc[mask, 'tradestatus'] = '0'
    ready = backend.inspect(request)
    events = [i for i in ready['issues'] if i['symbol'] == '000001']
    assert len(events) == 1
    assert events[0]['code'] == 'SUSPENDED'
    assert events[0]['resolution'] == 'none'
    assert events[0]['severity'] == 'info'
    assert ready['repair_plan']['executable_count'] == 0
    assert ready['status'] == 'READY'


def test_delisted_gap_does_not_request_nonexistent_future_bars(tmp_path):
    backend, adapter = make_backend(tmp_path)
    adapter.bars.loc[adapter.bars.symbol.eq('000001'), 'delisted_date'] = '2023-01-01'
    adapter.bars = adapter.bars[~(adapter.bars.symbol.eq('000001') & adapter.bars.date.ge('2023-01-01'))]
    ready = backend.inspect(scope())
    assert any(i['code'] == 'DELISTED_UNSETTLED' and i['severity']=='warning' for i in ready['issues'])
    assert not any(i['code'] == 'MISSING_BARS' for i in ready['issues'])
    assert ready['repair_plan']['executable_count'] == 0


def test_pit_only_blocker_has_no_download_plan(tmp_path):
    backend, _ = make_backend(tmp_path)
    request = scope(); request['quality_mode'] = 'strict'
    ready = backend.inspect(request)
    assert ready['status'] == 'BLOCKED'
    assert ready['repair_plan']['executable_count'] == 0
    assert ready['issues'][0]['resolution'] == 'user'


def test_normal_trading_missing_amount_is_downloadable(tmp_path):
    backend, adapter, _, request = rule_backend(tmp_path)
    mask = adapter.bars.symbol.eq('000001') & adapter.bars.date.eq('2020-12-01')
    adapter.bars.loc[mask, 'amount'] = float('nan')
    adapter.bars.loc[mask, 'tradestatus'] = '1'
    ready = backend.inspect(request)
    issue = next(i for i in ready['issues'] if i['code'] == 'MISSING_FACTOR_FIELD')
    assert issue['resolution'] == 'download'
    assert ready['repair_plan']['actions'][0]['kind'] == 'bars'


def test_status_verification_persists_event_and_stops_download_loop(tmp_path):
    backend, adapter = make_backend(tmp_path)
    mask = adapter.bars.symbol.eq('000001') & adapter.bars.date.eq('2023-06-01')
    adapter.bars.loc[mask, 'volume'] = 0
    class Provider:
        def fetch_trading_status(self, symbol, start, end):
            return pd.DataFrame([{'date': '2023-06-01', 'tradestatus': '0'}])
        def fetch_ohlcv(self, request):
            raise AssertionError('confirmed suspension must not download prices')
    backend.provider = Provider()
    ready = backend.prepare(scope(), lambda _: None, lambda: False)
    assert any(i['code'] == 'SUSPENDED' for i in ready['issues'])
    assert ready['repair_plan']['executable_count'] == 0
    assert ready['repair_summary']['attempted'] == 1
    assert backend.prepare(scope(), lambda _: None, lambda: False)['status'] == 'READY'


def test_empty_plan_does_not_create_task_and_stale_plan_rejected(tmp_path):
    import pytest
    from alphalab.research.workflow import ResearchWorkflow
    from alphalab.tests.integration.test_workflow import finish
    backend, adapter = make_backend(tmp_path/'cache')
    flow = ResearchWorkflow(tmp_path/'workflow', backend=backend)
    try:
        draft = flow.create_draft()
        request = scope(); request['quality_mode'] = 'strict'
        draft = flow.update_draft(draft['id'], draft['revision'], scope=request)
        checked = flow.check(draft['id'], draft['revision'])
        plan = checked['readiness']['repair_plan']['plan_id']
        with pytest.raises(ValueError, match='没有可自动'):
            flow.prepare(draft['id'], draft['revision'], plan_id=plan)
        assert not flow._all('tasks')
        # An executable approved plan is revalidated by the worker, before downloads.
        adapter.bars.loc[adapter.bars.symbol.eq('000001'), 'close'] = -1
        checked = flow.check(draft['id'], draft['revision'])
        plan = checked['readiness']['repair_plan']['plan_id']
        assert checked['readiness']['repair_plan']['executable_count'] > 0
        adapter.bars.loc[adapter.bars.symbol.eq('000001'), 'close'] = -2
        task = flow.prepare(draft['id'], draft['revision'], plan_id=plan)
        task = finish(flow, task['id'])
        assert task['status'] == 'FAILED' and task['error_code'] == 'REPAIR_PLAN_CHANGED'
        assert flow.get_draft(draft['id'])['readiness']['repair_plan']['plan_id'] != plan
    finally:
        flow.close()


def test_cancel_later_action_keeps_earlier_attempt_evidence(tmp_path):
    import pytest
    from etf_strategy.src.market_data_store import normalize_bars
    backend, adapter = make_backend(tmp_path)
    adapter.bars.loc[adapter.bars.date.eq('2023-06-01'), 'close'] = -1
    class Provider:
        def fetch_ohlcv(self, request):
            if request.symbol == '600000':
                raise InterruptedError('cancel second shard')
            rows = adapter.bars[adapter.bars.symbol.eq(request.symbol) & adapter.bars.date.between(request.start, request.end)].copy()
            rows['close'] = 10.5; rows['ts'] = rows.date; rows['timeframe'] = '1d'
            return normalize_bars(rows)
    backend.provider = Provider()
    with pytest.raises(InterruptedError):
        backend.prepare(scope(), lambda _: None, lambda: False)
    records = [json.loads(p.read_text()) for p in (tmp_path/'repair_attempts').glob('*.json')]
    assert any(r['action']['symbol'] == '000001' and r['status'] == 'completed' for r in records)
    assert any(r['action']['symbol'] == '600000' and r['status'] == 'cancelled' for r in records)
    ready = backend.inspect(scope())
    assert not any(i['symbol'] == '000001' for i in ready['issues'])


def test_calendar_bootstrap_transient_failure_retries_and_records(tmp_path, monkeypatch):
    backend, _ = make_backend(tmp_path)
    real_calendar = backend.calendar
    backend.calendar = None
    calls = []
    def provision(start, end):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError('network timeout')
        backend.calendar = real_calendar
    monkeypatch.setattr(backend, '_provision_calendar', provision)
    ready = backend.prepare(scope(), lambda _: None, lambda: False)
    assert ready['status'] == 'READY'
    assert len(calls) == 2
    records = [json.loads(p.read_text()) for p in (tmp_path/'repair_attempts').glob('*.json')]
    assert any(r['action']['kind'] == 'calendar' and r['status'] == 'resolved' for r in records)


def test_child_transport_error_remains_retryable(tmp_path, monkeypatch):
    import subprocess
    import pytest
    from alphalab.research.data_repair import retry_transient
    backend, _ = make_backend(tmp_path)
    calls=[]
    def child(*args, **kwargs):
        calls.append(1)
        return subprocess.CompletedProcess(args, 1, stdout='', stderr='ConnectionError: Connection refused')
    monkeypatch.setattr(subprocess, 'run', child)
    with pytest.raises(ConnectionError):
        retry_transient(lambda: backend._child('pass', []), lambda: False, lambda _: None)
    assert len(calls) == 3


def test_first_cache_repair_preserves_original_source_quantities(tmp_path):
    from etf_strategy.src.market_data_store import normalize_bars
    backend, adapter = make_backend(tmp_path)
    mask = adapter.bars.symbol.eq('600000') & adapter.bars.date.eq('2023-06-01')
    adapter.bars.loc[mask, 'close'] = -1
    class Provider:
        def fetch_ohlcv(self, request):
            rows=adapter.bars[adapter.bars.symbol.eq(request.symbol) & adapter.bars.date.between(request.start,request.end)].copy()
            rows['close']=10.5; rows['ts']=rows.date; rows['timeframe']='1d'
            rows['volume']=float('nan'); rows['amount']=float('nan')
            return normalize_bars(rows)
    backend.provider=Provider()
    result=backend.prepare(scope(),lambda _:None,lambda:False)
    assert result['status']=='READY',result['issues']
    assert adapter.bars.loc[mask,'close'].iloc[0] == -1  # source stays immutable


def test_inspect_reports_per_stock_progress_and_cancels(tmp_path):
    import pytest
    backend,_=make_backend(tmp_path)
    stages=[]
    result=backend.inspect(scope(),progress=stages.append)
    assert result['status']=='READY'
    assert any('逐股检查' in stage for stage in stages)
    with pytest.raises(InterruptedError):
        backend.inspect(scope(),cancelled=lambda:True)


def test_provider_adjustment_mismatch_becomes_persistent_non_retryable_issue(tmp_path):
    from etf_strategy.src.market_data_store import normalize_bars
    backend, adapter=make_backend(tmp_path)
    adapter.bars.loc[adapter.bars.symbol.eq('600000'),'adjustment']='none'
    calls=[]
    class Provider:
        def fetch_ohlcv(self, request):
            calls.append(request.symbol)
            rows=adapter.bars[adapter.bars.symbol.eq(request.symbol)&adapter.bars.date.between(request.start,request.end)].copy()
            rows['ts']=rows.date;rows['timeframe']='1d'
            return normalize_bars(rows)
    backend.provider=Provider()
    ready=backend.prepare(scope(),lambda _:None,lambda:False)
    assert any(i['code']=='SOURCE_ADJUSTMENT_UNAVAILABLE' for i in ready['issues'])
    assert ready['repair_plan']['executable_count']==0
    backend.prepare(scope(),lambda _:None,lambda:False)
    assert calls==['600000']


def test_phase_aware_wizard_entry_suspension_keeps_cash_and_other_weight(tmp_path):
    from alphalab.tests.integration.test_wizard_backend import portfolio
    backend,adapter=make_backend(tmp_path)
    row=adapter.bars.symbol.eq('000001')&adapter.bars.date.eq('2021-01-04')
    adapter.bars.loc[row,'tradestatus']='0';adapter.bars.loc[row,['volume','amount']]=float('nan')
    adapter.bars.loc[row,['open','high','low','close']]=0.
    adapter.bars['execution_open']=10.
    ready=backend.inspect(scope())
    assert ready['status']=='READY',ready['issues']
    assert next(i for i in ready['issues'] if i['code']=='SUSPENDED')['severity']=='info'
    config=portfolio();config['min_holdings']=1
    preview=backend.preview(scope(),config,ready)
    assert [h['symbol'] for h in preview['holdings']]==['600000']
    assert preview['holdings'][0]['target_weight']==.2
    assert preview['cash_residual']>79000


def test_explicit_source_reverification_queries_only_symbol_and_preserves_unsupported_evidence(tmp_path):
    from etf_strategy.src.market_data_store import normalize_bars
    backend, adapter = make_backend(tmp_path)
    adapter.bars.loc[adapter.bars.symbol.eq('600000'), 'adjustment'] = 'none'
    calls = []
    class Provider:
        adjustment = 'none'
        def fetch_ohlcv(self, request):
            calls.append(request.symbol)
            if len(calls) > 1:
                assert list((tmp_path / 'source_capabilities').glob('*.json')), 'keep limitation until validated publish'
            rows = adapter.bars[adapter.bars.symbol.eq(request.symbol) & adapter.bars.date.between(request.start, request.end)].copy()
            rows['adjustment'] = self.adjustment
            rows['ts'] = rows.date; rows['timeframe'] = '1d'
            return normalize_bars(rows)
    backend.provider = Provider()
    ready = backend.prepare(scope(), lambda _: None, lambda: False)
    original = next(i for i in ready['issues'] if i['code'] == 'SOURCE_ADJUSTMENT_UNAVAILABLE')['evidence']
    backend.retry_source(scope(), '600000')
    assert calls == ['600000', '600000']
    ready = backend.inspect(scope())
    evidence = next(i for i in ready['issues'] if i['code'] == 'SOURCE_ADJUSTMENT_UNAVAILABLE')['evidence']
    assert evidence['data_identity'] == original['data_identity']
    assert ready['repair_plan']['executable_count'] == 0
    backend.prepare(scope(), lambda _: None, lambda: False)
    assert calls == ['600000', '600000']
    backend.provider.adjustment = 'hfq'
    backend.retry_source(scope(), '600000')
    assert calls == ['600000', '600000', '600000']
    assert backend.inspect(scope())['status'] == 'READY'
    assert not list((tmp_path / 'source_capabilities').glob('*.json'))


def test_cancelled_explicit_source_reverification_keeps_original_limitation_and_cache(tmp_path):
    import pytest
    from etf_strategy.src.market_data_store import normalize_bars
    backend, adapter = make_backend(tmp_path)
    adapter.bars.loc[adapter.bars.symbol.eq('600000'), 'adjustment'] = 'none'
    calls = []
    class Provider:
        def fetch_ohlcv(self, request):
            calls.append(request.symbol)
            rows = adapter.bars[adapter.bars.symbol.eq(request.symbol) & adapter.bars.date.between(request.start, request.end)].copy()
            rows['ts'] = rows.date; rows['timeframe'] = '1d'
            if len(calls) > 1:
                rows['adjustment'] = 'hfq'
            return normalize_bars(rows)
    backend.provider = Provider()
    backend.prepare(scope(), lambda _: None, lambda: False)
    target = next((tmp_path / 'source_capabilities').glob('*.json'))
    original = target.read_bytes()
    with pytest.raises(InterruptedError):
        backend.retry_source(scope(), '600000', cancelled=lambda: len(calls) > 1)
    assert calls == ['600000', '600000']
    assert target.read_bytes() == original
    assert not (tmp_path / 'market_data.duckdb').exists()
