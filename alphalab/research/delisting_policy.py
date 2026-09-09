"""Account for known assets while retaining delisted shares with unknown recovery.

No synthetic quote, sale, recovery value, or complete portfolio return is created.
"""
import pandas as pd
from .suspension_policy import suspended_rows

POLICY_VERSION = 'retain-unsettled-v1'


def unsettled_performance(after, prices, shares, entry_prices, dates, horizon, spec):
    from .engine import HorizonPerformance
    if spec.wizard_metadata.get('delisting_policy') != POLICY_VERSION or 'delisted_date' not in after:
        return None
    cutoff = pd.Timestamp(dates[-1])
    events = {}
    for symbol in shares.index:
        part = after[after.symbol.eq(symbol)]
        ds = pd.to_datetime(part.delisted_date, errors='coerce').dropna().unique()
        if len(ds)==1 and pd.Timestamp(ds[0])<=cutoff:
            events[symbol]=pd.Timestamp(ds[0])
    if not events:
        return None
    for symbol in shares.index:
        missing=prices[symbol].isna()
        if missing.any() and (symbol not in events or (prices.index[missing]<events[symbol]).any()):
            return None  # Ordinary missing prices retain the existing failure path.
    known=shares.index.difference(list(events))
    terminal=after[after.date.eq(cutoff)].drop_duplicates('symbol',keep='last').set_index('symbol')
    paused=suspended_rows(terminal).reindex(known).fillna(False)
    buy=entry_prices*shares*(1+spec.commission_rate)
    cash=float(spec.initial_cash-buy.sum())
    final=prices.iloc[-1].reindex(known)*shares.reindex(known)
    proceeds=final*(1-spec.slippage_rate)*(1-spec.commission_rate)
    liquid=known[~paused]
    open_known=known[paused]
    realized_cash=cash+float(proceeds.reindex(liquid).sum())
    buy_commission=float((entry_prices*shares*spec.commission_rate).sum())
    sell_commission=float((final.reindex(liquid)*(1-spec.slippage_rate)*spec.commission_rate).sum())
    slippage=float(((entry_prices-entry_prices/(1+spec.slippage_rate))*shares).sum()+final.reindex(liquid).sum()*spec.slippage_rate)
    return HorizonPerformance(horizon=horizon,status='UNSETTLED',total_return=None,max_drawdown=None,
        gross_return=None,evaluated_date=cutoff.date(),
        message='退市股份尚未结算，完整期末权益及收益不可确定；已知资产不包含未结算股份价值。',
        initial_cash=float(spec.initial_cash),cash_residual=cash,realized_cash=realized_cash,
        known_assets_value=realized_cash+float(final.reindex(open_known).sum()),
        realized_profit_loss=float((proceeds-buy).reindex(liquid).sum()),
        commission_paid=buy_commission+sell_commission,slippage_paid=slippage,
        liquidation_status='UNSETTLED_DELISTING',unsettled_symbols=sorted(events),
        open_positions={str(s):float(shares[s]) for s in shares.index if s in events or s in open_known})
