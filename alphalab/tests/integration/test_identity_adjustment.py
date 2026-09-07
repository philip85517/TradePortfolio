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
    adapter.bars.loc[adapter.bars.symbol.eq('000001'),'adjustment']='qfq'
    second=backend.preview(request,config,backend.inspect(request))
    assert [h['symbol'] for h in second['holdings']]==[h['symbol'] for h in first['holdings']]
    assert second['holdings'][0]['shares']==first['holdings'][0]['shares']
    assert second['cash_residual']==pytest.approx(first['cash_residual'])
