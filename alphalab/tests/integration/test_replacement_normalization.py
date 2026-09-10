import pandas as pd
import pytest
from alphalab.research.replacement_inputs import normalize_replacement_bars


def _ranking_fixture(tmp_path):
    from types import SimpleNamespace
    days = pd.bdate_range('2025-01-02', periods=120)
    bars = pd.DataFrame([
        dict(symbol='600000', date=day, open=10+i*.1, high=11+i*.1,
             low=9+i*.1, close=10+i*.1, amount=50_000_000., volume=1000.,
             adjustment='hfq', tradestatus=1, listed_date='2000-01-01', delisted_date=None)
        for i, day in enumerate(days)
    ])
    backend = SimpleNamespace(cache_dir=tmp_path,
                              _calendar_dates=lambda start, end: list(days.date))
    return bars, backend


def _rank_fixture(bars, backend):
    from types import SimpleNamespace
    from alphalab.research.replacement_inputs import attach_replacement_inputs
    from alphalab.research.suspension_policy import prepare_view
    cutoff = bars.date.max()
    bars = prepare_view(bars, cutoff)
    frozen = SimpleNamespace(bars=bars)
    attach_replacement_inputs(frozen, bars, pd.DataFrame(), backend, {})
    return frozen.replacement_rank(cutoff, set())


@pytest.mark.parametrize('column', ['open', 'high', 'low', 'close'])
@pytest.mark.parametrize('value', [float('nan'), float('inf'), 0.])
def test_rank_invalid_trading_prices_fail_instead_of_filtering(tmp_path, column, value):
    bars, backend = _ranking_fixture(tmp_path)
    bars.loc[119, column] = value
    with pytest.raises(ValueError, match=r'REPLACEMENT_DATA_INVALID.*600000.*2025-06-18'):
        _rank_fixture(bars, backend)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -1.])
def test_rank_missing_turnover_in_lookback_fails_instead_of_skipping(tmp_path, value):
    bars, backend = _ranking_fixture(tmp_path)
    bars.loc[110, 'amount'] = value
    day = str(bars.loc[110, 'date'].date())
    with pytest.raises(ValueError, match=rf'REPLACEMENT_DATA_INVALID.*600000.*{day}'):
        _rank_fixture(bars, backend)


@pytest.mark.parametrize('index', [110, 119])
def test_rank_confirmed_suspension_uses_existing_valuation_policy(tmp_path, index):
    bars, backend = _ranking_fixture(tmp_path)
    bars.loc[index, 'tradestatus'] = 0
    bars.loc[index, ['open', 'high', 'low', 'close', 'amount', 'volume']] = float('nan')
    assert _rank_fixture(bars, backend) == ([] if index == 119 else ['600000'])


def test_verified_overlap_normalizes_ohlc_not_turnover():
    dates=pd.bdate_range('2023-01-01',periods=6)
    q=pd.DataFrame([dict(symbol='A',date=d,open=10.,high=12.,low=9.,close=11.,amount=500.,adjustment='qfq') for d in dates])
    h=q.iloc[:5].copy();h[['open','high','low','close']]*=3;h['adjustment']='hfq'
    mixed=pd.concat([h,q.iloc[5:]],ignore_index=True)
    result,audit=normalize_replacement_bars(mixed,q)
    assert result.iloc[-1].close==33
    assert result.amount.eq(500).all()
    assert result.adjustment.eq('hfq').all()
    assert audit[0]['factor']==3
    q.loc[0,'close']=20
    with pytest.raises(ValueError,match='复权'):
        normalize_replacement_bars(mixed,q)


def test_missing_rank_day_is_data_error_not_no_candidate(tmp_path):
    from types import SimpleNamespace
    from alphalab.research.replacement_inputs import attach_replacement_inputs
    from alphalab.tests.integration.test_wizard_backend import make_backend
    backend, adapter=make_backend(tmp_path)
    data=adapter.bars[adapter.bars.date.lt('2025-06-13')].copy()
    frozen=SimpleNamespace(bars=data)
    history=pd.DataFrame([dict(symbol=s,listed_date='2010-01-01',delisted_date=None,snapshot_id='fixture',source='fixture') for s in ['000001','600000']])
    attach_replacement_inputs(frozen,data,history,backend,{})
    with pytest.raises(ValueError,match='替补排名.*缺少'):
        frozen.replacement_rank(pd.Timestamp('2025-06-13'),set())


def test_supplemental_prices_preserve_verified_suspension(tmp_path,monkeypatch):
    import duckdb
    from alphalab.research.replacement_inputs import prepare_replacement_data
    from alphalab.research import data_binding
    days=pd.bdate_range('2023-01-02',periods=6)
    rows=pd.DataFrame([dict(market='a_share',timeframe='1d',symbol='000001',ts=d,trade_date=d.date(),open=10.,high=12.,low=9.,close=11.,volume=1000.,amount=10000.,adjustment='qfq') for d in days])
    path=tmp_path/'source.duckdb'
    con=duckdb.connect(str(path));con.register('rows',rows);con.execute('CREATE TABLE market_ohlcv AS SELECT * FROM rows');con.close()
    original=rows.rename(columns={'ts':'date'}).copy();original.loc[:4,['open','high','low','close']]*=3;original.loc[:4,'adjustment']='hfq';original['tradestatus']=1;original.loc[5,'tradestatus']=0
    monkeypatch.setattr(data_binding,'default_research_db_candidates',lambda:())
    ready={'symbols':['000001'],'binding':{'sources':[{'db_path':str(path)}]},'dates':{'warmup_start_date':str(days[0].date()),'signal_date':str(days[4].date()),'exit_date':str(days[5].date())}}
    result,_=prepare_replacement_data(original,ready)
    assert result.loc[result.date.eq(days[5]),'tradestatus'].iloc[0]==0


def test_rounding_intervals_prove_multiplier_without_relaxing_bad_ratios():
    import numpy as np
    days=pd.bdate_range('2023-01-01',periods=6)
    exact=np.array([1.453,1.468,1.481,1.493,1.507,1.511])
    q=pd.DataFrame([dict(symbol='A',date=d,open=round(v,2),high=round(v+.012,2),low=round(v-.012,2),close=round(v+.003,2),amount=500.,adjustment='qfq') for d,v in zip(days,exact)])
    h=q.iloc[:5].copy()
    for c,offset in [('open',0),('high',.012),('low',-.012),('close',.003)]: h[c]=(exact[:5]+offset)*45
    h['adjustment']='hfq'
    out,audit=normalize_replacement_bars(pd.concat([h,q.iloc[5:]],ignore_index=True),q)
    assert out.adjustment.eq('hfq').all()
    assert audit[0]['qfq_rounding_unit']==.01
    assert audit[0]['factor_interval_low']<=45<=audit[0]['factor_interval_high']


def test_wide_rounding_interval_does_not_prove_usable_conversion():
    days=pd.bdate_range('2023-01-01',periods=6)
    q=pd.DataFrame([dict(symbol='A',date=d,open=.01,high=.01,low=.01,close=.01,amount=500.,adjustment='qfq') for d in days])
    h=q.iloc[:5].copy();h[['open','high','low','close']]=100.;h['adjustment']='hfq'
    with pytest.raises(ValueError,match='换算比例.*精度'):
        normalize_replacement_bars(pd.concat([h,q.iloc[5:]],ignore_index=True),q)


def test_small_provider_factor_rounding_across_corporate_action_is_audited():
    days=pd.bdate_range('2023-01-01',periods=6)
    q=pd.DataFrame([dict(symbol='A',date=d,open=10.12345678,high=12.12345678,low=9.12345678,close=11.12345678,amount=500.,adjustment='qfq') for d in days])
    h=q.iloc[:5].copy();h[['open','high','low','close']]*=4.5825556467
    h.loc[2:,['open','high','low','close']]*=4.5825523084/4.5825556467
    h['adjustment']='hfq'
    out,audit=normalize_replacement_bars(pd.concat([h,q.iloc[5:]],ignore_index=True),q)
    assert out.adjustment.eq('hfq').all()
    assert audit[0]['factor_rounding_relative_tolerance']==1e-5
    h.loc[2,'close']*=1.001
    with pytest.raises(ValueError,match='一致换算比例'):
        normalize_replacement_bars(pd.concat([h,q.iloc[5:]],ignore_index=True),q)
