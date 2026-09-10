import pytest
from alphalab.tests.integration.test_wizard_backend import make_backend, scope, portfolio


def test_policy_is_explicit_and_rejects_unsupported_manual_replacement(tmp_path):
    backend, _ = make_backend(tmp_path)
    ready = backend.inspect(scope())
    assert backend._spec(scope(), portfolio(), ready).wizard_metadata['delisting_policy'] == 'retain-unsettled-v1'
    with pytest.raises(ValueError, match='固定规则'):
        backend._spec(scope(), portfolio(delisting_policy='announcement-replace-v1'), ready)


def test_policy_freezes_announcement_evidence(tmp_path):
    backend, _ = make_backend(tmp_path)
    ready = backend.inspect(scope())
    request = scope(); request['selection_mode'] = 'rule'
    settings = portfolio(delisting_policy='announcement-replace-v1'); settings['weighting'] = 'equal'
    spec = backend._spec(request, settings, ready)
    assert spec.wizard_metadata['delisting_policy'] == 'announcement-replace-v1'
    assert spec.wizard_metadata['delisting_events'][0]['source_url'].startswith('https://')
    assert spec.wizard_metadata['holding_method'] == 'announcement_exit_and_replace'


def test_run_persists_replacement_sales_and_complete_nav(tmp_path):
    import json
    import numpy as np
    import pandas as pd
    from pathlib import Path
    backend, adapter = make_backend(tmp_path)
    for symbol, part in adapter.bars.groupby('symbol'):
        close = np.linspace(10,40,len(part))
        adapter.bars.loc[part.index,['open','close']] = np.column_stack([close,close])
        adapter.bars.loc[part.index,'high'] = close+1
        adapter.bars.loc[part.index,'low'] = close-1
    adapter.bars['tradestatus']=1
    adapter.bars.loc[adapter.bars.symbol.eq('600000'),'delisted_date']='2024-01-02'
    adapter.bars=adapter.bars[~(adapter.bars.symbol.eq('600000') & adapter.bars.date.ge('2024-01-02'))]
    (tmp_path/'delisting_events.json').write_text(json.dumps([dict(symbol='600000',event_id='fixture',event_type='termination_decision',published_at='2023-06-01',source_url='https://example.com/decision',delisted_date='2024-01-02')]))
    adapter.load_universe_as_of=lambda *args: pd.DataFrame([dict(symbol=s,listed_date='2010-01-01',effective_from='2010-01-01',effective_to=None,source='fixture',snapshot_id='v1') for s in ['000001','600000']])
    request=scope(); request.update(selection_mode='rule',symbols=[])
    settings=portfolio(delisting_policy='announcement-replace-v1');settings['weighting']='equal'
    ready=backend.inspect(request)
    assert ready['status']=='READY',ready['issues']
    result=backend.run(request,settings,ready,tmp_path/'runs')
    assert result['summary']['status']=='COMPLETE'
    assert result['summary']['total_return'] is not None
    sales=[e for e in result['summary']['execution_events'] if e['action']=='SELL' and e['reason']=='termination_decision']
    assert sales[0]['date']=='2023-06-02'
    h=str(ready['dates']['horizon'])
    assert result['manifest']['portfolio_performance']['strategy'][h]['execution_events']==result['summary']['execution_events']
    nav=pd.read_csv(Path(result['artifact_dir'])/'nav.csv')
    assert nav.iloc[-1].equity==pytest.approx(result['summary']['ending_equity'])


def test_existing_draft_accepts_new_policy_without_losing_readiness(tmp_path):
    from alphalab.research.workflow import ResearchWorkflow
    workflow=ResearchWorkflow(tmp_path/'workspace')
    draft=workflow.create_draft()
    draft['portfolio'].pop('delisting_policy',None)
    workflow._put('drafts',draft)
    updated=workflow.update_draft(draft['id'],draft['revision'],portfolio={'delisting_policy':'announcement-replace-v1'})
    assert updated['portfolio']['delisting_policy']=='announcement-replace-v1'
    workflow.close()
