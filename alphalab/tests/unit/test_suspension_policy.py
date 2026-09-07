from dataclasses import replace
from datetime import date

import pandas as pd
import pytest

from alphalab.research import engine


def bars():
    return pd.DataFrame([
        dict(symbol=s, date=d, open=p, high=p, low=p, close=p, amount=1e8, volume=1000, adjustment='hfq', tradestatus=status)
        for s in ['A', 'B']
        for d, p, status in zip(pd.date_range('2024-01-01', periods=4), [10, 11, 999, 999] if s == 'A' else [10, 11, 12, 13], ['1', '1', '0', '0'] if s == 'A' else ['1'] * 4)
    ])


def selected():
    return pd.DataFrame([dict(symbol=s, name=s, industry='X', rank=i+1, total_score=2-i) for i, s in enumerate(['A', 'B'])])


def phase_spec(**kwargs):
    assert 'suspension_policy' in engine.ResearchSpec.__dataclass_fields__, 'versioned opt-in policy is missing'
    return engine.ResearchSpec('2024-01-01', suspension_policy='phase-aware-v1', lot_size=1, initial_cash=1000, **kwargs)


def test_policy_view_preserves_raw_and_only_carries_reliable_valuation():
    assert hasattr(engine, 'prepare_view'), 'suspension research view is missing'
    raw = bars()
    raw.loc[raw.tradestatus.eq('0'), ['volume', 'amount']] = float('nan')
    view = engine.prepare_view(raw, date(2024, 1, 3))
    assert raw.loc[2, 'amount'] != raw.loc[2, 'amount']
    assert view.loc[2, 'amount'] == 0
    assert view.loc[2, 'open'] == 999
    assert view.loc[3, 'valuation_close'] == 11
    assert view.loc[3, 'valuation_stale_days'] == 2


def test_signal_eligibility_does_not_use_future_suspension():
    assert hasattr(engine, 'apply_candidate_policy'), 'signal eligibility policy is missing'
    raw = bars()
    candidates = selected().assign(eligible=True, reason='ok')
    first = engine.apply_candidate_policy(candidates, raw, date(2024, 1, 2), min_effective_samples=1)
    assert first.eligible.all()
    last = engine.apply_candidate_policy(candidates, raw, date(2024, 1, 3), min_effective_samples=1)
    assert not last.set_index('symbol').loc['A', 'eligible']
    insufficient = engine.apply_candidate_policy(candidates, raw, date(2024, 1, 2), min_effective_samples=3)
    assert not insufficient.eligible.any()


@pytest.mark.parametrize('weighting,weights,expected', [('equal', {}, .5), ('score', {}, 1/3), ('custom', {'A': .2, 'B': .6}, .6)])
def test_suspended_entry_keeps_assigned_weight_in_cash(weighting, weights, expected):
    spec = phase_spec(portfolio_weighting=weighting, target_weights=weights)
    portfolio, status, reasons = engine._build_portfolio(selected(), bars(), date(2024, 1, 3), spec)
    assert status == 'OK'
    assert list(portfolio.symbol) == ['B']
    assert portfolio.iloc[0].target_weight == pytest.approx(expected)
    assert '停牌' in reasons['A']
    _, status, _ = engine._build_portfolio(selected(), bars(), date(2024, 1, 3), replace(spec, min_holdings=2))
    assert status == 'INSUFFICIENT_HOLDINGS'


def test_suspended_holding_and_exit_value_without_sale_costs():
    spec = phase_spec(commission_rate=.01, slippage_rate=.02)
    portfolio, _, _ = engine._build_portfolio(selected(), bars(), date(2024, 1, 2), spec)
    performance, nav = engine._evaluate_forward(portfolio, bars(), date(2024, 1, 2), (2,), spec)
    result = performance[2]
    assert result.status == 'COMPLETE'
    assert result.liquidation_status == 'OPEN_POSITION'
    shares = portfolio.set_index('symbol').shares
    assert result.unrealized_holdings_value == pytest.approx(shares['A'] * 11)
    assert result.commission_paid == pytest.approx((portfolio.entry_price * portfolio.shares).sum() * .01 + shares['B'] * 13 * .98 * .01)
    assert nav.iloc[-1].stale_symbols == 'A'
    assert nav.iloc[-1].max_valuation_stale_days == 2
    assert result.ending_equity == pytest.approx(result.realized_cash + result.unrealized_holdings_value)


def test_legacy_policy_remains_default():
    spec = engine.ResearchSpec('2024-01-01', initial_cash=10000, lot_size=1)
    assert getattr(spec, 'suspension_policy', None) == 'legacy'
    portfolio, _, _ = engine._build_portfolio(selected(), bars(), date(2024, 1, 3), spec)
    assert set(portfolio.symbol) == {'A', 'B'}


def test_missing_suspension_ohlc_counts_calendar_liquidity_and_passes_strict_quality():
    dates = pd.bdate_range('2024-01-01', periods=120)
    data = pd.DataFrame([dict(symbol='A', date=d, open=10+i*.1, high=11+i*.1, low=9+i*.1,
        close=10+i*.1, volume=1000, amount=4e7, adjustment='hfq', tradestatus='1') for i, d in enumerate(dates)])
    data.loc[118, ['open', 'high', 'low', 'close', 'amount', 'volume']] = float('nan')
    data.loc[118, 'tradestatus'] = '0'
    view = engine.prepare_view(data, dates[-1])
    candidates, _ = engine._score_fixed_v0(view)
    assert candidates.iloc[0].amount_20d == pytest.approx(4e7 * 19 / 20)
    assert candidates.iloc[0].history_count == 120
    quality = engine._quality_summary(view, dates[-1].date())
    engine._validate_quality_mode(quality, 'strict')
    assert pd.isna(view.loc[118, 'open'])


def test_unknown_missing_close_is_not_carried_and_adjustment_basis_cannot_cross():
    raw = bars()
    raw.loc[2, 'adjustment'] = 'qfq'
    raw.loc[6, 'close'] = float('nan')
    raw.loc[6, 'tradestatus'] = None
    view = engine.prepare_view(raw, date(2024, 1, 1))
    assert pd.isna(view.loc[2, 'valuation_close'])
    assert pd.isna(view.loc[6, 'valuation_close'])
    raw.loc[2, 'adjustment'] = 'hfq'
    raw.loc[2, 'tradestatus'] = None
    raw.loc[2, ['close', 'amount', 'volume']] = float('nan')
    spec = phase_spec()
    portfolio, _, _ = engine._build_portfolio(selected(), raw, date(2024, 1, 2), spec)
    performance, _ = engine._evaluate_forward(portfolio, raw, date(2024, 1, 2), (2,), spec)
    assert performance[2].status == 'INSUFFICIENT_FORWARD_DATA'


def test_candidate_policy_equivalence_for_mixed_history_and_duplicate_days():
    data = bars().astype({'close': float})
    data.loc[0, 'amount'] = float('nan')
    data.loc[1, 'volume'] = float('nan')
    data.loc[4, 'close'] = float('inf')
    data.loc[5, ['volume', 'amount']] = 0
    # Last duplicate is authoritative; a future duplicate cannot change signal eligibility.
    data = pd.concat([data, data.iloc[[2]].assign(tradestatus='1', volume=500, amount=10000),
                      data.iloc[[6]].assign(tradestatus='0'),
                      data.iloc[[3]].assign(tradestatus='1')], ignore_index=True)
    candidates = selected().assign(eligible=True, reason='ok')
    result = engine.apply_candidate_policy(candidates, data, date(2024, 1, 3), min_effective_samples=2).set_index('symbol')
    assert result.effective_traded_samples.to_dict() == {'A': 3, 'B': 0}
    assert result.eligible.to_dict() == {'A': True, 'B': False}
    assert result.loc['A', 'reason'] == 'ok'
    assert result.loc['B', 'reason'] == 'ok；信号日停牌；有效交易样本不足2日'
    pd.testing.assert_frame_equal(candidates, selected().assign(eligible=True, reason='ok'))


def test_view_leaves_unsuspended_symbol_values_unchanged():
    raw = bars()
    view = engine.prepare_view(raw, date(2024, 1, 1))
    active = raw.symbol.eq('B')
    pd.testing.assert_series_equal(view.loc[active, 'valuation_close'], raw.loc[active, 'close'], check_names=False)
    pd.testing.assert_series_equal(view.loc[active, 'valuation_date'], raw.loc[active, 'date'], check_names=False)
    assert view.loc[active, 'valuation_stale_days'].eq(0).all()
    pd.testing.assert_frame_equal(view.loc[active, raw.columns], raw.loc[active], check_dtype=False)


def test_frozen_trading_status_roundtrip_preserves_open_position(tmp_path):
    import duckdb
    from etf_strategy.src.market_data_store import upsert_bars

    raw = bars().assign(market='a_share', timeframe='1d', adjusted=True)
    raw['ts'] = raw.date
    db = tmp_path / 'snapshot.duckdb'
    upsert_bars(raw, db)
    adapter = engine.DuckDBMarketDataAdapter(db)
    legacy = adapter.load(date(2024, 1, 1), date(2024, 1, 4))
    assert 'tradestatus' not in legacy
    states = raw[['symbol', 'date', 'adjustment', 'tradestatus']]
    with duckdb.connect(str(db)) as con:
        con.register('states', states)
        con.execute('CREATE TABLE frozen_trading_status AS SELECT * FROM states')
    replay = adapter.load(date(2024, 1, 1), date(2024, 1, 4))
    assert 'tradestatus' in replay, 'frozen suspension evidence disappeared on replay'
    assert replay.set_index(['symbol', 'date']).tradestatus.to_dict() == raw.set_index(['symbol', 'date']).tradestatus.to_dict()
    spec = phase_spec()
    holdings, _, _ = engine._build_portfolio(selected(), raw, date(2024, 1, 2), spec)
    expected, expected_nav = engine._evaluate_forward(holdings, raw, date(2024, 1, 2), (2,), spec)
    actual, actual_nav = engine._evaluate_forward(holdings, replay, date(2024, 1, 2), (2,), spec)
    assert actual[2] == expected[2]
    pd.testing.assert_frame_equal(actual_nav, expected_nav)


def test_frozen_status_join_does_not_apply_other_adjustment_basis(tmp_path):
    import duckdb
    from etf_strategy.src.market_data_store import upsert_bars

    raw = bars().assign(market='a_share', timeframe='1d', adjusted=True)
    raw['ts'] = raw.date
    db = tmp_path / 'snapshot.duckdb'
    upsert_bars(raw, db)
    states = raw[['symbol', 'date', 'adjustment', 'tradestatus']].assign(adjustment='qfq')
    with duckdb.connect(str(db)) as con:
        con.register('states', states)
        con.execute('CREATE TABLE frozen_trading_status AS SELECT * FROM states')
    replay = engine.DuckDBMarketDataAdapter(db).load(date(2024, 1, 1), date(2024, 1, 4))
    assert 'tradestatus' in replay, 'frozen status table is not loaded'
    assert replay.tradestatus.isna().all()
