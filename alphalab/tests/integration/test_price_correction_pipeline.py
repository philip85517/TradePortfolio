import pandas as pd
import pytest


def test_provider_validation_checks_corrected_prices_and_records_provenance():
    from alphalab.research.replacement_history import _normalize
    raw=pd.DataFrame([dict(code='sh.600321',date=d,open=p,high=p,low=p,close=p,volume=1000,amount=10000,adjustflag='1',tradestatus='1',pctChg=change) for d,p,change in [('2022-10-21',10.93700102,'0'),('2022-10-24',13.7467575,'-3.3149')]])
    result=_normalize(raw,('600321','2022-10-21','2022-10-24'))
    assert result.close.iloc[1]/result.close.iloc[0]-1==pytest.approx(1.75/1.81-1)
    assert result.source_correction_id.iloc[1]=='baostock-600321-hfq-20221024-factor-v1'
    assert raw.close.iloc[1]==13.7467575


def test_wizard_recomputes_corrected_inputs_without_overwriting_source(tmp_path):
    from alphalab.tests.integration.test_wizard_backend import make_backend,scope
    backend,adapter=make_backend(tmp_path)
    adapter.bars.loc[adapter.bars.symbol.eq('600000'),'symbol']='600321'
    adapter.bars['source']='baostock'
    affected=adapter.bars.symbol.eq('600321') & adapter.bars.date.ge('2022-10-24')
    adapter.bars.loc[affected,['open','high','low','close']]*=7.855290/6.042542
    original=adapter.bars.loc[affected,'close'].iloc[0]
    request=scope();request['symbols']=['600321']
    ready=backend.inspect(request)
    assert ready['status']=='READY',ready['issues']
    assert ready['price_corrections'][0]['symbol']=='600321'
    assert any(i['code']=='SOURCE_PRICE_CORRECTION' and i['severity']=='warning' for i in ready['issues'])
    _,view,_=backend._inspect(request)
    assert view.loc[view.date.eq('2022-10-24'),'close'].iloc[0]==pytest.approx(10.5)
    assert adapter.bars.loc[affected,'close'].iloc[0]==original


def test_forward_only_correction_is_included_in_frozen_spec(tmp_path,monkeypatch):
    import numpy as np
    from alphalab.research import replacement_inputs
    from alphalab.research.price_corrections import apply_verified_price_corrections
    from alphalab.tests.integration.test_wizard_backend import make_backend,scope,portfolio
    backend,adapter=make_backend(tmp_path)
    adapter.bars.loc[adapter.bars.symbol.eq('600000'),'symbol']='600321'
    adapter.load_universe_as_of=lambda *args: pd.DataFrame([dict(symbol=s,listed_date='2010-01-01',effective_from='2010-01-01',effective_to=None,source='fixture',snapshot_id='v1') for s in ['000001','600321']])
    for _,part in adapter.bars.groupby('symbol'):
        values=np.linspace(10,40,len(part))
        adapter.bars.loc[part.index,['open','close']]=np.column_stack([values,values])
        adapter.bars.loc[part.index,'high']=values+1
        adapter.bars.loc[part.index,'low']=values-1
    request=scope();request.update(selection_mode='rule',symbols=[])
    settings=portfolio(delisting_policy='announcement-replace-v1');settings['weighting']='equal'
    ready=backend.inspect(request)
    assert ready['status']=='READY',ready['issues']
    assert not ready['price_corrections']
    def supplement(data,*args,**kwargs):
        data=data.copy()
        mask=data.symbol.eq('600321') & data.date.ge('2022-10-24')
        data.loc[mask,['open','high','low','close']]*=7.855290/6.042542
        return apply_verified_price_corrections(data,provider='baostock'),[]
    monkeypatch.setattr(replacement_inputs,'prepare_replacement_data',supplement)
    current,frozen=backend._freeze(request,ready,settings)
    spec=backend._spec(request,settings,current)
    assert spec.wizard_metadata['price_corrections'][0]['symbol']=='600321'
