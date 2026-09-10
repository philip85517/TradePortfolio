"""Frozen point-in-time inputs and auditable raw anchors for event replacement."""
from __future__ import annotations

import json
import math
import pandas as pd

from .engine import _apply_universe_mode
from .plugins import FixedV0Plugin
from .suspension_policy import apply_candidate_policy


def attach_replacement_inputs(frozen, ranking_data, history, backend, scope):
    frozen.replacement_data = frozen.bars.copy(deep=True)
    frozen.replacement_scoring_data = ranking_data.copy(deep=True)
    frozen.replacement_rankings = []
    frozen.replacement_anchors = []
    if not history.empty:
        ends = pd.to_datetime(history.get('delisted_date', history.get('effective_to')), errors='coerce')
        if ends is not None:
            boundary = pd.Series(ends.to_numpy(), index=history.symbol).groupby(level=0).min()
            for frame in (frozen.bars, frozen.replacement_data, frozen.replacement_scoring_data):
                known = pd.to_datetime(frame.get('delisted_date', pd.Series(index=frame.index, dtype=object)), errors='coerce')
                frame['delisted_date'] = known.fillna(frame.symbol.map(boundary))
    from .delisting_events import load_events
    events = load_events(backend.cache_dir / 'delisting_events.json')

    def rank(day, excluded):
        cutoff = pd.Timestamp(day)
        excluded = set(excluded)
        for event in events:
            published = event['published_at']
            if 'T' in published and pd.Timestamp(published).tz_convert('Asia/Shanghai') <= cutoff.tz_localize('Asia/Shanghai') + pd.Timedelta(hours=15):
                excluded.add(event['symbol'])
        # Never discard candidates based on their future price coverage.
        before = ranking_data[ranking_data.date.le(cutoff)].copy()
        current_history = history.copy()
        if not current_history.empty:
            starts = pd.to_datetime(current_history.get('listed_date', current_history.get('effective_from')), errors='coerce')
            ends = pd.to_datetime(current_history.get('delisted_date', current_history.get('effective_to')), errors='coerce')
            if starts is not None:
                current_history = current_history.loc[starts.isna() | starts.le(cutoff)]
            if ends is not None:
                current_history = current_history.loc[ends.reindex(current_history.index).isna() | ends.reindex(current_history.index).gt(cutoff)]
            before = before[before.symbol.isin(current_history.symbol)]
        before, _ = _apply_universe_mode(before, cutoff.date(), 'point-in-time', current_history if not current_history.empty else None)
        current = set(before.loc[before.date.eq(cutoff), 'symbol'])
        expected = set(current_history.symbol if not current_history.empty else ranking_data.symbol) - excluded
        missing = expected - current
        if missing:
            raise ValueError(f"REPLACEMENT_DATA_MISSING：替补排名 {cutoff.date()} 缺少 {len(missing)} 只股票的当日行情：{','.join(sorted(missing)[:10])}")
        sessions = backend._calendar_dates((cutoff-pd.Timedelta(days=250)).date(),cutoff.date())[-120:]
        window = before[before.date.isin(pd.to_datetime(sessions)) & before.symbol.isin(expected)]
        listing_source = current_history if not current_history.empty else before
        listing_dates = pd.to_datetime(listing_source.get('listed_date', listing_source.get('effective_from', pd.Series(index=listing_source.index, dtype=object))), errors='coerce')
        first_listing = pd.Series(listing_dates.to_numpy(), index=listing_source.symbol).groupby(level=0).min()
        session_index = pd.DatetimeIndex(pd.to_datetime(sessions))
        observed = {symbol: pd.DatetimeIndex(part.date) for symbol, part in window.groupby('symbol', sort=False)}
        gaps = []
        for symbol in sorted(expected):
            listed = first_listing.get(symbol, pd.NaT)
            required = session_index if pd.isna(listed) else session_index[session_index >= listed]
            missing_days = required.difference(observed.get(symbol, pd.DatetimeIndex([])))
            if len(missing_days):
                gaps.append(f'{symbol} {missing_days[0].date()}')
        if gaps:
            raise ValueError(f"REPLACEMENT_DATA_MISSING：替补排名 {cutoff.date()} 缺少上市区间内筛选证据：{','.join(gaps[:10])}")
        # A supplied date with missing values is still missing scoring evidence.
        # Otherwise fixed_v0 silently drops its close/amount and changes both
        # eligibility and the effective return/liquidity lookback windows.
        import numpy as np
        from .suspension_policy import suspended_rows
        prices = window.reindex(columns=['open', 'high', 'low', 'close']).apply(pd.to_numeric, errors='coerce')
        amount = pd.to_numeric(window.get('amount', pd.Series(index=window.index, dtype=float)), errors='coerce')
        invalid_prices = ~np.isfinite(prices).all(axis=1) | ~prices.gt(0).all(axis=1)
        invalid_amount = ~np.isfinite(amount) | amount.lt(0)
        invalid = ~suspended_rows(window) & (invalid_prices | invalid_amount)
        if invalid.any():
            row = window.loc[invalid].sort_values(['date', 'symbol']).iloc[0]
            raise ValueError(f"REPLACEMENT_DATA_INVALID：替补排名 {cutoff.date()} 的 {row.symbol} {row.date.date()} 缺少有效OHLC或成交额评分证据")
        before = before[before.date.isin(pd.to_datetime(sessions))].copy()
        candidates, _ = FixedV0Plugin().score(before)
        candidates = apply_candidate_policy(candidates, before, cutoff, min_effective_samples=61)
        candidates = candidates[candidates.eligible & candidates.symbol.isin(current) & ~candidates.symbol.isin(excluded)]
        candidates = candidates.sort_values(['total_score', 'symbol'], ascending=[False, True], kind='mergesort')
        frozen.replacement_rankings.extend(dict(signal_date=str(cutoff.date()), symbol=str(row.symbol), total_score=float(row.total_score)) for row in candidates.itertuples())
        return candidates.symbol.astype(str).tolist()

    def raw_open(symbol, day):
        stamp = str(pd.Timestamp(day).date())
        path = backend.cache_dir / 'execution_prices.json'
        prices = json.loads(path.read_text()) if path.exists() else {}
        key = f'{symbol}:{stamp}'
        if key not in prices:
            if backend.provider is not None:
                from etf_strategy.src.market_data_providers import FetchRequest
                response = backend.provider.fetch_ohlcv(FetchRequest('a_share', symbol, '1d', pd.Timestamp(stamp), pd.Timestamp(stamp), options={'adjust': 'none'}))
                if response.empty:
                    raise ValueError(f'EXECUTION_PRICE_UNAVAILABLE：{symbol} {stamp} 缺少未复权开盘价')
                date_column = 'date' if 'date' in response else 'ts'
                if len(response) != 1 or date_column not in response or str(response.iloc[0].get('symbol')) != symbol or pd.Timestamp(response.iloc[0][date_column]).date() != pd.Timestamp(stamp).date() or response.iloc[0].get('adjustment') != 'none':
                    raise ValueError(f'EXECUTION_PRICE_UNAVAILABLE：{symbol} {stamp} 来源返回日期、股票或未复权口径不匹配')
                value = float(response.iloc[0]['open'])
                backend._save_execution_price(symbol, stamp, value)
            elif backend.adapter is not None:
                # Injected fixtures must explicitly provide same-date raw anchors.
                rows = ranking_data[ranking_data.symbol.eq(symbol) & ranking_data.date.eq(pd.Timestamp(stamp))]
                value = float(rows.iloc[0].get('raw_open', float('nan'))) if not rows.empty else float('nan')
                if not math.isfinite(value) or value <= 0:
                    raise ValueError(f'EXECUTION_PRICE_UNAVAILABLE：{symbol} {stamp} 缺少未复权开盘价')
                backend._save_execution_price(symbol, stamp, value)
            else:
                backend._provision_entry(symbol, stamp)
            prices = json.loads(path.read_text())
        value = float(prices[key])
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f'EXECUTION_PRICE_UNAVAILABLE：{symbol} {stamp} 未复权开盘价无效')
        from ..utils import json_hash
        evidence=dict(symbol=symbol, date=stamp, raw_open=value, source='verified-raw-open-cache')
        evidence['identity']=json_hash(evidence)
        frozen.replacement_anchors.append(evidence)
        return value

    frozen.replacement_rank = rank
    frozen.replacement_raw_open = raw_open


def normalize_replacement_bars(data, overlap):
    """Convert qfq only when independent hfq overlap proves a stable multiplier."""
    import numpy as np
    out = data.copy()
    audit = []
    overlaps = {symbol: frame for symbol, frame in overlap.groupby('symbol',sort=False)}
    columns = ['open','high','low','close']
    for symbol, part in out.groupby('symbol',sort=False):
        q = part.adjustment.eq('qfq')
        if not q.any():
            continue
        h = part[part.adjustment.eq('hfq')][['date',*columns]]
        source = overlaps.get(symbol, pd.DataFrame(columns=['date',*columns]))[['date',*columns]]
        paired = h.merge(source,on='date',suffixes=('_hfq','_qfq')).dropna()
        if len(paired)<5:
            raise ValueError(f'REPLACEMENT_ADJUSTMENT_UNAVAILABLE：{symbol} 替补复权缺少至少5日重叠证据')
        left=paired[[c+'_hfq' for c in columns]].to_numpy(float)
        right=paired[[c+'_qfq' for c in columns]].to_numpy(float)
        if not np.isfinite(left).all() or not np.isfinite(right).all() or (left<=0).any() or (right<=0).any():
            raise ValueError(f'REPLACEMENT_ADJUSTMENT_UNAVAILABLE：{symbol} 复权重叠价格无效')
        # Source quotes are rounded decimals. Prove a common factor by
        # intersecting every OHLC rounding interval, not a loose percent fit.
        precision = next((d for d in range(2,9) if np.allclose(right, np.round(right,d),rtol=0,atol=1e-10)),8)
        unit = 10.0**(-precision)
        half = unit/2 + 1e-10
        factor_tolerance = 1e-5  # 0.001%: bounded provider factor rounding across ex-dates.
        lower=float(np.max((left-5e-8)/(right+half)/(1+factor_tolerance)))
        upper=float(np.min((left+5e-8)/(right-half)/(1-factor_tolerance))) if (right>half).all() else float('nan')
        if not np.isfinite(upper) or lower>upper or lower<=0:
            raise ValueError(f'REPLACEMENT_ADJUSTMENT_UNAVAILABLE：{symbol} 复权重叠价格无法证明一致换算比例')
        # A broad intersection does not identify a useful price conversion.
        # Bound factor uncertainty to 0.1% across the entire feasible interval.
        if upper/lower-1 > .001:
            raise ValueError(f'REPLACEMENT_ADJUSTMENT_UNAVAILABLE：{symbol} 换算比例证据精度不足')
        ratios=left/right
        factor=float(np.clip(np.median(ratios),lower,upper))
        residual=float(np.max(np.abs(ratios/factor-1)))
        out.loc[part.index[q],columns]*=factor
        out.loc[part.index[q],'adjustment']='hfq'
        audit.append(dict(symbol=str(symbol),factor=factor,factor_interval_low=lower,factor_interval_high=upper,qfq_rounding_unit=unit,factor_rounding_relative_tolerance=factor_tolerance,max_relative_residual=residual,overlap_days=len(paired),overlap_start=str(paired.date.min().date()),overlap_end=str(paired.date.max().date()),method='verified-overlap-qfq-to-hfq-v1'))
    unknown=out[~out.adjustment.eq('hfq')]
    if not unknown.empty:
        raise ValueError(f'REPLACEMENT_ADJUSTMENT_UNAVAILABLE：{unknown.iloc[0].symbol} 替补行情复权未知')
    # Same evidence rule as readiness: positive observed turnover proves trading;
    # absence of turnover never proves suspension and remains unknown.
    status = pd.to_numeric(out.get('tradestatus', pd.Series(index=out.index,dtype=float)), errors='coerce')
    volume = pd.to_numeric(out.get('volume', pd.Series(index=out.index,dtype=float)), errors='coerce')
    amount = pd.to_numeric(out.get('amount', pd.Series(index=out.index,dtype=float)), errors='coerce')
    observed = out[['open','high','low','close']].apply(pd.to_numeric,errors='coerce').gt(0).all(axis=1) & (volume.gt(0) | amount.gt(0))
    out['tradestatus'] = status.mask(status.isna() & observed,1)
    out['trading_status_evidence'] = 'source'
    out.loc[status.isna() & observed,'trading_status_evidence'] = 'observed-positive-turnover'
    return out,audit


def prepare_replacement_data(data, readiness, *, cache_dir=None, progress=None, history=None, sessions=None):
    import duckdb
    from pathlib import Path
    from .data_binding import default_research_db_candidates
    symbols = list(readiness['symbols'])
    required = {}
    known_ends = data.dropna(subset=['delisted_date']).groupby('symbol').delisted_date.last() if 'delisted_date' in data else pd.Series(dtype=object)
    if sessions is not None and history is not None and not history.empty:
        for symbol, identity in history.groupby('symbol', sort=False):
            row = identity.iloc[-1]
            start = pd.to_datetime(row.get('listed_date', row.get('effective_from')), errors='coerce')
            end = pd.to_datetime(row.get('delisted_date', row.get('effective_to')), errors='coerce')
            observed_end = pd.to_datetime(known_ends.get(symbol), errors='coerce')
            if pd.notna(observed_end) and (pd.isna(end) or observed_end < end):
                end = observed_end
            required[symbol] = [pd.Timestamp(day) for day in sessions
                                if (pd.isna(start) or pd.Timestamp(day) >= start) and
                                (pd.isna(end) or pd.Timestamp(day) < end)]
    candidates = [source.get('db_path') for source in readiness['binding'].get('sources', [])] + list(default_research_db_candidates())
    paths = list(dict.fromkeys(str(path) for path in candidates if path and Path(path).is_file()))
    if any(source.get('source') == 'injected-adapter' for source in readiness['binding'].get('sources', [])):
        paths = []
    best, count = None, 0
    for path in paths:
        with duckdb.connect(path,read_only=True) as con:
            coverage=con.execute("SELECT count(DISTINCT symbol) FROM market_ohlcv WHERE market='a_share' AND timeframe='1d' AND adjustment='qfq' AND trade_date BETWEEN ? AND ?",[readiness['dates']['warmup_start_date'],readiness['dates']['exit_date']]).fetchone()[0]
        if coverage>count:
            best,count=path,coverage
    if best:
        with duckdb.connect(best,read_only=True) as con:
            con.register('research_symbols',pd.DataFrame({'symbol':symbols}))
            source=con.execute("SELECT market,symbol,ts AS date,open,high,low,close,volume,amount,adjustment FROM market_ohlcv JOIN research_symbols USING(symbol) WHERE market='a_share' AND timeframe='1d' AND adjustment='qfq' AND trade_date BETWEEN ? AND ?",[readiness['dates']['warmup_start_date'],readiness['dates']['exit_date']]).df()
        source['price_source']=best
        if 'tradestatus' in data:
            verified = data.dropna(subset=['tradestatus']).drop_duplicates(['symbol','date'],keep='last').set_index(['symbol','date']).tradestatus
            source['tradestatus'] = verified.reindex(pd.MultiIndex.from_frame(source[['symbol','date']])).to_numpy()
        # One qfq snapshot per stock, overlaid by already verified hfq evidence.
        hfq=data[data.adjustment.eq('hfq')]
        keys=pd.MultiIndex.from_frame(hfq[['symbol','date']])
        source_only=source[~pd.MultiIndex.from_frame(source[['symbol','date']]).isin(keys)]
        combined=pd.concat([source_only,hfq],ignore_index=True)
        metadata=data.sort_values('date').groupby('symbol').last()
        for column in ['name','listed_date','delisted_date','industry_level1','industry_level2','industry_level3']:
            if column in metadata:
                combined[column]=combined.symbol.map(metadata[column])
        overlap=source[source.date.le(pd.Timestamp(readiness['dates']['signal_date']))]
    else:
        combined=data
        overlap=pd.DataFrame(columns=['symbol','date','open','high','low','close'])
    overlaps = dict(tuple(overlap.groupby('symbol', sort=False)))
    pieces, audit, missing = [], [], []
    for index, (symbol, part) in enumerate(combined.groupby('symbol', sort=False)):
        if progress and index % 500 == 0:
            progress(f'核验替补行情复权：{index} / {len(symbols)} 只')
        try:
            normalized, evidence = normalize_replacement_bars(part, overlaps.get(symbol, overlap.iloc[:0]))
            absent = set(required.get(symbol, [])) - set(normalized.date)
            if absent:
                raise ValueError(f'REPLACEMENT_DATA_MISSING：{symbol} {min(absent).date()} 缺少行情，需补齐后复权数据')
            state = pd.to_numeric(normalized.tradestatus, errors='coerce')
            invalid_state = ~state.isin([0, 1])
            invalid_amount = state.eq(1) & ~pd.to_numeric(normalized.amount, errors='coerce').map(lambda value: pd.notna(value) and math.isfinite(value) and value >= 0)
            if (invalid_state | invalid_amount).any():
                raise ValueError(f'REPLACEMENT_DATA_INVALID：{symbol} 缺少可核实交易状态或成交额')
            pieces.append(normalized)
            for record in evidence:
                record['source_paths'] = [best] if best else []
            audit.extend(evidence)
        except ValueError as error:
            if cache_dir is None:
                raise
            missing.append((symbol, part, str(error)))
    if missing:
        from .replacement_history import fetch_replacement_history
        start, end = readiness['dates']['warmup_start_date'], readiness['dates']['exit_date']
        options = {'progress': progress} if progress else {}
        if required:
            options['expected_dates'] = {symbol: required.get(symbol, []) for symbol, _, _ in missing}
        fetched = fetch_replacement_history([(symbol, start, end) for symbol, _, _ in missing], cache_dir, **options)
        for symbol, original, reason in missing:
            direct = fetched[symbol].copy()
            verified = original[original.adjustment.eq('hfq')].copy()
            columns = ['open', 'high', 'low', 'close']
            paired = verified[['date', *columns]].merge(direct[['date', *columns]], on='date', suffixes=('_old', '_new')).dropna()
            minimum_overlap = 5
            initial_ipo_days = pd.DatetimeIndex([])
            if len(paired) < 5 and history is not None and not history.empty and sessions is not None:
                identity = history[history.symbol.eq(symbol)]
                listing = pd.to_datetime(identity.get('listed_date', identity.get('effective_from', pd.Series(index=identity.index, dtype=object))), errors='coerce').dropna().unique()
                if len(listing) == 1:
                    listed, signal = pd.Timestamp(listing[0]), pd.Timestamp(readiness['dates']['signal_date'])
                    if pd.Timestamp(start) <= listed <= signal:
                        calendar = pd.DatetimeIndex(pd.to_datetime(sessions)).normalize().unique().sort_values()
                        initial_ipo_days = calendar[(calendar >= listed) & (calendar <= signal)]
                        # The history and trusted calendar must prove that this
                        # IPO's initial window really only contains 1-4 sessions.
                        # Every one must have both old and new finite OHLC.
                        if listed in calendar and signal in calendar and 1 <= len(initial_ipo_days) < 5 and not len(initial_ipo_days.difference(pd.DatetimeIndex(paired.date))):
                            minimum_overlap = len(initial_ipo_days)
            if len(paired) < minimum_overlap:
                raise ValueError(f'REPLACEMENT_ADJUSTMENT_UNAVAILABLE：{symbol} 新旧后复权缺少5日衔接证据；短历史例外需验证上市日期且覆盖全部初始交易日')
            import numpy as np
            left = paired[[c+'_old' for c in columns]].to_numpy(float)
            right = paired[[c+'_new' for c in columns]].to_numpy(float)
            if not np.allclose(left, right, rtol=1e-5, atol=5e-8):
                raise ValueError(f'REPLACEMENT_ADJUSTMENT_UNAVAILABLE：{symbol} 新旧后复权价格不一致，保留原数据')
            # Existing HFQ prices do not prove that missing state/turnover is valid.
            # Keep the fetched repair on precisely those defective existing rows.
            state = pd.to_numeric(verified.get('tradestatus', pd.Series(index=verified.index, dtype=float)), errors='coerce')
            amount = pd.to_numeric(verified.get('amount', pd.Series(index=verified.index, dtype=float)), errors='coerce')
            repair = ~state.isin([0, 1]) | (state.eq(1) & (~np.isfinite(amount) | amount.lt(0)))
            if repair.any():
                matched = direct.set_index('date').reindex(verified.date)
                for column in [*columns, 'tradestatus', 'volume', 'amount', 'price_source', 'source_correction_id']:
                    if column in matched:
                        verified.loc[repair, column] = matched[column].to_numpy()[repair.to_numpy()]
            # Preserve the already inspected initial selection and execution anchors.
            keys = pd.MultiIndex.from_frame(verified[['symbol', 'date']])
            direct = direct[~pd.MultiIndex.from_frame(direct[['symbol', 'date']]).isin(keys)]
            for column in ['name','listed_date','delisted_date','industry_level1','industry_level2','industry_level3']:
                if column in original and original[column].notna().any():
                    direct[column] = original[column].dropna().iloc[-1]
            supplemented = pd.concat([verified, direct], ignore_index=True)
            normalized, _ = normalize_replacement_bars(supplemented, overlap.iloc[:0])
            state = pd.to_numeric(normalized.tradestatus, errors='coerce')
            amount = pd.to_numeric(normalized.amount, errors='coerce')
            if (~state.isin([0, 1]) | (state.eq(1) & (~np.isfinite(amount) | amount.lt(0)))).any():
                raise ValueError(f'REPLACEMENT_DATA_INVALID：{symbol} 补数后交易状态或成交额仍不完整')
            pieces.append(normalized)
            audit.append(dict(symbol=symbol, method='direct-provider-hfq-v1',
                              source_paths=[str(cache_dir / 'replacement_history')],
                              overlap_days=len(paired),
                              overlap_requirement='complete_initial_ipo_window' if minimum_overlap < 5 else 'at_least_five_days',
                              initial_ipo_overlap_dates=[str(day.date()) for day in initial_ipo_days] if minimum_overlap < 5 else [],
                              max_overlap_relative_error=float(np.max(np.abs(left/right-1))),
                              request_start=start, request_end=end, fallback_reason=reason))
    return pd.concat(pieces, ignore_index=True), audit
