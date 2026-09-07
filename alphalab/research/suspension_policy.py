"""Versioned, research-only interpretation of confirmed suspension facts.

phase-aware-v1 uses calendar-row liquidity averages with suspended turnover zero,
requires 61 effective traded observations for fixed_v0's 60-session return, and
never manufactures execution quotes. Raw storage is never changed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

POLICY_VERSION = 'phase-aware-v1'
MIN_EFFECTIVE_SAMPLES = 61


def suspended_rows(data: pd.DataFrame) -> pd.Series:
    return pd.to_numeric(data.get('tradestatus', pd.Series(index=data.index, dtype=object)), errors='coerce').eq(0)


def prepare_view(data: pd.DataFrame, signal, entry=None, exit=None) -> pd.DataFrame:
    """Copy facts into an interpreted view; carry prices only for known suspensions.

    Valuation anchors are grouped by symbol AND adjustment basis. A missing or
    anomalous normal-trading bar never receives a fabricated forward fill.
    ``valuation_stale_days`` is elapsed calendar days since the anchor.
    """
    view = data.copy(deep=True)
    view['date'] = pd.to_datetime(view['date'])
    suspended = suspended_rows(view)
    for column in ('amount', 'volume'):
        if column in view:
            view.loc[suspended, column] = 0.0
    view['valuation_close'] = pd.to_numeric(view['close'], errors='coerce')
    view['valuation_date'] = view['date']
    view['valuation_stale_days'] = 0
    if not suspended.any():
        return view
    # Only affected symbols need anchor work; ordinary trading rows already have
    # their valuation. Keep earlier rows of affected symbols as possible anchors.
    affected = view[view.symbol.isin(view.loc[suspended, 'symbol'])]
    keys = ['symbol'] + (['adjustment'] if 'adjustment' in view else [])
    for _, part in affected.sort_values('date', kind='mergesort').groupby(keys, dropna=False, sort=False):
        paused = suspended.reindex(part.index)
        if not paused.any():
            continue
        close = pd.to_numeric(part['close'], errors='coerce')
        amount = pd.to_numeric(part.get('amount', pd.Series(index=part.index, dtype=float)), errors='coerce')
        volume = pd.to_numeric(part.get('volume', pd.Series(index=part.index, dtype=float)), errors='coerce')
        reliable = ~paused & close.gt(0) & np.isfinite(close) & (amount.gt(0) | volume.gt(0))
        anchors = close.where(reliable).ffill()
        anchor_dates = part['date'].where(reliable).ffill()
        indexes = part.index[paused]
        view.loc[indexes, 'valuation_close'] = anchors.loc[indexes]
        view.loc[indexes, 'valuation_date'] = anchor_dates.loc[indexes]
        view.loc[indexes, 'valuation_stale_days'] = (part.loc[indexes, 'date'] - anchor_dates.loc[indexes]).dt.days
    return view


def apply_candidate_policy(candidates, before, signal, *, min_effective_samples=MIN_EFFECTIVE_SAMPLES):
    """Apply eligibility from facts dated no later than the frozen signal date."""
    output = candidates.copy()
    history = before[pd.to_datetime(before.date).le(pd.Timestamp(signal))].copy()
    # Stable date order makes the last supplied same-symbol/date fact win,
    # matching per-symbol deduplication without thousands of Python group loops.
    history = history.sort_values('date', kind='mergesort').drop_duplicates(['symbol', 'date'], keep='last')
    paused = suspended_rows(history)
    close = pd.to_numeric(history.close, errors='coerce')
    amount = pd.to_numeric(history.get('amount', pd.Series(index=history.index, dtype=float)), errors='coerce')
    volume = pd.to_numeric(history.get('volume', pd.Series(index=history.index, dtype=float)), errors='coerce')
    effective = ~paused & close.gt(0) & np.isfinite(close) & (amount.gt(0) | volume.gt(0))
    effective_counts = effective.groupby(history.symbol, sort=False).sum()
    effective_counts.index = effective_counts.index.astype(str)
    signal_suspended = set(history.loc[paused & pd.to_datetime(history.date).eq(pd.Timestamp(signal)), 'symbol'].astype(str))
    output['effective_traded_samples'] = output.symbol.astype(str).map(effective_counts).fillna(0).astype(int)
    if 'reason' not in output:
        output['reason'] = ''
    for mask, reason in [
        (output.symbol.astype(str).isin(signal_suspended), '信号日停牌'),
        (output.effective_traded_samples.lt(min_effective_samples), f'有效交易样本不足{min_effective_samples}日'),
    ]:
        output.loc[mask, 'eligible'] = False
        output.loc[mask, 'reason'] = output.loc[mask, 'reason'].fillna('').astype(str).map(lambda value: (value + '；' + reason).strip('；'))
    return output
