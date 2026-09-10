"""Explicit, evidence-backed source corrections applied to copies, never source DBs.

These repairs are data-quality provenance, not historical trading signals. A
caller must recompute any scoring derived from the affected original prices.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pandas as pd


_EVIDENCE_PATH = Path(__file__).with_name('data') / 'price_corrections.json'
_CORRECTIONS = json.loads(_EVIDENCE_PATH.read_text(encoding='utf-8'))['corrections']
for _correction in _CORRECTIONS:
    _correction['multiplier'] = float(_correction['factor_numerator']) / float(_correction['factor_denominator'])


def active_price_corrections(symbols, start, end) -> list[dict]:
    """Return independent manifest records for corrections intersecting a scope."""
    symbols = {str(symbol) for symbol in symbols}
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if pd.isna(start) or pd.isna(end) or start > end:
        raise ValueError('price correction scope requires a valid ordered date range')
    return [deepcopy(correction) for correction in _CORRECTIONS
            if correction['symbol'] in symbols and end >= pd.Timestamp(correction['effective_from'])
            and (not correction.get('effective_to') or start <= pd.Timestamp(correction['effective_to']))]


def apply_verified_price_corrections(frame, date_column='date', provider=None):
    """Copy a frame and repair only identified source/symbol/basis/date rows.

    An explicit per-row source takes precedence over the provider fallback, so a
    mixed-source frame cannot misapply a BaoStock repair to another vendor. The
    source_correction_id and source markers make repeat application safe, including
    standard market snapshots which preserve source but omit additional columns.
    Only OHLC values are multiplied. Volume, amount and original frames stay intact.
    """
    result = frame.copy(deep=True)
    if result.empty or not {'symbol', 'adjustment', date_column}.issubset(result.columns):
        return result
    source = result['source'].copy() if 'source' in result else pd.Series(None, index=result.index, dtype=object)
    if provider is not None:
        source = source.fillna(provider)
    for correction in _CORRECTIONS:
        marker = correction['source'] + '+correction:' + correction['source_correction_id']
        restored = (result.symbol.eq(correction['symbol']) & result.adjustment.eq(correction['adjustment'])
                    & source.eq(marker)).fillna(False)
        if restored.any():
            if 'source_correction_id' not in result:
                result['source_correction_id'] = pd.Series(None, index=result.index, dtype=object)
            result.loc[restored, 'source_correction_id'] = correction['source_correction_id']
        mask = (result.symbol.eq(correction['symbol']) & result.adjustment.eq(correction['adjustment'])
                & source.eq(correction['source'])).fillna(False)
        if not mask.any():
            continue
        days = pd.to_datetime(result.loc[mask, date_column], errors='raise').dt.normalize()
        eligible = days.ge(pd.Timestamp(correction['effective_from']))
        if correction.get('effective_to'):
            eligible &= days.le(pd.Timestamp(correction['effective_to']))
        mask.loc[mask] = eligible.to_numpy()
        if 'source_correction_id' in result:
            # Preserve other provenance and refuse to compound a different repair.
            existing = result.source_correction_id
            mask &= existing.isna() | existing.eq('')
        if not mask.any():
            continue
        for column in ('open', 'high', 'low', 'close'):
            if column in result:
                result.loc[mask, column] = pd.to_numeric(result.loc[mask, column], errors='raise') * correction['multiplier']
        if 'source_correction_id' not in result:
            result['source_correction_id'] = pd.Series(None, index=result.index, dtype=object)
        result.loc[mask, 'source_correction_id'] = correction['source_correction_id']
        if 'source' not in result:
            result['source'] = source
        result.loc[mask, 'source'] = marker
    return result
