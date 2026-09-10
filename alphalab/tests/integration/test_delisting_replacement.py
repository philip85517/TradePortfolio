import pandas as pd
import pytest
from alphalab.research.engine import ResearchSpec


def fixture(*, commission=0, slip=0, candidates=('B',)):
    days = pd.bdate_range('2026-01-05', periods=5)
    rows = [dict(date=d, symbol=s, open=p, high=p+1, low=p-1, close=p, amount=10000, volume=1000, tradestatus=1)
            for d in days for s, p in [('A', 10), ('B', 6), ('C', 7)]]
    spec = ResearchSpec('2026-01-02', horizons=(4,), initial_cash=2000, commission_rate=commission, slippage_rate=slip,
                        wizard_metadata={'delisting_events': [dict(event_id='a', symbol='A', published_at='2026-01-05',
                            event_type='termination_decision', source_url='https://www.sse.com.cn/a')]})
    portfolio = pd.DataFrame([dict(symbol='A', shares=100, entry_price=10*(1+slip))])
    return portfolio, pd.DataFrame(rows), days, spec, lambda day, excluded: [s for s in candidates if s not in excluded], lambda s, d: {'B': 6, 'C': 7}[s]


def run(args):
    from alphalab.research.replacement_policy import evaluate_replacement
    return evaluate_replacement(*args)


def test_date_only_exit_next_day_buy_following_day_and_lot_budget():
    perf, nav = run(fixture())
    events = perf[4].execution_events
    sell = next(e for e in events if e['action'] == 'SELL' and e['symbol'] == 'A')
    buy = next(e for e in events if e['action'] == 'BUY')
    assert sell['date'] == '2026-01-06'
    assert buy['date'] == '2026-01-07'
    assert buy['rank_cutoff'] == '2026-01-06'
    assert buy['shares'] == 100
    assert buy['total_cost'] <= sell['net_proceeds']
    assert nav.iloc[-1].equity == pytest.approx(perf[4].ending_equity)


def test_no_candidate_keeps_proceeds_cash():
    perf, nav = run(fixture(candidates=()))
    assert perf[4].ending_equity == 2000
    assert not any(e['action'] == 'BUY' for e in perf[4].execution_events)


def test_fees_and_slippage_reconcile_and_initial_cost_is_drawdown():
    perf, nav = run(fixture(commission=.01, slip=.01))
    p = perf[4]
    assert p.ending_equity == pytest.approx(2000-p.commission_paid-p.slippage_paid)
    assert nav.iloc[0].drawdown < 0
    assert nav.iloc[-1].equity == pytest.approx(p.ending_equity)


def test_suspension_defers_exit_and_terminal_does_not_buy():
    args = list(fixture())
    args[1].loc[(args[1].symbol == 'A') & args[1].date.between('2026-01-06', '2026-01-08'), 'tradestatus'] = 0
    p = run(args)[0][4]
    assert not any(e['action'] == 'BUY' for e in p.execution_events)
    assert next(e for e in p.execution_events if e['action'] == 'SELL')['date'] == '2026-01-09'


def test_new_position_missing_bar_reports_symbol_and_date():
    args = list(fixture())
    args[1] = args[1][~((args[1].symbol == 'B') & args[1].date.eq('2026-01-08'))]
    with pytest.raises(ValueError, match='B.*2026-01-08'):
        run(args)


def test_duplicate_events_sell_once_and_replacement_can_itself_exit():
    args = list(fixture(candidates=('B', 'C')))
    events = args[3].wizard_metadata['delisting_events']
    events.append(dict(events[0]))
    events.append(dict(event_id='b', symbol='B', published_at='2026-01-07', event_type='termination_decision', source_url='https://www.sse.com.cn/b'))
    p = run(args)[0][4]
    sells = [e for e in p.execution_events if e['action'] == 'SELL']
    assert [(e['symbol'], e['date']) for e in sells] == [('A', '2026-01-06'), ('B', '2026-01-08')]
    assert any(e['action'] == 'SELECT' and e['symbol'] == 'C' for e in p.execution_events)


def test_locked_price_defers_without_changing_ranking_cutoff():
    args = list(fixture())
    mask = args[1].symbol.eq('B') & args[1].date.eq('2026-01-07')
    args[1].loc[mask, ['open', 'high', 'low', 'close']] = 6
    events = run(args)[0][4].execution_events
    buy = next(e for e in events if e['action'] == 'BUY')
    assert buy['date'] == '2026-01-08'
    assert buy['rank_cutoff'] == '2026-01-06'


def test_delisted_without_sale_is_unsettled_not_zero_or_last_quote_return():
    args = list(fixture())
    args[3].wizard_metadata['delisting_events'][0]['delisted_date'] = '2026-01-08'
    args[1].loc[args[1].symbol.eq('A') & args[1].date.ge('2026-01-06'), 'tradestatus'] = 0
    args[1] = args[1][~(args[1].symbol.eq('A') & args[1].date.ge('2026-01-08'))]
    p, nav = run(args)
    assert p[4].status == 'UNSETTLED'
    assert p[4].total_return is None
    assert p[4].ending_equity is None
    assert p[4].known_assets_value == 1000
    assert pd.isna(nav.iloc[-1].equity)


def test_replacement_without_announcement_remains_unsettled_at_metadata_boundary():
    args = list(fixture())
    args[1].loc[args[1].symbol.eq('B'), 'delisted_date'] = '2026-01-08'
    args[1] = args[1][~(args[1].symbol.eq('B') & args[1].date.ge('2026-01-08'))]
    performance, nav = run(args)
    result = performance[4]
    # A future boundary cannot remove B from the Jan 6 ranking or Jan 7 buy.
    assert next(e for e in result.execution_events if e['action'] == 'BUY')['date'] == '2026-01-07'
    assert not any(e['action'] == 'SELL' and e['symbol'] == 'B' for e in result.execution_events)
    boundary = next(e for e in result.execution_events if e['action'] == 'UNSETTLED')
    assert boundary['date'] == '2026-01-08'
    assert boundary['reason'] == 'listing_metadata_delisting'
    assert boundary['event_id'] is None
    assert result.status == 'UNSETTLED'
    assert result.total_return is None and result.ending_equity is None
    assert result.open_positions == {'B': 100}
    assert result.realized_cash == 1400
    assert result.known_assets_value == 1400
    assert pd.isna(nav.iloc[-1].equity)


def test_pending_replacement_is_cancelled_only_when_metadata_boundary_arrives():
    args = list(fixture())
    args[1].loc[args[1].symbol.eq('B'), 'delisted_date'] = '2026-01-08'
    args[1].loc[args[1].symbol.eq('B') & args[1].date.eq('2026-01-07'), 'tradestatus'] = 0
    args[1] = args[1][~(args[1].symbol.eq('B') & args[1].date.ge('2026-01-08'))]
    performance, _ = run(args)
    result = performance[4]
    assert next(e for e in result.execution_events if e['action'] == 'SELECT')['symbol'] == 'B'
    assert next(e for e in result.execution_events if e['action'] == 'DEFER_BUY')['date'] == '2026-01-07'
    cancel = next(e for e in result.execution_events if e['action'] == 'CANCEL_BUY')
    assert cancel['date'] == '2026-01-08'
    assert cancel['reason'] == 'listing_metadata_delisting'
    assert result.status == 'COMPLETE'
    assert result.ending_equity == 2000


def test_future_metadata_boundary_does_not_excuse_missing_current_bar():
    args = list(fixture())
    args[1].loc[args[1].symbol.eq('B'), 'delisted_date'] = '2026-01-09'
    args[1] = args[1][~(args[1].symbol.eq('B') & args[1].date.eq('2026-01-08'))]
    with pytest.raises(ValueError, match='B.*2026-01-08.*缺少'):
        run(args)


def test_replacement_raw_entry_anchor_preserves_total_return_and_share_budget():
    args = list(fixture())
    # Adjusted price units are arbitrary: the new lot must still cost raw 6/share.
    args[1].loc[args[1].symbol.eq('B'), ['open', 'high', 'low', 'close']] *= 5
    p = run(args)[0][4]
    assert p.ending_equity == pytest.approx(2000)
    assert next(e for e in p.execution_events if e['action'] == 'BUY')['shares'] == 100


def test_unknown_status_and_missing_raw_quote_fail_loudly():
    args = list(fixture())
    args[1].loc[args[1].symbol.eq('B') & args[1].date.eq('2026-01-07'), 'tradestatus'] = None
    with pytest.raises(ValueError, match='B.*2026-01-07.*状态'):
        run(args)
    args = list(fixture())
    args[-1] = lambda symbol, day: float('nan')
    with pytest.raises(ValueError, match='B.*2026-01-07.*原始'):
        run(args)


def test_single_stock_cap_and_terminal_suspension_retains_open_position():
    from dataclasses import replace
    args = list(fixture())
    args[3] = replace(args[3], max_single_weight=.2)
    p = run(args)[0][4]
    assert not any(e['action'] == 'BUY' for e in p.execution_events)
    args = list(fixture())
    args[1].loc[args[1].symbol.eq('B') & args[1].date.eq('2026-01-09'), 'tradestatus'] = 0
    p, nav = run(args)
    assert p[4].liquidation_status == 'OPEN_POSITION'
    assert p[4].open_positions == {'B': 100}
    assert p[4].ending_equity == pytest.approx(nav.iloc[-1].equity)


def test_weight_cap_at_buy_open_cannot_use_other_holdings_future_close():
    from dataclasses import replace
    args = list(fixture())
    args[0] = pd.concat([args[0], pd.DataFrame([dict(symbol='C', shares=100, entry_price=7)])], ignore_index=True)
    args[3] = replace(args[3], max_single_weight=.29)
    args[1].loc[args[1].symbol.eq('C') & args[1].date.eq('2026-01-07'), 'close'] = 100
    assert not any(e['action'] == 'BUY' for e in run(args)[0][4].execution_events)


def test_empty_initial_portfolio_does_not_report_allcash_success():
    args = list(fixture())
    args[0] = args[0].iloc[:0]
    p, nav = run(args)
    assert p[4].status == 'INSUFFICIENT_FORWARD_DATA'
    assert p[4].total_return is None
    assert nav.empty


def test_execution_audit_identifies_total_return_valuation_model():
    p = run(fixture())[0][4]
    sell = next(e for e in p.execution_events if e['action'] == 'SELL')
    assert sell['price_basis'] == 'adjusted_total_return_anchored_at_raw_entry'
    assert sell['raw_entry_open'] == 10
    assert sell['adjusted_entry_open'] == 10


def test_nonpositive_normal_close_reports_actual_position_and_day():
    args = list(fixture())
    args[1].loc[args[1].symbol.eq('B') & args[1].date.eq('2026-01-08'), 'close'] = 0
    with pytest.raises(ValueError, match='B.*2026-01-08.*价格'):
        run(args)


def test_realized_and_unrealized_profit_loss_reconcile_across_replacements():
    args = list(fixture(commission=.01, slip=.01))
    args[1].loc[args[1].symbol.eq('B') & args[1].date.eq('2026-01-09'), 'tradestatus'] = 0
    p = run(args)[0][4]
    sell = next(e for e in p.execution_events if e['action'] == 'SELL')
    buy = next(e for e in p.execution_events if e['action'] == 'BUY')
    assert p.realized_profit_loss == pytest.approx(sell['net_proceeds'] - 100*10.1*1.01)
    assert p.unrealized_profit_loss == pytest.approx(600-buy['total_cost'])
    assert p.realized_profit_loss + p.unrealized_profit_loss == pytest.approx(p.profit_loss)
    p = run(fixture(commission=.01, slip=.01))[0][4]
    assert p.realized_profit_loss == pytest.approx(p.profit_loss)
    assert p.unrealized_profit_loss == 0


def test_unsettled_api_exposes_unknown_unrealized_pnl_and_partial_nav():
    args = list(fixture())
    args[3].wizard_metadata['delisting_events'][0]['delisted_date'] = '2026-01-08'
    args[1].loc[args[1].symbol.eq('A') & args[1].date.ge('2026-01-06'), 'tradestatus'] = 0
    p, nav = run(args)
    assert p[4].liquidation_status == 'UNSETTLED_DELISTING'
    assert p[4].realized_profit_loss == 0
    assert p[4].unrealized_profit_loss is None
    assert nav.iloc[0].equity == 2000
    assert nav[nav.date.ge(pd.Timestamp('2026-01-08').date())].equity.isna().all()


def test_initial_one_price_lot_keeps_cash_and_does_not_increase_other_lot():
    args = list(fixture(commission=.01, slip=.01))
    args[0] = pd.concat([args[0], pd.DataFrame([dict(symbol='C', shares=100, entry_price=7*1.01)])], ignore_index=True)
    args[1].loc[args[1].symbol.eq('A') & args[1].date.eq('2026-01-05'), ['open', 'high', 'low', 'close']] = 10
    p, nav = run(args)
    events = p[4].execution_events
    assert any(e['action'] == 'INITIAL_NOT_FILLED' and e['symbol'] == 'A' and e['reason'] == 'one_price_bar' for e in events)
    assert not any(e['action'] in ('BUY', 'SELL') and e['symbol'] in ('A', 'B') for e in events)
    assert next(e for e in events if e['action'] == 'SELL')['shares'] == 100
    assert p[4].ending_equity == pytest.approx(2000 - 28)
    assert nav.iloc[0].equity == pytest.approx(2000 - (707*1.01-700))
