import pandas as pd
import pytest
from alphalab.tests.integration.test_wizard_backend import rule_backend, make_backend, scope


def alias_fixture(tmp_path):
    backend, adapter, history, request = rule_backend(tmp_path)
    mapping = {'000001':'300114', '600000':'302132'}
    adapter.bars['symbol'] = adapter.bars.symbol.replace(mapping)
    history['symbol'] = history.symbol.replace(mapping)
    adapter.bars.loc[adapter.bars.symbol.eq('302132'), 'adjustment'] = 'none'
    request['end_date'] = '2024-12-31'
    request['symbols'] = []
    return backend, adapter, history, request


def test_historical_pool_keeps_old_code_once_instead_of_future_alias(tmp_path):
    backend, adapter, history, request = alias_fixture(tmp_path)
    ready = backend.inspect(request)
    assert ready['status'] == 'READY', ready['issues']
    assert ready['symbols'] == ['300114']
    assert ready['selected_symbols'] == ['300114']
    event = next(i for i in ready['issues'] if i['code'] == 'SYMBOL_ALIAS_RESOLVED')
    assert event['severity'] == 'info'
    assert event['evidence']['effective_date'] == '2025-02-17'
    assert not ready['repair_plan']['actions']
    assert set(adapter.bars.symbol) == {'300114','302132'}


def test_missing_old_code_is_not_silently_removed_from_historical_pool(tmp_path):
    backend, adapter, history, request = alias_fixture(tmp_path)
    adapter.bars = adapter.bars[adapter.bars.symbol.ne('300114')]
    ready = backend.inspect(request)
    assert ready['status'] == 'BLOCKED'
    assert any(i['code'] == 'MISSING_BARS' and i['symbol'] == '300114' for i in ready['issues'])


def test_selected_code_transition_requires_execution_mapping(tmp_path):
    backend, adapter, history, request = alias_fixture(tmp_path)
    request['end_date'] = '2025-06-30'
    ready = backend.inspect(request)
    assert any(i['code'] == 'SYMBOL_TRANSITION_UNSUPPORTED' for i in ready['issues'])


def test_manual_future_code_is_explained_without_silently_changing_selection(tmp_path):
    backend, adapter = make_backend(tmp_path)
    adapter.bars['symbol'] = adapter.bars.symbol.replace({'000001':'302132'})
    request=scope(); request['symbols']=['302132']
    ready=backend.inspect(request)
    assert any(i['code']=='SYMBOL_NOT_EFFECTIVE' for i in ready['issues'])
    assert request['symbols']==['302132']


def test_adjustment_distribution_separates_cross_stock_and_within_stock_mix(tmp_path):
    backend,adapter=make_backend(tmp_path)
    adapter.bars.loc[adapter.bars.symbol.eq('600000'),'adjustment']='qfq'
    ready=backend.inspect(scope())
    summary=ready['adjustment_summary']
    assert summary['stock_counts']=={'hfq':1,'qfq':1}
    assert summary['mixed_symbols']==[]
    adapter.bars.loc[adapter.bars.symbol.eq('600000') & adapter.bars.date.eq('2023-06-01'),'adjustment']='hfq'
    ready=backend.inspect(scope())
    assert ready['adjustment_summary']['mixed_symbols']==['600000']
    assert any(i['code']=='ADJUSTMENT_UNAVAILABLE' for i in ready['issues'])


def test_pit_issue_identifies_missing_history_fields(tmp_path):
    backend,adapter,history,request=rule_backend(tmp_path)
    history['industry_level2']=None
    ready=backend.inspect(request)
    issue=next(i for i in ready['issues'] if i['code']=='PIT_UNAVAILABLE')
    assert issue['evidence']['missing_fields']=={'industry_level2':2}


def test_fixed_rule_and_real_entry_lots_are_invariant_under_constant_price_scale(tmp_path):
    from alphalab.tests.integration.test_wizard_backend import portfolio
    backend,adapter,history,request=rule_backend(tmp_path)
    adapter.bars['execution_open']=10.
    config=portfolio();config.update(weighting='equal',min_holdings=1)
    first=backend.preview(request,config,backend.inspect(request))
    adapter.bars.loc[adapter.bars.symbol.eq('000001'),['open','high','low','close']]*=7.
    second=backend.preview(request,config,backend.inspect(request))
    assert [h['symbol'] for h in second['holdings']]==[h['symbol'] for h in first['holdings']]
    assert second['holdings'][0]['shares']==first['holdings'][0]['shares']
    assert second['cash_residual']==pytest.approx(first['cash_residual'])


def test_qfq_is_planned_for_verified_hfq_replacement(tmp_path):
    backend,adapter=make_backend(tmp_path)
    adapter.bars.loc[adapter.bars.symbol.eq('600000'),'adjustment']='qfq'
    ready=backend.inspect(scope())
    issue=next(i for i in ready['issues'] if i['code']=='ADJUSTMENT_STANDARDIZATION_REQUIRED')
    assert issue['symbol']=='600000'
    assert issue['resolution']=='download'
    assert ready['status']=='BLOCKED'
    action=next(a for a in ready['repair_plan']['actions'] if a['symbol']=='600000')
    assert action['kind']=='bars'
    assert action['start']==ready['dates']['warmup_start_date']
    assert action['end']==ready['dates']['exit_date']
    assert ready['adjustment_summary']['target']=='hfq'
    assert ready['adjustment_summary']['remaining_symbols']==['600000']


def test_uniform_repair_publishes_new_prices_and_retains_original_source(tmp_path):
    from etf_strategy.src.market_data_store import normalize_bars
    backend,adapter=make_backend(tmp_path)
    adapter.bars.loc[adapter.bars.symbol.eq('600000'),'adjustment']='qfq'
    class Provider:
        def fetch_ohlcv(self,request):
            data=adapter.load(request.start.date(),request.end.date(),symbols=[request.symbol])
            data[['open','high','low','close']]*=5
            data['adjustment']='hfq';data['ts']=data.date;data['timeframe']='1d'
            return normalize_bars(data)
    backend.provider=Provider()
    ready=backend.prepare(scope(),lambda _:None,lambda:False)
    assert ready['status']=='READY',ready['issues']
    assert ready['adjustment_summary']['stock_counts']=={'hfq':2}
    assert ready['adjustment_summary']['remaining_symbols']==[]
    assert set(adapter.bars[adapter.bars.symbol.eq('600000')].adjustment)=={'qfq'}
    _,data,_=backend._inspect(scope())
    assert data.loc[data.symbol.eq('600000'),'close'].iloc[0]==52.5


def test_qfq_source_refusal_stops_ordinary_uniform_retries(tmp_path):
    from etf_strategy.src.market_data_store import normalize_bars
    backend,adapter=make_backend(tmp_path)
    adapter.bars.loc[adapter.bars.symbol.eq('600000'),'adjustment']='qfq'
    calls=[]
    class Provider:
        def fetch_ohlcv(self,request):
            calls.append(request.symbol)
            data=adapter.load(request.start.date(),request.end.date(),symbols=[request.symbol])
            data['ts']=data.date;data['timeframe']='1d'
            return normalize_bars(data)
    backend.provider=Provider()
    ready=backend.prepare(scope(),lambda _:None,lambda:False)
    assert any(i['code']=='SOURCE_ADJUSTMENT_UNAVAILABLE' for i in ready['issues'])
    assert ready['repair_plan']['executable_count']==0
    backend.prepare(scope(),lambda _:None,lambda:False)
    assert calls==['600000']
