"""Verified identifier changes; never infer identity from today's name or price."""
from __future__ import annotations

from collections import Counter
import pandas as pd
from .data_readiness import make_issue

IDENTITY_VERSION = 'security-code-intervals-v1'
CODE_CHANGES = ({
    'old_symbol': '300114', 'new_symbol': '302132', 'effective_date': '2025-02-17',
    'old_name': '中航电测', 'new_name': '中航成飞', 'share_conversion_ratio': 1,
    'announced_at': '2025-02-07',
    'source_url': 'https://static.cninfo.com.cn/finalpage/2025-02-07/1222485220.PDF',
},)


def resolve_signal_symbols(symbols, signal, *, rule):
    """Fix duplicate aliases in a rule universe; never rewrite manual selection.

    Keep/add the active identifier even when its data is absent: ordinary quality
    checks must then block and request the missing historical evidence. A code
    correction must never shrink the economic universe to hide a missing series.
    """
    output = set(symbols)
    issues = []
    for event in CODE_CHANGES:
        before = pd.Timestamp(signal) < pd.Timestamp(event['effective_date'])
        active = event['old_symbol'] if before else event['new_symbol']
        inactive = event['new_symbol'] if before else event['old_symbol']
        if inactive not in output:
            continue
        if rule:
            output.discard(inactive)
            output.add(active)
        issues.append(make_issue('SYMBOL_ALIAS_RESOLVED' if rule else 'SYMBOL_NOT_EFFECTIVE',
            f'{inactive} 在信号日不是有效代码；当时应使用 {active}' + ('，同一证券只计一次' if rule else ''),
            '按官方代码生效区间保留当时标识，不更改原始数据' if rule else f'核实研究意图后使用当时的证券代码 {active}',
            symbol=inactive, resolution='none' if rule else 'user', severity='info' if rule else 'blocking',
            evidence={**event, 'active_symbol': active, 'signal_date': str(pd.Timestamp(signal).date()), 'identity_policy': IDENTITY_VERSION}))
    return sorted(output), issues


def transition_issue(symbol, signal, exit_date):
    for event in CODE_CHANGES:
        if symbol == event['old_symbol'] and pd.Timestamp(signal) < pd.Timestamp(event['effective_date']) <= pd.Timestamp(exit_date):
            return make_issue('SYMBOL_TRANSITION_UNSUPPORTED',
                f'{symbol} 持有期间变更为 {event["new_symbol"]}；须验证跨代码行情与持仓连续性',
                '核实新旧代码复权因子和持仓映射，不能把代码变更当作退市清算',
                symbol=symbol, evidence=event)
    return None


def adjustment_summary(coverage):
    counts = dict(sorted(Counter(r.get('adjustment', 'unknown') for r in coverage).items()))
    return {'stock_counts': counts,
            'mixed_symbols': [r['symbol'] for r in coverage if r.get('adjustment') == 'mixed'],
            'unknown_symbols': [r['symbol'] for r in coverage if r.get('adjustment', 'unknown') not in {'qfq','hfq','mixed'}],
            'source_stock_counts': dict(sorted(Counter(source for r in coverage for source in r.get('price_sources', [])).items())),
            'execution_basis': '建仓数量以未复权开盘价为锚；研究收益使用单股连续复权序列',
            'limitation': '不同股票使用前/后复权不等于同一股票混用口径。当前规则依赖收益率和均线关系，只有同股序列之间为固定正比例时才具有尺度不变性；标签一致不能证明算法、因子或跨来源收益一致。不可直接比较复权价格高低，也不能把复权价当作实际成交报价。'}


def pit_evidence(history, symbols, required_fields):
    covered = set(history.symbol.astype(str)) if 'symbol' in history else set()
    relevant = history[history.symbol.astype(str).isin(symbols)] if 'symbol' in history else history
    missing = {}
    for field in required_fields:
        absent = len(symbols) if field not in relevant else int((relevant[field].isna() | relevant[field].astype(str).str.strip().eq('')).sum()) + len(set(symbols)-covered)
        if absent:
            missing[field] = absent
    return {'required_symbols': len(symbols), 'missing_identity_symbols': sorted(set(symbols)-covered),
            'missing_fields': missing,
            'required_history_columns': ['symbol','effective_from','effective_to','status','source','snapshot_id',*required_fields],
            'import_contract': '历史行业记录必须有真实生效区间；当前行业快照不能回填为历史事实',
            'candidate_source_documentation': 'https://tushare.pro/document/2?doc_id=335'}
