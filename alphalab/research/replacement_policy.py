"""Daily, cash-constrained execution for publicly known termination decisions.

Prices are a synthetic total-return series anchored at each lot's raw entry open.
This preserves adjusted-price returns without treating adjusted prices as real quotes.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd

from .delisting_events import eligible_on
from .engine import HorizonPerformance


@dataclass
class _Lot:
    symbol: str
    shares: float
    scale: float
    last_price: float
    cost: float
    raw_entry_open: float
    adjusted_entry_open: float
    unsettled: bool = False


def _positive(value):
    try:
        return math.isfinite(float(value)) and float(value) > 0
    except (ValueError, TypeError):
        return False


def evaluate_replacement(portfolio, data, sessions, spec, rank, raw_open):
    """Evaluate each horizon with its own terminal liquidation and audit ledger."""
    dates = pd.DatetimeIndex(pd.to_datetime(sessions)).normalize().unique().sort_values()
    bars = data.copy()
    bars['date'] = pd.to_datetime(bars['date']).dt.normalize()
    metadata_boundaries = {}
    if 'delisted_date' in bars:
        boundaries = bars[['symbol', 'delisted_date']].dropna(subset=['delisted_date']).copy()
        boundaries['delisted_date'] = pd.to_datetime(boundaries['delisted_date']).dt.normalize()
        metadata_boundaries = boundaries.groupby('symbol').delisted_date.min().to_dict()
    bars = bars.drop_duplicates(['symbol', 'date'], keep='last').set_index(['symbol', 'date'])
    events = list({e['event_id']: e for e in spec.wizard_metadata.get('delisting_events', [])}.values())
    if spec.max_industry_weight is not None:
        raise ValueError('退市替补暂不支持未经历史行业验证的行业权重上限')

    def bar(symbol, day):
        try:
            row = bars.loc[(symbol, day)]
        except KeyError:
            raise ValueError(f'{symbol} {day.date()}: 缺少持仓或替补行情') from None
        state = pd.to_numeric(row.get('tradestatus'), errors='coerce')
        if state not in (0, 1):
            raise ValueError(f'{symbol} {day.date()}: 交易状态未知')
        return row

    def executable(row, symbol, day):
        if float(row.tradestatus) == 0:
            return False, 'confirmed_suspension'
        if not all(_positive(row.get(k)) for k in ('open', 'high', 'low', 'close')):
            raise ValueError(f'{symbol} {day.date()}: 缺少有效成交价格')
        if not (_positive(row.get('amount')) or _positive(row.get('volume'))):
            raise ValueError(f'{symbol} {day.date()}: 正常交易状态但缺少正成交量')
        if float(row.high) == float(row.low):
            return False, 'one_price_bar'
        return True, None

    performances, nav_rows = {}, []
    for horizon in sorted(set(spec.horizons)):
        if len(dates) <= horizon or not len(dates) or portfolio.empty or not portfolio['shares'].map(_positive).any():
            performances[horizon] = HorizonPerformance(horizon, 'INSUFFICIENT_FORWARD_DATA', None, None, None, None, initial_cash=spec.initial_cash)
            continue
        window = dates[:horizon+1]
        cash = float(spec.initial_cash)
        commissions = slippage = realized_pnl = 0.0
        lots, pending, audit, path = {}, [], [], []
        for p in portfolio.itertuples():
            if not _positive(p.shares):
                continue
            row = bar(p.symbol, window[0])
            can_trade, reason = executable(row, p.symbol, window[0])
            if not can_trade:
                audit.append(dict(action='INITIAL_NOT_FILLED', symbol=p.symbol,
                                  date=str(window[0].date()), reason=reason,
                                  requested_shares=float(p.shares)))
                continue
            raw = float(p.entry_price)/(1+spec.slippage_rate)
            notional = float(p.entry_price)*float(p.shares)
            cost = notional*(1+spec.commission_rate)
            cash -= cost
            commissions += notional*spec.commission_rate
            slippage += raw*float(p.shares)*spec.slippage_rate
            lots[p.symbol] = _Lot(p.symbol, float(p.shares), raw/float(row.open), raw, cost, raw, float(row.open))

        def sell(symbol, day, price, reason, event=None):
            nonlocal cash, commissions, slippage, realized_pnl
            lot = lots.pop(symbol)
            gross = price*lot.shares
            slipped = gross*(1-spec.slippage_rate)
            fee = slipped*spec.commission_rate
            net = slipped-fee
            commissions += fee
            slippage += gross*spec.slippage_rate
            cash += net
            realized_pnl += net-lot.cost
            audit.append(dict(action='SELL', symbol=symbol, date=str(day.date()), reason=reason,
                              shares=lot.shares, price=price*(1-spec.slippage_rate), net_proceeds=net,
                              commission=fee, slippage=gross*spec.slippage_rate,
                              price_basis='adjusted_total_return_anchored_at_raw_entry',
                              raw_entry_open=lot.raw_entry_open, adjusted_entry_open=lot.adjusted_entry_open,
                              event_id=event.get('event_id') if event else None,
                              source_url=event.get('source_url') if event else None))
            return net

        for index, day in enumerate(window):
            terminal = index == horizon
            budgets = []
            public = {e['symbol'] for e in events if eligible_on(e, day)}
            for symbol, lot in list(lots.items()):
                applicable = [e for e in events if e['symbol'] == symbol and eligible_on(e, day)]
                event = applicable[0] if applicable else None
                # A listing boundary proves the exchange position is unsettled,
                # even when the finite announcement bundle has no decision entry.
                # It never triggers an early sale or exclusion from prior ranking.
                metadata_boundary = metadata_boundaries.get(symbol)
                metadata_ended = metadata_boundary is not None and day >= metadata_boundary
                event_ended = event and event.get('delisted_date') and day >= pd.Timestamp(event['delisted_date']).normalize()
                if metadata_ended or event_ended:
                    if not lot.unsettled:
                        audit.append(dict(action='UNSETTLED', symbol=symbol, date=str(day.date()),
                                          reason='termination_decision_delisting' if event_ended else 'listing_metadata_delisting',
                                          event_id=event['event_id'] if event_ended else None))
                    lot.unsettled = True
                if lot.unsettled:
                    continue
                row = bar(symbol, day)
                can_trade, reason = executable(row, symbol, day)
                if event and index > 0:
                    resumes = event.get('trading_resumes_on')
                    if resumes and day < pd.Timestamp(resumes).normalize():
                        can_trade, reason = False, 'before_trading_resumes'
                    if can_trade:
                        net = sell(symbol, day, float(row.open)*lot.scale, 'termination_decision', event)
                        if not terminal:
                            budgets.append((net, event))
                        continue
                    audit.append(dict(action='DEFER_SELL', symbol=symbol, date=str(day.date()), reason=reason, event_id=event['event_id']))
                if float(row.tradestatus) != 0:
                    lot.last_price = float(row.close)*lot.scale

            # Pending selections were frozen at an earlier close; do not substitute
            # another stock after inspecting this stock's future availability.
            for order in list(pending):
                symbol = order['symbol']
                metadata_boundary = metadata_boundaries.get(symbol)
                if metadata_boundary is not None and day >= metadata_boundary:
                    audit.append(dict(action='CANCEL_BUY', symbol=symbol, date=str(day.date()),
                                      reason='listing_metadata_delisting', rank_cutoff=order['rank_cutoff']))
                    pending.remove(order)
                    continue
                if symbol in public or symbol in lots or terminal:
                    audit.append(dict(action='CANCEL_BUY', symbol=symbol, date=str(day.date()), reason='terminal_or_ineligible', rank_cutoff=order['rank_cutoff']))
                    pending.remove(order)
                    continue
                row = bar(symbol, day)
                can_trade, reason = executable(row, symbol, day)
                if not can_trade:
                    audit.append(dict(action='DEFER_BUY', symbol=symbol, date=str(day.date()), reason=reason, rank_cutoff=order['rank_cutoff']))
                    continue
                try:
                    raw = float(raw_open(symbol, day))
                except Exception as exc:
                    raise ValueError(f'{symbol} {day.date()}: 替补原始开盘价不可核实: {exc}') from exc
                if not _positive(raw):
                    raise ValueError(f'{symbol} {day.date()}: 替补原始开盘价不可核实')
                price = raw*(1+spec.slippage_rate)
                budget = min(order['budget'], cash)
                if spec.max_single_weight is not None:
                    equity = cash
                    for held in lots.values():
                        if held.unsettled:
                            continue
                        held_row = bar(held.symbol, day)
                        mark = held.last_price if float(held_row.tradestatus) == 0 else float(held_row.open)*held.scale
                        equity += held.shares*mark
                    budget = min(budget, equity*spec.max_single_weight)
                shares = math.floor(budget/(price*(1+spec.commission_rate))/spec.lot_size)*spec.lot_size
                pending.remove(order)
                if shares <= 0:
                    audit.append(dict(action='CASH', symbol=symbol, date=str(day.date()), reason='lot_budget', rank_cutoff=order['rank_cutoff']))
                    continue
                cost = shares*price*(1+spec.commission_rate)
                fee = shares*price*spec.commission_rate
                cash -= cost
                commissions += fee
                slippage += shares*raw*spec.slippage_rate
                lots[symbol] = _Lot(symbol, shares, raw/float(row.open), float(row.close)*raw/float(row.open), cost, raw, float(row.open))
                audit.append(dict(action='BUY', symbol=symbol, date=str(day.date()), shares=shares, price=price,
                                  total_cost=cost, commission=fee, slippage=shares*raw*spec.slippage_rate,
                                  price_basis='verified_raw_open_plus_slippage', raw_entry_open=raw,
                                  adjusted_entry_open=float(row.open),
                                  rank_cutoff=order['rank_cutoff'], event_id=order['event']['event_id'], source_url=order['event'].get('source_url')))

            if terminal:
                for symbol, lot in list(lots.items()):
                    if lot.unsettled:
                        continue
                    row = bar(symbol, day)
                    can_trade, reason = executable(row, symbol, day)
                    # An official resumption date also constrains terminal liquidation.
                    blocked = any(e['symbol'] == symbol and eligible_on(e, day) and e.get('trading_resumes_on') and day < pd.Timestamp(e['trading_resumes_on']).normalize() for e in events)
                    if can_trade and not blocked:
                        sell(symbol, day, float(row.close)*lot.scale, 'terminal')
                    else:
                        audit.append(dict(action='OPEN_POSITION', symbol=symbol, date=str(day.date()), reason=reason or 'before_trading_resumes'))
            else:
                for budget, event in budgets:
                    excluded = public | set(lots) | {o['symbol'] for o in pending}
                    candidates = [str(s) for s in rank(day, excluded) if str(s) not in excluded]
                    cutoff = str(day.date())
                    if candidates:
                        pending.append(dict(symbol=candidates[0], budget=budget, rank_cutoff=cutoff, event=event))
                        audit.append(dict(action='SELECT', symbol=candidates[0], date=cutoff, rank_cutoff=cutoff, budget=budget, event_id=event['event_id']))
                    else:
                        audit.append(dict(action='CASH', symbol=event['symbol'], date=cutoff, reason='no_candidate', budget=budget, rank_cutoff=cutoff))
            known = cash + sum(l.shares*l.last_price for l in lots.values() if not l.unsettled)
            path.append(np.nan if any(l.unsettled for l in lots.values()) else known)

        values = pd.Series(path, index=window, dtype=float)
        peak = values.cummax().clip(lower=spec.initial_cash)
        drawdown = values/peak-1
        daily = values/values.shift(1)-1
        daily.iloc[0] = values.iloc[0]/spec.initial_cash-1
        unsettled = [s for s, l in lots.items() if l.unsettled]
        ending = None if unsettled else float(values.iloc[-1])
        vol = float(daily.std(ddof=1)) if not unsettled and len(values)>1 else None
        open_value = sum(l.shares*l.last_price for l in lots.values() if not l.unsettled)
        total = ending/spec.initial_cash-1 if ending is not None else None
        performances[horizon] = HorizonPerformance(
            horizon, 'UNSETTLED' if unsettled else 'COMPLETE', total,
            None if unsettled else float(drawdown.min()),
            None if unsettled else (ending+commissions+slippage)/spec.initial_cash-1,
            window[-1].date(), initial_cash=spec.initial_cash, ending_equity=ending,
            profit_loss=None if ending is None else ending-spec.initial_cash,
            max_drawdown_amount=None if unsettled else float((values-peak).min()),
            daily_volatility=vol, annualized_volatility=None if vol is None else vol*np.sqrt(252),
            annualized_return=None if ending is None or horizon == 0 else ((ending/spec.initial_cash)**(252/horizon)-1 if ending>0 else -1),
            sharpe=float(daily.mean()/vol*np.sqrt(252)) if vol and vol>0 else None,
            commission_paid=commissions, slippage_paid=slippage, cash_residual=cash,
            liquidation_status='UNSETTLED_DELISTING' if unsettled else ('OPEN_POSITION' if lots else 'LIQUIDATED'),
            unrealized_holdings_value=open_value, realized_cash=cash,
            realized_profit_loss=realized_pnl,
            unrealized_profit_loss=None if unsettled else sum(l.shares*l.last_price-l.cost for l in lots.values()),
            open_positions={s: l.shares for s,l in lots.items()}, known_assets_value=cash+open_value,
            unsettled_symbols=unsettled, execution_events=audit)
        for day in window:
            nav_rows.append(dict(date=day.date(), horizon=horizon, equity=values.loc[day], daily_return=daily.loc[day], drawdown=drawdown.loc[day]))
    return performances, pd.DataFrame(nav_rows, columns=['date', 'horizon', 'equity', 'daily_return', 'drawdown'])
