"""Data-first interval adapter over the existing historical research engine.

Calendar and provider injection are explicit boundaries for deterministic integration
fixtures. Production calendars come from BaoStock, never inferred from stock bars.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys

import pandas as pd

from ..utils import json_hash, parse_date
from .data_binding import auto_bind_research_db, DataBindingError
from .engine import (DuckDBMarketDataAdapter, HistoricalResearchLab, InMemoryMarketDataAdapter,
                     PortfolioSpec, ResearchSpec, _normalise_dates, _jsonable,
                     _build_portfolio, _prepare_plugin_candidates, _apply_universe_mode,
                     _validate_universe_quality)
from .plugins import FixedV0Plugin, factor_definition, validate_plugin_output
from .data_readiness import make_issue, liquidity_issues, finalize_readiness


def _merge_history(existing, incoming):
    """Prefer complete PIT rows; later listing-only caches cannot degrade them."""
    if existing.empty:
        return incoming.copy()
    combined = pd.concat([existing, incoming], ignore_index=True)
    columns = [c for c in ('industry_level1', 'industry_level2', 'industry_level3') if c in combined]
    combined['_quality'] = combined[columns].notna().sum(axis=1) if columns else 0
    return combined.sort_values('_quality', kind='mergesort').drop_duplicates('symbol', keep='last').drop(columns='_quality').reset_index(drop=True)


@dataclass(frozen=True)
class ManualSelectionPlugin:
    plugin_id: str = 'manual_v1'
    version: str = '1.0.0'
    role: str = 'filter_and_score'
    supported_markets: tuple = ('a_share',)
    required_fields: tuple = ('close', 'adjustment')
    min_history_days: int = 1
    score_direction: str = 'higher_is_better'
    parameter_schema: dict = field(default_factory=dict)

    def score(self, before):
        rows = before.sort_values(['symbol', 'date']).groupby('symbol').tail(1)
        out = pd.DataFrame({'symbol': rows.symbol, 'eligible': True, 'total_score': 1.,
                            'reason': '用户手选；存在事后选择偏差', 'history_count': 1})
        return out, {'universe': len(out), 'rule_eligible': len(out)}

    def source_hash(self):
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class FrozenRulePlugin(FixedV0Plugin):
    """Keep fixed_v0 scores on their original signal-day price basis."""
    def __init__(self, selection_bars):
        super().__init__()
        candidates, funnel = super().score(selection_bars.copy(deep=True))
        object.__setattr__(self, '_candidates', candidates)
        object.__setattr__(self, '_funnel', funnel)

    def score(self, before):
        return self._candidates.copy(deep=True), dict(self._funnel)


class FrozenAdapter(InMemoryMarketDataAdapter):
    def __init__(self, bars, history, selection_bars=None):
        super().__init__(bars)
        self.history = history.copy()
        self.selection_bars = selection_bars.copy() if selection_bars is not None else bars.copy()

    def load_universe_as_of(self, as_of, market='a_share', symbols=None):
        return self.history.copy()


class FrozenBinding:
    def __init__(self, readiness, adapter):
        self.readiness = readiness
        self.adapter = adapter
        self.db_path = None

    def to_dict(self):
        return {'source': 'wizard-frozen-snapshot', 'db_path': str(self.db_path) if self.db_path else None, 'data_fingerprint': self.readiness['data_identity'],
                **self.readiness['binding']}


    def write_snapshot(self, artifact_dir):
        from etf_strategy.src.market_data_store import upsert_bars
        import duckdb
        self.db_path = (artifact_dir / 'market_data.duckdb').resolve()
        data = self.adapter.bars.copy()
        data['ts'] = data['date']
        data['timeframe'] = '1d'
        data['market'] = 'a_share'
        if 'adjusted' not in data:
            data['adjusted'] = True
        upsert_bars(data, self.db_path)
        metadata = data.sort_values('date').groupby('symbol').tail(1)
        columns = [c for c in ('market', 'symbol', 'name', 'listed_date', 'delisted_date',
                    'industry_level1', 'industry_level2', 'industry_level3') if c in metadata]
        metadata = metadata[columns]
        if not self.adapter.history.empty:
            history = self.adapter.history.copy()
            for c in ('name', 'industry_level1', 'industry_level2', 'industry_level3'):
                if c in history:
                    metadata[c] = metadata.symbol.map(history.set_index('symbol')[c])
        with duckdb.connect(str(self.db_path)) as con:
            con.register('frozen_metadata', metadata)
            con.execute('DROP TABLE IF EXISTS market_universe')
            con.execute('CREATE TABLE market_universe AS SELECT * FROM frozen_metadata')
        return ['market_data.duckdb']


class WizardResearchBackend:
    def __init__(self, db_path='auto', cache_dir=None, *, calendar=None, provider=None, adapter=None):
        self.db_path = db_path
        self.cache_dir = Path(cache_dir or Path(__file__).resolve().parents[2] / '.alphalab/wizard-data')
        self.calendar = calendar
        self.provider = provider
        self.adapter = adapter

    def _validate_scope(self, scope):
        if scope.get('market', 'a_share') != 'a_share':
            raise ValueError('当前仅支持 A 股日线')
        start, end = parse_date(scope['start_date']), parse_date(scope['end_date'])
        if start > end:
            raise ValueError('开始日期必须早于结束日期')
        if start < date(2021, 1, 1) or end > date(2025, 12, 31):
            raise ValueError('首版支持 2021～2025 年区间')
        mode = scope.get('selection_mode', 'manual')
        if mode not in {'manual', 'rule'}:
            raise ValueError('选股方式必须是 manual 或 rule')
        if scope.get('quality_mode', 'strict') not in {'strict', 'exploratory'}:
            raise ValueError('数据质量模式无效')
        symbols = scope.get('symbols', [])
        if not isinstance(symbols, list) or any(not re.fullmatch(r'[036489]\d{5}', str(s)) for s in symbols):
            raise ValueError('股票代码必须为六位 A 股代码')
        if len(symbols) != len(set(symbols)):
            raise ValueError('股票代码重复，请删除重复项')
        if mode == 'manual' and not symbols:
            raise ValueError('请至少输入一只股票')
        if mode == 'rule' and scope.get('rule_version', 'fixed_v0') != 'fixed_v0':
            raise ValueError('当前仅支持 fixed_v0 规则')
        top_n = scope.get('top_n', 10)
        if isinstance(top_n, bool) or int(top_n) != float(top_n) or not 1 <= int(top_n) <= 500:
            raise ValueError('候选数量必须是 1～500 的整数')
        return start, end

    def _calendar_dates(self, start, end):
        if self.calendar is not None:
            return sorted({parse_date(d) for d in self.calendar(start, end) if start <= parse_date(d) <= end})
        target = self.cache_dir / 'calendar.json'
        if target.exists():
            payload = json.loads(target.read_text())
            if payload['start'] <= str(start) and payload['end'] >= str(end):
                return [parse_date(d) for d in payload['dates'] if str(start) <= d <= str(end)]
        raise ValueError('可信交易日历尚未准备；点击补齐所需数据获取 BaoStock 交易日历')

    def _dates(self, scope):
        start, end = self._validate_scope(scope)
        sessions = self._calendar_dates(start - timedelta(days=450), end)
        holding = [d for d in sessions if start <= d <= end]
        if len(holding) < 2:
            raise ValueError('区间须包含建仓日及至少一个后续交易日')
        prior = [d for d in sessions if d < holding[0]]
        count = 120 if scope.get('selection_mode') == 'rule' else 1
        if len(prior) < count:
            raise ValueError('交易日历不足以确定信号日及预热区间')
        required = prior[-count:] + holding
        return {'requested_start_date': str(start), 'requested_end_date': str(end),
                'signal_date': str(prior[-1]), 'entry_date': str(holding[0]),
                'exit_date': str(holding[-1]), 'warmup_start_date': str(prior[-count]),
                'warmup_sessions': count, 'horizon': len(holding) - 1,
                'calendar_source': 'injected' if self.calendar else 'baostock.query_trade_dates'}, required

    def _load(self, dates, scope):
        start, end = parse_date(dates['warmup_start_date']), parse_date(dates['exit_date'])
        symbols = scope.get('symbols') or None
        sources, frames, adapters = [], [], []
        if self.adapter is not None:
            adapters.append(self.adapter)
            sources.append({'source': 'injected-adapter'})
        else:
            try:
                binding = auto_bind_research_db(self.db_path, start_date=start, end_date=end)
                adapters.append(DuckDBMarketDataAdapter(binding.db_path, binding.universe_db_path, binding.industry_db_path))
                sources.append(binding.to_dict())
            except DataBindingError:
                pass
        cache = self.cache_dir / 'market_data.duckdb'
        if cache.exists() and all(getattr(a, 'db_path', None) != cache for a in adapters):
            sidecar = self.cache_dir / 'universe.duckdb'
            adapters.append(DuckDBMarketDataAdapter(cache, sidecar if sidecar.exists() else None))
            sources.append({'source': 'wizard-cache', 'db_path': str(cache)})
        history = pd.DataFrame()
        for adapter in adapters:
            frame = adapter.load(start, end, 'a_share', symbols)
            if not frame.empty:
                frames.append(_normalise_dates(frame))
            loader = getattr(adapter, 'load_universe_as_of', None)
            if loader:
                candidate = loader(parse_date(dates['signal_date']), 'a_share', symbols)
                if candidate is not None and not candidate.empty:
                    history = _merge_history(history, candidate)
        # Overlay cached provider shards; duplicates *within* a source survive quality checks.
        data = pd.DataFrame()
        for frame in frames:
            if not data.empty:
                # Missing provider quantities cannot erase verified source facts.
                # Zero is a real response, so only absent/nonfinite values fall back.
                previous = data.drop_duplicates(['symbol', 'date'], keep='last').set_index(['symbol', 'date'])
                frame = frame.copy()
                keys = pd.MultiIndex.from_frame(frame[['symbol', 'date']])
                for column in ('volume', 'amount'):
                    if column not in previous:
                        continue
                    incoming = pd.to_numeric(frame.get(column, pd.Series(index=frame.index, dtype=float)), errors='coerce')
                    prior = pd.to_numeric(previous[column].reindex(keys), errors='coerce')
                    missing = ~incoming.map(lambda v: pd.notna(v) and math.isfinite(v))
                    values = prior.where(prior.map(lambda v: pd.notna(v) and math.isfinite(v))).to_numpy()
                    frame[column] = incoming.where(~missing, pd.Series(values, index=frame.index))
                data = data[~pd.MultiIndex.from_frame(data[['symbol', 'date']]).isin(keys)]
            data = pd.concat([data, frame], ignore_index=True)
        if frames and not data.empty:
            metadata = pd.concat(frames, ignore_index=True).sort_values('date').groupby('symbol').last()
            for column in ('name', 'listed_date', 'delisted_date', 'industry_level1', 'industry_level2', 'industry_level3'):
                if column in metadata:
                    values = data.symbol.map(metadata[column])
                    data[column] = data[column].fillna(values) if column in data else values
        from .data_binding import default_research_universe_cache_path, default_research_industry_cache_path
        from .universe_history import load_universe_as_of, load_industry_snapshot
        universe_paths = [self.cache_dir / 'universe.duckdb']
        industry_paths = []
        if self.adapter is None:
            universe_paths.append(default_research_universe_cache_path())
            industry_paths.append(default_research_industry_cache_path())
            for adapter in adapters:
                if getattr(adapter, 'db_path', None):
                    universe_paths.append(adapter.db_path.parent / 'market_universe_history.duckdb')
                    industry_paths.append(adapter.db_path.parent / 'market_industry_snapshot.duckdb')
        for sidecar in dict.fromkeys(universe_paths):
            if sidecar.exists():
                candidate = load_universe_as_of(sidecar, parse_date(dates['signal_date']), 'a_share', tuple(symbols) if symbols else None)
                if not candidate.empty:
                    history = _merge_history(history, candidate)
                    sources.append({'source': 'existing-universe-sidecar', 'universe_db_path': str(sidecar)})
        if not data.empty and not history.empty:
            metadata = history.set_index('symbol')
            for column in ('name', 'industry_level1', 'industry_level2', 'industry_level3'):
                if column in metadata:
                    values = data.symbol.map(metadata[column])
                    data[column] = data[column].fillna(values) if column in data else values
        if not data.empty:
            for sidecar in dict.fromkeys(industry_paths):
                if sidecar.exists():
                    snapshot = load_industry_snapshot(sidecar, 'a_share', tuple(symbols) if symbols else None)
                    if not snapshot.empty:
                        metadata = snapshot.drop_duplicates('symbol').set_index('symbol')
                        for column in ('industry_level1', 'industry_level2', 'industry_level3'):
                            if column in metadata:
                                values = data.symbol.map(metadata[column])
                                data[column] = data[column].fillna(values) if column in data else values
                        sources.append({'source': 'current-industry-sidecar', 'industry_db_path': str(sidecar)})
        status_file = self.cache_dir / 'trading_status.json'
        if not data.empty and status_file.exists():
            states = json.loads(status_file.read_text())
            values = pd.Series([states.get(f"{row.symbol}:{row.date.date()}", {}).get('tradestatus') for row in data.itertuples()], index=data.index)
            data['tradestatus'] = values.combine_first(data.get('tradestatus', pd.Series(index=data.index, dtype=object)))
        execution_file = self.cache_dir / 'execution_prices.json'
        if not data.empty and execution_file.exists():
            execution = json.loads(execution_file.read_text())
            values = data.symbol.map(lambda symbol: execution.get(f"{symbol}:{dates['entry_date']}"))
            data['execution_open'] = data['execution_open'].fillna(values) if 'execution_open' in data else values
        return data, history, sources

    def _inspect(self, scope):
        result, data, history = self._inspect_raw(scope)
        return finalize_readiness(result), data, history

    def _inspect_raw(self, scope):
        result = {'status': 'BLOCKED', 'issues': [], 'dates': {}, 'coverage': [],
                  'data_identity': None, 'binding': {}, 'requirement_id': json_hash(scope),
                  'warnings': []}
        current_symbol = None
        def issue(code, message, action='修改研究范围或配置数据来源后重新检查', **details):
            result['issues'].append(make_issue(code, message, action, symbol=current_symbol, **details))
        try:
            self._validate_scope(scope)
        except (ValueError, KeyError, TypeError) as exc:
            issue('INVALID_SCOPE', str(exc), '修改研究范围')
            return result, pd.DataFrame(), pd.DataFrame()
        try:
            dates, required = self._dates(scope)
            result['dates'] = dates
        except (ValueError, OSError) as exc:
            issue('CALENDAR_UNAVAILABLE', str(exc))
            return result, pd.DataFrame(), pd.DataFrame()
        try:
            data, history, sources = self._load(dates, scope)
            result['binding'] = {'sources': sources}
        except Exception as exc:
            issue('SOURCE_UNAVAILABLE', f'读取历史数据失败：{exc}')
            return result, pd.DataFrame(), pd.DataFrame()
        strict = scope.get('quality_mode', 'strict') == 'strict'
        rule = scope.get('selection_mode') == 'rule'
        symbols = list(scope.get('symbols', []))
        if rule:
            if history.empty:
                issue('PIT_UNAVAILABLE', '规则选股需要信号日历史股票池，不能用当前全市场名单替代', '补齐历史上市信息或改用手选股票', resolution='download')
            else:
                symbols = sorted(history.symbol.astype(str).unique())
                data = data[data.symbol.isin(symbols)] if not data.empty else data
        if strict:
            required_industry = ['industry_level1', 'industry_level2', 'industry_level3']
            complete = not history.empty and all(c in history for c in required_industry)
            complete = complete and history[required_industry].notna().all().all()
            complete = complete and history[required_industry].astype(str).apply(lambda col: col.str.strip().ne('')).all().all()
            complete = complete and set(symbols).issubset(set(history.symbol.astype(str)))
            if not complete:
                issue('PIT_UNAVAILABLE', '正式研究需要历史上市状态及行业生效区间；当前来源无法证明完整 PIT',
                      '提供完整历史元数据，或明确选择探索模式后重新检查')
        else:
            result['warnings'].append('探索结果仅供描述，不构成严格历史证据；当前行业信息可能含前视偏差')
        if not rule:
            result['warnings'].append('手选股票清单存在事后选择偏差，不代表无偏历史股票池')
        signal = pd.Timestamp(dates['signal_date'])
        selected_symbols = set() if rule else set(symbols)
        if rule and not data.empty:
            before = data[data.date <= signal]
            try:
                candidates, _ = FixedV0Plugin().score(before.copy(deep=True))
                selected_symbols = set(candidates[candidates.eligible].sort_values(
                    ['total_score', 'symbol'], ascending=[False, True]).head(int(scope.get('top_n', 10))).symbol)
            except (ValueError, KeyError, TypeError):
                # Price/field diagnostics below explain incomplete historical inputs.
                selected_symbols = set()
        result['selected_symbols'] = sorted(selected_symbols)
        if rule:
            result['warnings'].append('规则仅要求选中股票覆盖完整持有区间；未选中股票保留信号日前筛选证据。向导不计算全股票池基准，避免缺失或退市造成幸存者偏差。')
        all_expected = {pd.Timestamp(d) for d in required}
        for symbol in symbols:
            current_symbol = symbol
            issue_count = len(result['issues'])
            part = data[data.symbol.astype(str).eq(symbol)] if not data.empty else pd.DataFrame()
            metadata_row = part.ffill().iloc[-1] if not part.empty else pd.Series(dtype=object)
            history_rows = history[history.symbol.astype(str).eq(symbol)] if not history.empty else pd.DataFrame()
            listed = pd.to_datetime(metadata_row.get('listed_date'), errors='coerce')
            delisted = pd.to_datetime(metadata_row.get('delisted_date'), errors='coerce')
            # BaoStock stock-basic intervals describe listing, whereas arbitrary
            # PIT industry effective_to dates are not evidence of delisting.
            listing_history = history_rows[history_rows.source.eq('baostock')] if 'source' in history_rows else pd.DataFrame()
            if not listing_history.empty:
                if pd.isna(listed):
                    listed = pd.to_datetime(listing_history.iloc[0].get('effective_from'), errors='coerce')
                if pd.isna(delisted):
                    delisted = pd.to_datetime(listing_history.iloc[0].get('effective_to'), errors='coerce')
            selected = symbol in selected_symbols
            expected = all_expected if selected or not rule else {d for d in all_expected if d <= signal}
            if pd.notna(listed):
                expected = {d for d in expected if d >= listed}
            if pd.notna(delisted):
                expected = {d for d in expected if d < delisted}
            actual = set(part.date) if not part.empty else set()
            missing = sorted(expected - actual)
            if rule and not selected:
                part = part[part.date <= signal] if not part.empty else part
            result['coverage'].append({'symbol': symbol, 'name': str(part.iloc[-1].get('name', symbol)) if not part.empty else symbol,
                'status': 'MISSING' if missing else 'READY', 'selected': selected, 'required_sessions': len(expected),
                'listed_date': str(listed.date()) if pd.notna(listed) else None,
                'last_required_date': str(max(expected).date()) if expected else None,
                'available_sessions': len(actual & expected), 'missing_dates': [str(d.date()) for d in missing]})
            if missing:
                issue('MISSING_BARS', f'{symbol} 缺少 {len(missing)} 个交易日行情：{missing[0].date()}～{missing[-1].date()}（包含预热）', dates=missing, phase='coverage')
            if part.empty:
                continue
            anchors = pd.to_numeric(part.get('execution_open', pd.Series(dtype=float)), errors='coerce').dropna()
            if selected and self.adapter is None and (anchors.empty or not anchors.map(math.isfinite).all() or (anchors <= 0).any()):
                issue('EXECUTION_PRICE_UNAVAILABLE', f'{symbol} 缺少建仓日未复权开盘价，无法核算真实整手和本金')
                if result['coverage'][-1]['status'] == 'READY':
                    result['coverage'][-1]['status'] = 'ENTRY_PRICE_MISSING'
            prices = part[['open', 'high', 'low', 'close']].apply(pd.to_numeric, errors='coerce')
            invalid = (~prices.apply(lambda col: col.map(math.isfinite))).any(axis=1) | (prices <= 0).any(axis=1)
            invalid |= prices.high < prices[['open', 'close']].max(axis=1)
            invalid |= prices.low > prices[['open', 'close']].min(axis=1)
            if invalid.any() or part.duplicated(['symbol', 'date']).any():
                issue('INVALID_BARS', f'{symbol} 存在重复、非法或不完整 OHLC', dates=part.loc[invalid | part.duplicated(['symbol','date'], keep=False), 'date'], phase='prices')
            adjustments = set(part.get('adjustment', pd.Series(['unknown'])).astype(str).str.lower())
            result['coverage'][-1]['adjustment'] = next(iter(adjustments)) if len(adjustments) == 1 else 'mixed'
            if len(adjustments) != 1 or adjustments & {'unknown', 'none', 'nan', ''}:
                issue('ADJUSTMENT_UNAVAILABLE', f'{symbol} 复权口径未知或不一致', dates=part.date, phase='prices', evidence={'adjustments': sorted(adjustments)})
            result['issues'].extend(liquidity_issues(part, symbol, rule, dates))
            # A historical identity must resolve by listing dates or an effective snapshot.
            in_history = not history.empty and symbol in set(history.symbol.astype(str))
            if not in_history and pd.isna(listed):
                issue('IDENTITY_UNAVAILABLE', f'{symbol} 缺少可验证的上市日期和名称')
            if pd.notna(listed) and listed > pd.Timestamp(dates['signal_date']):
                issue('NOT_LISTED', f'{symbol} 在信号日尚未上市')
            if selected and pd.notna(delisted) and delisted <= pd.Timestamp(dates['exit_date']):
                issue('DELISTED', f'{symbol} 在持有区间内退市；V1 不支持退市清算')
            price_codes = {'INVALID_BARS', 'ADJUSTMENT_UNAVAILABLE', 'UNTRADABLE', 'MISSING_FACTOR_FIELD', 'SUSPENDED', 'TRADING_STATUS_UNKNOWN'}
            if any(i['code'] in price_codes for i in result['issues'][issue_count:]):
                result['coverage'][-1]['status'] = 'INVALID'
        current_symbol = None
        if not symbols:
            issue('EMPTY_UNIVERSE', '没有可解析的历史股票范围')
        result['symbols'] = symbols
        result['data_identity'] = json_hash({'data': _jsonable(data.sort_values(['symbol', 'date']).to_dict('records')) if not data.empty else [],
            'history': _jsonable(history.to_dict('records')), 'dates': dates, 'scope': scope})
        result['readiness_id'] = json_hash({'requirement': result['requirement_id'], 'data': result['data_identity']})
        if not result['issues']:
            result['status'] = 'READY'
        return _jsonable(result), data, history

    def inspect(self, scope):
        return self._inspect(scope)[0]

    def _freeze(self, scope, readiness):
        if not readiness or readiness.get('status') != 'READY':
            raise ValueError('DATA_NOT_READY：请先检查并补齐数据')
        current, data, history = self._inspect(scope)
        if current['status'] != 'READY' or current['data_identity'] != readiness.get('data_identity') or current['requirement_id'] != readiness.get('requirement_id'):
            raise ValueError('DATA_CHANGED：数据或范围已变化，请重新检查数据')
        selection_bars = data[data.date <= pd.Timestamp(current['dates']['signal_date'])].copy()
        if scope.get('selection_mode') == 'rule':
            selection_bars, _ = _apply_universe_mode(selection_bars, parse_date(current['dates']['signal_date']), 'point-in-time', history if not history.empty else None)
            data = data[(data.date <= pd.Timestamp(current['dates']['signal_date'])) | data.symbol.isin(current['selected_symbols'])].copy()
        # Preserve adjusted returns while expressing shares in actual entry-day
        # cash-market units. Raw entry anchors are part of the data identity.
        if 'execution_open' in data:
            for symbol, part in data.groupby('symbol'):
                entry = part[part.date.eq(pd.Timestamp(current['dates']['entry_date']))]
                anchors = pd.to_numeric(part.execution_open, errors='coerce').dropna()
                if not anchors.empty and not entry.empty:
                    anchor = float(anchors.iloc[-1])
                    if not math.isfinite(anchor) or anchor <= 0 or entry.empty:
                        raise ValueError('EXECUTION_PRICE_UNAVAILABLE：未复权开盘价无效')
                    scale = anchor / float(entry.iloc[0]['open'])
                    data.loc[part.index, ['open', 'high', 'low', 'close']] *= scale
        return current, FrozenAdapter(data, history, selection_bars)

    def _spec(self, scope, portfolio, readiness):
        weighting = portfolio.get('weighting', 'equal')
        manual = scope.get('selection_mode') != 'rule'
        if weighting not in ({'equal', 'custom'} if manual else {'equal', 'score'}):
            raise ValueError('当前选股方式不支持该权重方式')
        def number(key, default):
            value = float(portfolio.get(key, default))
            if not math.isfinite(value):
                raise ValueError(f'{key} 必须是有限数值')
            return value
        cash = number('initial_cash', 100000.)
        commission = number('commission_rate', .0003)
        slippage = number('slippage_rate', .0005)
        if cash <= 0 or not 0 <= commission < 1 or not 0 <= slippage < 1:
            raise ValueError('本金必须为正，成本费率须在 [0, 1) 内')
        minimum = number('min_holdings', 1)
        if minimum < 0 or int(minimum) != minimum:
            raise ValueError('最低持仓数必须是非负整数')
        weights = portfolio.get('weights', {}) if weighting == 'custom' else {}
        if not isinstance(weights, dict):
            raise ValueError('显式权重格式无效')
        weights = {str(k): float(v) for k, v in weights.items()}
        if weighting == 'custom' and (set(weights) != set(readiness['symbols']) or
                any(not math.isfinite(v) or v < 0 for v in weights.values()) or sum(weights.values()) > 1 + 1e-9):
            raise ValueError('显式权重必须覆盖所选股票、非负且总和不超过 100%')
        name = str(portfolio.get('name', '')).strip()
        if not name:
            raise ValueError('请填写组合名称')
        return ResearchSpec(requested_date=readiness['dates']['signal_date'],
            horizons=(readiness['dates']['horizon'],), top_n=len(readiness['symbols']) if manual else int(scope.get('top_n', 10)),
            initial_cash=cash, commission_rate=commission, slippage_rate=slippage,
            rule_version='manual_v1' if manual else 'fixed_v0', portfolio_weighting=weighting,
            max_single_weight=portfolio.get('max_single_weight'), max_industry_weight=portfolio.get('max_industry_weight'),
            min_holdings=int(minimum), target_weights=weights,
            universe_mode='point-in-time' if scope.get('quality_mode', 'strict') == 'strict' or not manual else 'observed-history',
            data_quality_mode=scope.get('quality_mode', 'strict'),
            portfolios=(PortfolioSpec('strategy', name=name),),
            wizard_metadata={'scope': scope, 'portfolio': portfolio, 'dates': readiness['dates'],
                             'data_identity': readiness['data_identity'], 'warnings': readiness['warnings'],
                             'benchmark_disabled_reason': '向导不以缺失未来数据的股票池构造基准，以避免幸存者偏差',
                             'holding_method': 'buy_and_hold', 'price_basis': 'adjusted returns anchored to raw entry open when available'})

    def _preview(self, scope, portfolio, current, adapter):
        spec = self._spec(scope, portfolio, current)
        dates = current['dates']
        data = adapter.bars.copy()
        signal = parse_date(dates['signal_date'])
        data, diagnostic = _apply_universe_mode(data, signal, spec.universe_mode,
            adapter.history if not adapter.history.empty else None)
        _validate_universe_quality(diagnostic, spec.data_quality_mode)
        before = data[data.date <= pd.Timestamp(signal)]
        plugin = ManualSelectionPlugin() if spec.rule_version == 'manual_v1' else FrozenRulePlugin(adapter.selection_bars)
        candidates, funnel = plugin.score(before.copy(deep=True))
        factor = factor_definition(plugin)
        candidates = validate_plugin_output(candidates, before, plugin_id=plugin.plugin_id, min_history_days=plugin.min_history_days)
        candidates, _ = _prepare_plugin_candidates(candidates, funnel, before, factor)
        selected = candidates[candidates.eligible].sort_values(['rank', 'symbol']).head(spec.top_n)
        if selected.empty:
            raise ValueError('EMPTY_CANDIDATE_POOL：规则没有候选股票，请修改范围或规则参数')
        if spec.max_industry_weight is not None and selected.industry.eq('UNKNOWN').any():
            raise ValueError('INDUSTRY_UNAVAILABLE：行业约束需要完整行业元数据')
        holdings, status, reasons = _build_portfolio(selected, data[data.date > pd.Timestamp(signal)], parse_date(dates['entry_date']), spec)
        if status != 'OK':
            raise ValueError(f'{status}：组合不可执行，请修改本金或约束；{reasons}')
        raw_notional = holdings.entry_price / (1 + spec.slippage_rate) * holdings.shares
        commission = float((holdings.entry_price * holdings.shares * spec.commission_rate).sum())
        slippage = float((holdings.entry_price * holdings.shares - raw_notional).sum())
        return _jsonable({'status': 'READY', 'holdings': holdings.to_dict('records'),
            'candidates': candidates.to_dict('records'), 'cash_residual': float(spec.initial_cash - raw_notional.sum() - commission - slippage),
            'initial_cash': spec.initial_cash, 'commission': commission, 'slippage': slippage,
            'diagnostics': reasons, 'warnings': current['warnings'], 'dates': dates,
            'data_identity': current['data_identity'], 'portfolio': portfolio}), spec

    def preview(self, scope, portfolio, readiness):
        current, adapter = self._freeze(scope, readiness)
        return self._preview(scope, portfolio, current, adapter)[0]

    def run(self, scope, portfolio, readiness, runs_dir):
        current, adapter = self._freeze(scope, readiness)
        preview, spec = self._preview(scope, portfolio, current, adapter)
        # All execution reads use this copied frame and copied metadata. Provider writes
        # after this point cannot change the run's validated snapshot.
        lab = HistoricalResearchLab(adapter, runs_dir, plugins={'manual_v1': ManualSelectionPlugin(), 'fixed_v0': FrozenRulePlugin(adapter.selection_bars) if scope.get('selection_mode') == 'rule' else FixedV0Plugin()}, data_binding=FrozenBinding(current, adapter))
        report = lab.run(spec)
        summary = _jsonable(report.performance[spec.horizons[0]])
        if summary['status'] != 'COMPLETE' or summary['evaluated_date'] != current['dates']['exit_date']:
            raise ValueError('RUN_INCOMPLETE：运行未覆盖请求的退出日期')
        manifest = json.loads((report.artifact_dir / 'manifest.json').read_text())
        return {'run_id': report.run_id, 'artifact_dir': str(report.artifact_dir), 'manifest': manifest,
                'summary': summary, 'preview': preview, 'review_url': f'/research/review/{report.run_id}/'}

    def prepare(self, scope, progress, cancelled):
        return self._prepare(scope, progress, cancelled, rounds=2)

    def _prepare(self, scope, progress, cancelled, rounds):
        self._validate_scope(scope)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        from .data_repair import validate_and_publish, save_attempt, retry_transient
        bootstrap_attempts = []
        def checkpoint(message):
            if cancelled():
                raise InterruptedError('已取消；已完成数据保留，可继续准备')
            progress(message)
        def bootstrap(action, operation):
            record = {'action': action, 'query': action, 'status': 'completed'}
            try:
                retry_transient(operation, cancelled, progress)
                record['status'] = 'resolved'
            except Exception as exc:
                record.update(status='failed', error=str(exc))
                raise
            finally:
                record['attempt_file'] = str(save_attempt(self.cache_dir, record))
                bootstrap_attempts.append(record)
        checkpoint('检查并准备可信交易日历')
        if self.calendar is None:
            start, end = self._validate_scope(scope)
            start -= timedelta(days=450)
            try:
                self._calendar_dates(start, end)
            except ValueError:
                bootstrap({'kind': 'calendar', 'symbol': None, 'start': str(start), 'end': str(end)}, lambda: self._provision_calendar(start, end))
        dates, _ = self._dates(scope)
        checkpoint('检查本地数据和历史上市身份')
        ready, _, history = self._inspect(scope)
        if ready['status'] == 'READY':
            return ready
        if self.adapter is None and any(a['kind'] == 'universe' for a in ready['repair_plan']['actions']):
            checkpoint('获取 BaoStock 上市与退市历史（不声称提供历史行业）')
            bootstrap({'kind': 'universe', 'symbol': None}, self._provision_universe)
            ready = self.inspect(scope)
        if scope.get('selection_mode') == 'rule' and not ready.get('symbols'):
            return ready
        from etf_strategy.src.market_data_providers import FetchRequest
        from .data_repair import validate_and_publish, save_attempt, retry_transient
        failures, attempts = [], bootstrap_attempts.copy()
        initial_plan = ready['repair_plan']
        for action in initial_plan['actions']:
            symbol, kind = action['symbol'], action['kind']
            if kind == 'universe' and bootstrap_attempts:
                continue
            start, end = action['start'], action['end']
            record = {'plan_id': initial_plan['plan_id'], 'action': action,
                      'before_identity': ready.get('data_identity'), 'query': action}
            checkpoint(f"{'核实交易状态' if kind == 'verify_status' else '修复数据'} {symbol or '历史元数据'}；有效缓存已保留")
            try:
                def execute():
                    if kind == 'verify_status':
                        return self._provision_status(symbol, start, end, cancelled)
                    if kind == 'universe':
                        return self._provision_universe()
                    if kind == 'calendar':
                        return None  # Prepared before date-dependent inspection.
                    if kind == 'bars':
                        if self.provider is not None:
                            frame = self.provider.fetch_ohlcv(FetchRequest('a_share', symbol, '1d', pd.Timestamp(start), pd.Timestamp(end), options={'adjust': 'hfq'}))
                            checkpoint(f'校验 {symbol} 下载分片')
                            validate_and_publish(frame, self.cache_dir / 'market_data.duckdb', symbol, start, end, cancelled=cancelled)
                        else:
                            self._provision_symbol(symbol, start, end, cancelled)
                    if kind == 'entry':
                        if self.provider is not None:
                            day = pd.Timestamp(dates['entry_date'])
                            raw = self.provider.fetch_ohlcv(FetchRequest('a_share', symbol, '1d', day, day, options={'adjust': 'none'}))
                            if raw.empty:
                                raise ValueError('未返回建仓日未复权价格')
                            checkpoint(f'保存 {symbol} 未复权建仓价')
                            self._save_execution_price(symbol, dates['entry_date'], float(raw.iloc[0]['open']))
                        else:
                            self._provision_entry(symbol, dates['entry_date'])
                retry_transient(execute, cancelled, progress)
                record['status'] = 'completed'
            except InterruptedError:
                record['status'] = 'cancelled'
                save_attempt(self.cache_dir, record)
                raise
            except Exception as exc:
                record.update(status='failed', error=str(exc))
                failures.append(make_issue('PROVIDER_FAILED', f'{symbol or kind} 处理失败：{exc}',
                                          '重试失败项；已验证缓存保留', symbol=symbol,
                                          resolution='verify' if kind == 'verify_status' else 'download',
                                          evidence={'repair_action': action}))
            record['execution_record'] = str(save_attempt(self.cache_dir, record))
            attempts.append(record)
        checkpoint('重新验证逐股覆盖、交易状态、复权和历史身份')
        result = self.inspect(scope)
        unresolved = {(a['symbol'], a['kind']) for a in result['repair_plan']['actions']}
        for record in attempts:
            action = record['action']
            record['after_identity'] = result.get('data_identity')
            record['result'] = {'data_identity': result.get('data_identity'), 'remaining_issue_ids': [i['issue_id'] for i in result['issues']]}
            if record['status'] == 'completed':
                record['status'] = 'unresolved' if (action['symbol'], action['kind']) in unresolved else 'resolved'
            record['attempt_file'] = str(save_attempt(self.cache_dir, record))
        # The final inspection is authoritative; stale request failures cannot veto READY.
        if result['status'] != 'READY':
            result['issues'].extend(failures)
        result['repair_attempts'] = attempts
        result['repair_summary'] = {'attempted': len(attempts),
            'resolved': sum(a['status'] == 'resolved' for a in attempts),
            'failed': sum(a['status'] == 'failed' for a in attempts),
            'unchanged': sum(a['status'] == 'unresolved' for a in attempts)}
        prior = {(a['symbol'], a['kind']) for a in initial_plan['actions']}
        # Newly discovered holdings/entry anchors may need another phase, never repeat unchanged work.
        next_actions = {(a['symbol'], a['kind']) for a in result['repair_plan']['actions']}
        if not failures and rounds > 1 and next_actions and not next_actions.intersection(prior):
            following = self._prepare(scope, progress, cancelled, rounds=rounds-1)
            following['repair_attempts'] = attempts + following.get('repair_attempts', [])
            following['repair_summary'] = {k: result['repair_summary'][k] + following.get('repair_summary', {}).get(k, 0) for k in result['repair_summary']}
            return following
        return result

    def _provision_status(self, symbol, start, end, cancelled=lambda: False):
        """Keep dated status evidence separate from price fields and adjustment units."""
        import tempfile
        if self.provider is not None:
            fetch = getattr(self.provider, 'fetch_trading_status', None)
            if fetch is None:
                raise ValueError('当前来源未提供交易状态查询；不能确认停牌')
            frame = fetch(symbol, start, end)
        else:
            code = """import baostock as bs, json, sys
login=bs.login(); assert login.error_code == '0', login.error_msg
try:
 r=bs.query_history_k_data_plus(sys.argv[1], 'date,tradestatus', start_date=sys.argv[2], end_date=sys.argv[3], frequency='d', adjustflag='3')
 assert r.error_code == '0', r.error_msg
 rows=[]
 while r.next(): rows.append(dict(zip(r.fields,r.get_row_data())))
 from pathlib import Path
 Path(sys.argv[4]).write_text(json.dumps(rows))
finally: bs.logout()
"""
            with tempfile.TemporaryDirectory(dir=self.cache_dir) as temporary:
                target = Path(temporary) / 'status.json'
                exchange = 'sh' if symbol.startswith(('5','6','9')) else 'sz'
                self._child(code, [f'{exchange}.{symbol}', start, end, target])
                frame = pd.DataFrame(json.loads(target.read_text()))
        if frame.empty or not {'date','tradestatus'}.issubset(frame.columns):
            raise ValueError('来源未返回有效交易状态；保留未核实问题')
        frame = frame.copy()
        frame['date'] = pd.to_datetime(frame['date'], errors='raise').dt.strftime('%Y-%m-%d')
        frame['tradestatus'] = frame.tradestatus.astype(str)
        if frame.date.duplicated().any() or not frame.date.between(str(start), str(end)).all() or not frame.tradestatus.isin(['0','1']).all():
            raise ValueError('来源交易状态日期或字段无效')
        if cancelled():
            raise InterruptedError('已取消；交易状态尚未发布')
        target = self.cache_dir / 'trading_status.json'
        values = json.loads(target.read_text()) if target.exists() else {}
        for row in frame.itertuples():
            values[f'{symbol}:{row.date}'] = {'tradestatus': row.tradestatus,
                'source': 'configured-provider' if self.provider is not None else 'baostock',
                'checked_at': pd.Timestamp.now(tz='UTC').isoformat()}
        temporary = target.with_suffix('.pending')
        temporary.write_text(json.dumps(values, ensure_ascii=False))
        temporary.replace(target)

    def _child(self, code, args):
        try:
            result = subprocess.run([sys.executable, '-c', code, *map(str, args)], capture_output=True, text=True, timeout=45, check=False)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError('BaoStock 请求超时；请重试') from exc
        if result.returncode:
            from .data_repair import _transient
            if _transient(RuntimeError((result.stderr or '') + (result.stdout or ''))):
                raise ConnectionError('BaoStock 网络连接失败；已验证缓存保留')
            raise ValueError('BaoStock 数据源请求失败；请检查网络后重试')
        return result

    def _provision_calendar(self, start, end):
        # A subprocess bounds provider socket hangs and isolates BaoStock login state.
        code = '''import baostock as bs, json, sys
login=bs.login(); assert login.error_code == '0', login.error_msg
try:
 r=bs.query_trade_dates(start_date=sys.argv[1],end_date=sys.argv[2]); assert r.error_code == '0', r.error_msg
 rows=[]
 while r.next(): rows.append(r.get_row_data())
 assert rows and rows[0][0] == sys.argv[1] and rows[-1][0] == sys.argv[2], 'incomplete calendar'
 from pathlib import Path
 Path(sys.argv[3]).write_text(json.dumps({'start':sys.argv[1],'end':sys.argv[2],'dates':[d for d,flag in rows if flag=='1'],'source':'baostock.query_trade_dates'}))
finally: bs.logout()
'''
        target = self.cache_dir / 'calendar.json'
        temporary = target.with_suffix('.pending')
        self._child(code, [start, end, temporary])
        temporary.replace(target)

    def _provision_universe(self):
        code = '''from alphalab.research.universe_history import fetch_baostock_universe_history
import sys
fetch_baostock_universe_history(db_path=sys.argv[1])
'''
        self._child(code, [self.cache_dir / 'universe.duckdb'])

    def _provision_symbol(self, symbol, start, end, cancelled=lambda: False):
        import tempfile
        from .data_repair import validate_and_publish
        code = """import sys,pandas as pd
from etf_strategy.src.market_data_providers import BaoStockProvider,FetchRequest
frame=BaoStockProvider().fetch_ohlcv(FetchRequest('a_share',sys.argv[1],'1d',pd.Timestamp(sys.argv[2]),pd.Timestamp(sys.argv[3]),options={'adjust':'hfq'}))
assert not frame.empty,'No bars returned'
frame.to_json(sys.argv[4],orient='table',date_format='iso')
"""
        with tempfile.TemporaryDirectory(dir=self.cache_dir) as temporary:
            target = Path(temporary) / 'response.json'
            self._child(code, [symbol, start, end, target])
            frame = pd.read_json(target, orient='table')
            validate_and_publish(frame, self.cache_dir / 'market_data.duckdb', symbol, start, end, cancelled=cancelled)

    def _save_execution_price(self, symbol, day, value):
        if not math.isfinite(value) or value <= 0:
            raise ValueError('未复权开盘价无效')
        target = self.cache_dir / 'execution_prices.json'
        values = json.loads(target.read_text()) if target.exists() else {}
        values[f'{symbol}:{day}'] = value
        temporary = target.with_suffix('.pending')
        temporary.write_text(json.dumps(values))
        temporary.replace(target)

    def _provision_entry(self, symbol, day):
        code = """import sys,pandas as pd,json
from pathlib import Path
from etf_strategy.src.market_data_providers import BaoStockProvider,FetchRequest
frame=BaoStockProvider().fetch_ohlcv(FetchRequest('a_share',sys.argv[1],'1d',pd.Timestamp(sys.argv[2]),pd.Timestamp(sys.argv[2]),options={'adjust':'none'}))
assert not frame.empty,'No raw entry price'
Path(sys.argv[3]).write_text(json.dumps(float(frame.iloc[0]['open'])))
"""
        temporary = self.cache_dir / 'raw-entry.pending'
        self._child(code, [symbol, day, temporary])
        self._save_execution_price(symbol, day, float(json.loads(temporary.read_text())))
        temporary.unlink(missing_ok=True)
