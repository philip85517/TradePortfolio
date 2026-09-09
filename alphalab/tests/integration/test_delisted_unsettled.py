import json
import pandas as pd
from alphalab.tests.integration.test_wizard_backend import make_backend,scope,portfolio


def test_delisted_holding_remains_unsettled_without_invented_sale_or_total_return(tmp_path):
    backend,adapter=make_backend(tmp_path)
    adapter.bars.loc[adapter.bars.symbol.eq('600000'),'delisted_date']='2024-01-02'
    adapter.bars=adapter.bars[~(adapter.bars.symbol.eq('600000') & adapter.bars.date.ge('2024-01-02'))]
    ready=backend.inspect(scope())
    assert ready['status']=='READY',ready['issues']
    assert any(i['code']=='DELISTED_UNSETTLED' and i['severity']=='warning' for i in ready['issues'])
    result=backend.run(scope(),portfolio(),ready,tmp_path/'runs')
    r=result['summary']
    assert r['status']=='UNSETTLED'
    assert r['total_return'] is None and r['ending_equity'] is None
    assert r['liquidation_status']=='UNSETTLED_DELISTING'
    assert r['open_positions']['600000']>0
    assert r['realized_cash']>50000
    assert r['known_assets_value']==r['realized_cash']
    assert r['unsettled_symbols']==['600000']
    assert r['evaluated_date']=='2025-06-27'
    json.dumps(result,allow_nan=False)


def test_missing_bar_before_delisting_still_blocks(tmp_path):
    backend,adapter=make_backend(tmp_path,('600000','2023-06-01'))
    adapter.bars.loc[adapter.bars.symbol.eq('600000'),'delisted_date']='2024-01-02'
    adapter.bars=adapter.bars[~(adapter.bars.symbol.eq('600000') & adapter.bars.date.ge('2024-01-02'))]
    assert backend.inspect(scope())['status']=='BLOCKED'


def test_all_holdings_delisted_still_evaluate_requested_calendar_without_fake_cash(tmp_path):
    backend,adapter=make_backend(tmp_path)
    adapter.bars['delisted_date']='2024-01-02'
    adapter.bars=adapter.bars[adapter.bars.date.lt('2024-01-02')]
    r=backend.run(scope(),portfolio(),backend.inspect(scope()),tmp_path/'runs')['summary']
    assert r['status']=='UNSETTLED'
    assert r['evaluated_date']=='2025-06-27'
    assert set(r['unsettled_symbols'])=={'000001','600000'}
    assert r['realized_cash']==r['cash_residual']
    assert r['realized_profit_loss']==0
    assert r['commission_paid']>0
    assert r['unrealized_holdings_value'] is None
