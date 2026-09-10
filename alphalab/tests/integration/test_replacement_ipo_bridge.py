import pandas as pd
import pytest
from alphalab.research.replacement_inputs import prepare_replacement_data


def fixture(tmp_path,monkeypatch,n=3,*,mature=False,missing=False,mismatch=False,verified_history=True):
    from alphalab.research import replacement_history
    days=pd.bdate_range('2022-12-28',periods=8)
    direct=pd.DataFrame([dict(symbol='001301',date=d,open=10.,high=11.,low=9.,close=10.,volume=1000.,amount=10000.,tradestatus=1,adjustment='hfq') for d in days])
    original=direct.iloc[:n].copy()
    original['tradestatus']=None
    original['amount']=float('nan')
    if missing:
        original=original.iloc[1:]
    if mismatch:
        direct.loc[0,'close']=10.1
    monkeypatch.setattr(replacement_history,'fetch_replacement_history',lambda *a,**kw:{'001301':direct})
    readiness={'symbols':['001301'],'binding':{'sources':[{'source':'injected-adapter'}]},'dates':{'warmup_start_date':'2022-07-08','signal_date':str(days[n-1].date()),'exit_date':str(days[-1].date())}}
    history=pd.DataFrame([dict(symbol='001301',listed_date='2020-01-01' if mature else str(days[0].date()),source='baostock')]) if verified_history else None
    return prepare_replacement_data(original,readiness,cache_dir=tmp_path,history=history,sessions=days)


@pytest.mark.parametrize('n',[1,2,3,4])
def test_complete_verified_ipo_initial_window_bridges_direct_hfq(tmp_path,monkeypatch,n):
    result,audit=fixture(tmp_path,monkeypatch,n)
    assert len(result)==8
    assert audit[0]['overlap_days']==n
    assert audit[0]['overlap_requirement']=='complete_initial_ipo_window'


@pytest.mark.parametrize('kwargs',[{'mature':True},{'missing':True},{'verified_history':False}])
def test_short_bridge_never_relaxes_mature_unknown_or_missing_initial_day(tmp_path,monkeypatch,kwargs):
    with pytest.raises(ValueError,match='001301.*5日'):
        fixture(tmp_path,monkeypatch,**kwargs)


def test_ipo_bridge_still_requires_matching_ohlc(tmp_path,monkeypatch):
    with pytest.raises(ValueError,match='001301.*价格不一致'):
        fixture(tmp_path,monkeypatch,mismatch=True)
