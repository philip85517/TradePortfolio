"""Deterministic samples are fixtures, not evidence of provider coverage."""
import json

import pandas as pd
import pytest

from alphalab.research.engine import InMemoryMarketDataAdapter


def make_backend(tmp_path, drop=None):
    from alphalab.research.wizard_backend import WizardResearchBackend
    days = pd.bdate_range('2020-01-01', '2025-12-31')
    rows = [dict(market='a_share', symbol=s, date=d, open=10., high=11., low=9.,
                 close=10.5, volume=10000000., amount=100000000., adjustment='hfq',
                 name=s, listed_date='2010-01-01', delisted_date=None, industry_level1='银行')
            for s in ['000001', '600000'] for d in days if (s, str(d.date())) != drop]
    adapter = InMemoryMarketDataAdapter(pd.DataFrame(rows))
    calendar = lambda start, end: [d.date() for d in days if start <= d.date() <= end]
    return WizardResearchBackend(cache_dir=tmp_path, adapter=adapter, calendar=calendar), adapter


def scope(**changes):
    return dict(market='a_share', start_date='2021-01-02', end_date='2025-06-29',
                selection_mode='manual', symbols=['000001', '600000'], quality_mode='exploratory',
                rule_version='fixed_v0', top_n=2, **changes)


def portfolio(**changes):
    return dict(name='测试', initial_cash=100000., weighting='custom',
                weights={'000001': .3, '600000': .2}, commission_rate=.001,
                slippage_rate=.001, min_holdings=2, **changes)


def test_weekend_bounds_and_full_multiyear_manual_run(tmp_path):
    backend, _ = make_backend(tmp_path)
    ready = backend.inspect(scope())
    assert ready['status'] == 'READY', ready
    assert ready['dates']['entry_date'] == '2021-01-04'
    assert ready['dates']['exit_date'] == '2025-06-27'
    assert ready['dates']['signal_date'] == '2021-01-01'
    preview = backend.preview(scope(), portfolio(), ready)
    assert preview['cash_residual'] > 50000
    assert [r['target_weight'] for r in preview['holdings']] == [.3, .2]
    result = backend.run(scope(), portfolio(), ready, tmp_path / 'runs')
    assert result['summary']['status'] == 'COMPLETE'
    assert result['summary']['evaluated_date'] == '2025-06-27'
    assert result['manifest']['spec']['wizard_metadata']['scope'] == scope()
    json.dumps(result, allow_nan=False)


def test_per_stock_gap_is_blocker_despite_global_bounds(tmp_path):
    backend, _ = make_backend(tmp_path, ('600000', '2023-06-01'))
    ready = backend.inspect(scope())
    assert ready['status'] == 'BLOCKED'
    assert any(i['code'] == 'MISSING_BARS' and '600000' in i['message'] for i in ready['issues'])


def test_strict_requires_historical_industry_not_current_labels(tmp_path):
    backend, _ = make_backend(tmp_path)
    request = scope(); request['quality_mode'] = 'strict'
    ready = backend.inspect(request)
    assert ready['status'] == 'BLOCKED'
    assert any(i['code'] == 'PIT_UNAVAILABLE' for i in ready['issues'])


def test_data_change_rejects_readiness(tmp_path):
    backend, adapter = make_backend(tmp_path)
    ready = backend.inspect(scope())
    adapter.bars.loc[0:, 'close'] = 10.6
    with pytest.raises(ValueError, match='DATA_CHANGED'):
        backend.run(scope(), portfolio(), ready, tmp_path / 'runs')


def test_rule_warmup_is_per_symbol(tmp_path):
    backend, adapter = make_backend(tmp_path)
    adapter.bars = adapter.bars[~((adapter.bars.symbol == '600000') & (adapter.bars.date < '2020-12-01'))]
    request = scope(); request['selection_mode'] = 'rule'
    ready = backend.inspect(request)
    assert ready['status'] == 'BLOCKED'
    assert any(i['code'] in {'MISSING_BARS', 'PIT_UNAVAILABLE'} for i in ready['issues'])


@pytest.mark.parametrize('start,end', [('2025-01-02','2021-01-02'),('2021-01-04','2021-01-04')])
def test_invalid_interval_blocks(tmp_path, start, end):
    backend, _ = make_backend(tmp_path)
    request=scope(); request.update(start_date=start,end_date=end)
    assert backend.inspect(request)['status'] == 'BLOCKED'


def test_custom_overweight_rejected(tmp_path):
    backend, _ = make_backend(tmp_path)
    config=portfolio(); config['weights']={'000001':.8,'600000':.8}
    with pytest.raises(ValueError, match='权重'):
        backend.preview(scope(),config,backend.inspect(scope()))


def test_preview_cash_and_run_equity_include_buy_slippage(tmp_path):
    backend, _ = make_backend(tmp_path)
    ready=backend.inspect(scope())
    preview=backend.preview(scope(),portfolio(),ready)
    spent=sum(row['entry_price'] * row['shares'] * 1.001 for row in preview['holdings'])
    assert preview['cash_residual'] == pytest.approx(100000-spent)
    result=backend.run(scope(),portfolio(),ready,tmp_path/'runs')
    assert result['summary']['cash_residual'] == pytest.approx(preview['cash_residual'])


def test_prepare_fills_only_missing_symbol_using_provider_and_persists_cache(tmp_path):
    backend, adapter=make_backend(tmp_path, ('600000','2023-06-01'))
    from etf_strategy.src.market_data_store import normalize_bars
    class Provider:
        def fetch_ohlcv(self, request):
            assert request.symbol == '600000'
            return normalize_bars(pd.DataFrame([dict(market='a_share',symbol=request.symbol,timeframe='1d',
                ts=request.start,open=10.,high=11.,low=9.,close=10.5,volume=10000000.,amount=100000000.,
                adjustment='hfq',adjusted=True,source='deterministic-fixture')]))
    backend.provider=Provider()
    result=backend.prepare(scope(),lambda message: None,lambda:False)
    assert result['status']=='READY',result
    assert backend.inspect(scope())['status']=='READY'


def test_rule_scoring_uses_signal_history_and_complete_interval(tmp_path):
    backend, adapter=make_backend(tmp_path)
    def history(as_of, market, symbols):
        return pd.DataFrame([dict(symbol=s,name=s,snapshot_id='fixture',effective_from='2010-01-01',effective_to=None,
            industry_level1='银行',industry_level2='银行',industry_level3='银行') for s in ['000001','600000']])
    adapter.load_universe_as_of=history
    adapter.bars['close'] = 10 + (adapter.bars.date-pd.Timestamp('2020-01-01')).dt.days * .0001
    adapter.bars['high'] = 12.
    request=scope();request['selection_mode']='rule';request['quality_mode']='strict'
    config=portfolio();config['weighting']='equal'
    ready=backend.inspect(request)
    assert ready['status']=='READY',ready
    preview=backend.preview(request,config,ready)
    assert len(preview['holdings'])==2
    result=backend.run(request,config,ready,tmp_path/'runs')
    assert result['summary']['evaluated_date']=='2025-06-27'


def test_saved_review_uses_frozen_price_snapshot(tmp_path):
    from pathlib import Path
    from alphalab.research.engine import DuckDBMarketDataAdapter
    backend, adapter=make_backend(tmp_path)
    result=backend.run(scope(),portfolio(),backend.inspect(scope()),tmp_path/'runs')
    path=Path(result['manifest']['diagnostics']['data_source']['db_path'])
    assert path.parent.name==result['run_id']
    adapter.bars['close']=20.
    saved=DuckDBMarketDataAdapter(path).load(pd.Timestamp('2021-01-04').date(),pd.Timestamp('2021-01-05').date())
    assert saved.close.eq(10.5).all()
    assert 'market_data.duckdb' in result['manifest']['artifact_hashes']


def test_bad_price_can_be_repaired_by_prepare(tmp_path):
    backend, adapter=make_backend(tmp_path)
    mask=adapter.bars.symbol.eq('600000') & adapter.bars.date.eq('2023-06-01')
    adapter.bars.loc[mask,'close']=-1.
    from etf_strategy.src.market_data_store import normalize_bars
    class Provider:
        def fetch_ohlcv(self, request):
            rows=adapter.bars[adapter.bars.symbol.eq(request.symbol) & adapter.bars.date.between(request.start,request.end)].copy()
            rows['close']=10.5;rows['ts']=rows.date;rows['timeframe']='1d';rows['adjusted']=True
            return normalize_bars(rows)
    backend.provider=Provider()
    assert backend.inspect(scope())['status']=='BLOCKED'
    assert backend.prepare(scope(),lambda _:None,lambda:False)['status']=='READY'


def test_prepare_cancellation_preserves_scope(tmp_path):
    backend,_=make_backend(tmp_path)
    request=scope()
    with pytest.raises(InterruptedError):
        backend.prepare(request,lambda _:None,lambda:True)
    assert request==scope()


def test_adjusted_series_is_anchored_to_actual_entry_open_for_lot_sizing(tmp_path):
    backend, adapter=make_backend(tmp_path)
    for column in ('open','high','low','close'):
        adapter.bars[column] *= 100
    adapter.bars['execution_open']=10.
    ready=backend.inspect(scope())
    preview=backend.preview(scope(),portfolio(),ready)
    assert preview['holdings'][0]['entry_price']==pytest.approx(10.01)
    assert preview['holdings'][0]['shares']==2900


def rule_backend(tmp_path):
    backend, adapter = make_backend(tmp_path)
    adapter.bars['close'] = 10 + (adapter.bars.date-pd.Timestamp('2020-01-01')).dt.days*.0001
    adapter.bars['high'] = 12.
    history = pd.DataFrame([dict(symbol=s, name=s, snapshot_id='fixture', effective_from='2010-01-01',
        effective_to=None, source='historical-industry-fixture', industry_level1='银行',
        industry_level2='银行', industry_level3='银行') for s in ['000001','600000']])
    adapter.load_universe_as_of=lambda *args: history.copy()
    request=scope();request.update(selection_mode='rule', top_n=1, quality_mode='strict')
    return backend, adapter, history, request


def test_rule_new_listing_is_ineligible_without_requesting_prelisting_bars(tmp_path):
    backend, adapter, history, request = rule_backend(tmp_path)
    adapter.bars.loc[adapter.bars.symbol.eq('600000'), 'listed_date']='2020-12-01'
    adapter.bars=adapter.bars[~(adapter.bars.symbol.eq('600000') & adapter.bars.date.lt('2020-12-01'))]
    ready=backend.inspect(request)
    assert ready['status']=='READY',ready['issues']
    config=portfolio();config.update(weighting='equal',min_holdings=1)
    preview=backend.preview(request,config,ready)
    assert [h['symbol'] for h in preview['holdings']]==['000001']
    assert not next(c for c in preview['candidates'] if c['symbol']=='600000')['eligible']


def test_rule_unselected_delisting_does_not_block_selected_holdings(tmp_path):
    backend, adapter, history, request = rule_backend(tmp_path)
    adapter.bars.loc[adapter.bars.symbol.eq('600000'),'delisted_date']='2023-01-01'
    adapter.bars=adapter.bars[~(adapter.bars.symbol.eq('600000') & adapter.bars.date.ge('2023-01-01'))]
    ready=backend.inspect(request)
    assert ready['status']=='READY',ready['issues']
    config=portfolio();config.update(weighting='equal',min_holdings=1)
    result=backend.run(request,config,ready,tmp_path/'runs')
    assert result['summary']['status']=='COMPLETE'
    assert result['manifest']['spec']['wizard_metadata']['benchmark_disabled_reason']


def test_rule_selected_forward_gap_still_blocks(tmp_path):
    backend, adapter, history, request = rule_backend(tmp_path)
    adapter.bars=adapter.bars[~(adapter.bars.symbol.eq('000001') & adapter.bars.date.eq('2023-01-02'))]
    ready=backend.inspect(request)
    assert ready['status']=='BLOCKED'
    assert any(i['code']=='MISSING_BARS' and '000001' in i['message'] for i in ready['issues'])


def test_historical_industry_change_after_signal_is_not_delisting(tmp_path):
    backend, adapter, history, request = rule_backend(tmp_path)
    history.loc[history.symbol.eq('000001'),'effective_to']='2022-01-01'
    assert backend.inspect(request)['status']=='READY'


def test_rule_tie_ranking_is_frozen_before_execution_price_scaling(tmp_path):
    backend, adapter, history, request = rule_backend(tmp_path)
    adapter.bars['execution_open']=7.
    ready=backend.inspect(request)
    assert ready['selected_symbols']==['000001']
    config=portfolio();config.update(weighting='equal',min_holdings=1)
    preview=backend.preview(request,config,ready)
    assert [r['symbol'] for r in preview['holdings']]==['000001']
    result=backend.run(request,config,ready,tmp_path/'runs')
    assert result['summary']['status']=='COMPLETE'


def test_frozen_rule_keeps_historical_industry_for_constraints(tmp_path):
    backend, adapter, history, request=rule_backend(tmp_path)
    request['top_n']=2
    history.loc[history.symbol.eq('600000'),'industry_level1']='能源'
    config=portfolio();config.update(weighting='equal',min_holdings=2,max_industry_weight=.5)
    ready=backend.inspect(request)
    preview=backend.preview(request,config,ready)
    assert {h['industry'] for h in preview['holdings']}=={'银行','能源'}


def test_existing_history_sidecar_reused_over_listing_only_cache(tmp_path, monkeypatch):
    from alphalab.research import data_binding
    from alphalab.research.wizard_backend import WizardResearchBackend
    from alphalab.research.universe_history import upsert_universe_history
    from etf_strategy.src.market_data_store import upsert_bars
    fixture, adapter, history, request=rule_backend(tmp_path)
    bars=adapter.bars.copy();bars['ts']=bars.date;bars['timeframe']='1d';bars['adjusted']=True
    database=tmp_path/'prices.duckdb';upsert_bars(bars,database)
    history['market']='a_share';history['status']='active'
    historical=tmp_path/'existing-history.duckdb'
    upsert_universe_history(history,historical)
    cache=tmp_path/'cache';cache.mkdir()
    listing_only=history.copy();listing_only['source']='baostock'
    for c in ['industry_level1','industry_level2','industry_level3']:listing_only[c]=None
    upsert_universe_history(listing_only,cache/'universe.duckdb')
    monkeypatch.setattr(data_binding,'default_research_universe_cache_path',lambda:historical)
    monkeypatch.setattr(data_binding,'default_research_industry_cache_path',lambda:tmp_path/'absent.duckdb')
    (cache/'execution_prices.json').write_text(json.dumps({'000001:2021-01-04':10.,'600000:2021-01-04':10.}))
    backend=WizardResearchBackend(database,cache_dir=cache,calendar=fixture.calendar)
    ready=backend.inspect(request)
    assert ready['status']=='READY',ready['issues']
    assert any(str(historical)==s.get('universe_db_path') for s in ready['binding']['sources'])
