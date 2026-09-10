import pandas as pd
import pytest


@pytest.mark.parametrize("bad_scale", [False, True])
def test_incompatible_local_adjustment_uses_cached_direct_hfq(tmp_path,monkeypatch,bad_scale):
    import duckdb
    from alphalab.research import data_binding, replacement_history
    from alphalab.research.replacement_inputs import prepare_replacement_data
    days=pd.bdate_range('2023-01-02',periods=6)
    rows=pd.DataFrame([dict(market='a_share',timeframe='1d',symbol='000001',ts=d,trade_date=d.date(),open=10.,high=12.,low=9.,close=11.,volume=1000.,amount=10000.,adjustment='qfq') for d in days])
    path=tmp_path/'source.duckdb'
    with duckdb.connect(str(path)) as con:
        con.register('rows',rows);con.execute('CREATE TABLE market_ohlcv AS SELECT * FROM rows')
    original=rows.iloc[:5].rename(columns={'ts':'date'}).copy()
    original[['open','high','low','close']]*=3
    original.loc[2,'close']=34 # Cannot explain all prices with the source ratio.
    original['adjustment']='hfq';original['tradestatus']=1
    full=rows.rename(columns={'ts':'date'}).copy();full[['open','high','low','close']]*=3
    full.loc[2,'close']=34;full['adjustment']='hfq';full['tradestatus']=1
    calls=[]
    def fetch(requests,cache_dir):
        calls.extend(requests)
        if bad_scale:
            full[['open','high','low','close']]*=2
        return {'000001':full}
    monkeypatch.setattr(replacement_history,'fetch_replacement_history',fetch)
    monkeypatch.setattr(data_binding,'default_research_db_candidates',lambda:())
    ready={'symbols':['000001'],'binding':{'sources':[{'db_path':str(path)}]},'dates':{'warmup_start_date':str(days[0].date()),'signal_date':str(days[4].date()),'exit_date':str(days[5].date())}}
    if bad_scale:
        with pytest.raises(ValueError,match="后复权.*不一致"):
            prepare_replacement_data(original,ready,cache_dir=tmp_path)
        return
    out,audit=prepare_replacement_data(original,ready,cache_dir=tmp_path)
    assert calls==[('000001',str(days[0].date()),str(days[5].date()))]
    assert len(out)==6
    assert out.adjustment.eq('hfq').all()
    assert out.loc[out.date.eq(days[2]),'close'].iloc[0]==34
    assert audit[0]['method']=='direct-provider-hfq-v1'


@pytest.mark.parametrize('defect', ['status', 'amount'])
def test_fallback_keeps_provider_repairs_on_existing_hfq_dates(tmp_path,monkeypatch,defect):
    from alphalab.research import data_binding, replacement_history
    from alphalab.research.replacement_inputs import prepare_replacement_data
    days=pd.bdate_range('2023-01-02',periods=6)
    good=pd.DataFrame([dict(symbol='000001',date=d,open=10.,high=12.,low=9.,close=11.,volume=1000.,amount=10000.,adjustment='hfq',tradestatus=1) for d in days])
    original=good.copy()
    if defect=='status':
        original.loc[5,['tradestatus','volume','amount']]=[float('nan'),0.,0.]
        good.loc[5,['tradestatus','volume','amount']]=[0,0.,0.]
    else:
        original.loc[5,'amount']=float('nan')
    monkeypatch.setattr(data_binding,'default_research_db_candidates',lambda:())
    monkeypatch.setattr(replacement_history,'fetch_replacement_history',lambda *args,**kwargs:{'000001':good})
    ready={'symbols':['000001'],'binding':{'sources':[]},'dates':{'warmup_start_date':str(days[0].date()),'signal_date':str(days[4].date()),'exit_date':str(days[5].date())}}
    out,_=prepare_replacement_data(original,ready,cache_dir=tmp_path)
    row=out.loc[out.date.eq(days[5])].iloc[0]
    assert row.tradestatus==good.iloc[-1].tradestatus
    assert row.amount==good.iloc[-1].amount
