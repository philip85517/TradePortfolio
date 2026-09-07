"""Date-specific evidence and server-owned recovery plans for research inputs."""
from __future__ import annotations

import numpy as np
import pandas as pd
from ..utils import json_hash


def make_issue(code, message, action, *, symbol=None, dates=(), phase='identity',
               resolution=None, evidence=None, severity='blocking'):
    defaults = {
        'CALENDAR_UNAVAILABLE': 'download', 'IDENTITY_UNAVAILABLE': 'download',
        'MISSING_BARS': 'download', 'INVALID_BARS': 'download',
        'ADJUSTMENT_UNAVAILABLE': 'download', 'EXECUTION_PRICE_UNAVAILABLE': 'download',
        'MISSING_FACTOR_FIELD': 'download', 'TRADING_STATUS_UNKNOWN': 'verify',
        'SUSPENDED': 'unsupported', 'DELISTED': 'unsupported',
    }
    resolution = resolution or defaults.get(code, 'user')
    days = sorted({str(pd.Timestamp(d).date()) for d in dates})
    ranges = [{'start': d, 'end': d} for d in days]
    identity = dict(code=code, symbol=symbol, phase=phase, date_ranges=ranges)
    category = {'download': 'data_missing', 'verify': 'status_unknown',
                'unsupported': 'market_event', 'none': 'market_event', 'user': 'source_capability'}[resolution]
    if code in {'INVALID_BARS', 'ADJUSTMENT_UNAVAILABLE', 'ADJUSTMENT_STANDARDIZATION_REQUIRED'}:
        category = 'data_conflict'
    actions = {'verify': '先核实这些日期的交易状态；不会将零成交量自动认定为停牌',
               'unsupported': '当前回测不支持此市场事件；补数无法解决。修改范围会改变实验样本',
               'download': '仅修复计划中的缺口，完成后重新校验'}
    return {**identity, 'issue_id': json_hash(identity), 'message': message,
            'action': actions.get(resolution, action), 'category': category,
            'resolution': resolution, 'severity': severity,
            'affected_rows': len(days), 'evidence': evidence or {},
            'blocking_scope': 'research' if severity == 'blocking' else 'none'}


def liquidity_issues(part, symbol, rule, dates, *, policy='legacy'):
    """Never infer suspension from zero volume; coalesce verified event fields."""
    volume = pd.to_numeric(part.get('volume', pd.Series(index=part.index, dtype=float)), errors='coerce')
    amount = pd.to_numeric(part.get('amount', pd.Series(index=part.index, dtype=float)), errors='coerce')
    status = part.get('tradestatus', pd.Series('', index=part.index)).fillna('').astype(str).str.replace(r'\.0$', '', regex=True)
    bad_volume = ~np.isfinite(volume.to_numpy(dtype=float, na_value=np.nan)) | volume.le(0).fillna(True)
    bad_amount = (~np.isfinite(amount.to_numpy(dtype=float, na_value=np.nan)) | amount.lt(0).fillna(True)) if rule else pd.Series(False, index=part.index)
    if not (bad_volume.any() or bad_amount.any() or status.eq('0').any()):
        return []
    masks = [('SUSPENDED', status.eq('0'), 'unsupported', '来源确认停牌；当前 V1 不支持停牌处理'),
             ('TRADING_STATUS_UNKNOWN', bad_volume & ~status.isin(['0','1']), 'verify', '成交量无效，需核实交易状态'),
             ('UNTRADABLE', bad_volume & status.eq('1'), 'download', '正常交易日成交量无效'),
             ('MISSING_FACTOR_FIELD', bad_amount & ~status.eq('0') & ~(bad_volume & ~status.isin(['0','1'])), 'download', '缺少规则所需有效成交额')]
    out = []
    for code, mask, resolution, label in masks:
        if not mask.any():
            continue
        rows = part[mask]
        for phase, group in rows.groupby(rows.date.map(lambda d: 'warmup' if str(d.date()) < dates['signal_date'] else 'signal' if str(d.date()) == dates['signal_date'] else 'entry' if str(d.date()) == dates['entry_date'] else 'exit' if str(d.date()) == dates['exit_date'] else 'holding')):
            evidence = {'status_source': 'provider' if code != 'TRADING_STATUS_UNKNOWN' else 'unverified',
                        'fields': ['volume', 'amount'] if rule else ['volume'],
                        'rows': [{'date': str(row.date.date()), 'tradestatus': str(row.get('tradestatus', 'unknown')),
                                  'volume': None if pd.isna(row.get('volume')) else float(row.volume),
                                  'amount': None if pd.isna(row.get('amount')) else float(row.amount)} for _, row in group.iterrows()]}
            known_event = code == 'SUSPENDED' and policy == 'phase-aware-v1'
            action = {'warmup': '已确认停牌；按市场交易日窗口计入零成交，保留原始空值',
                      'signal': '信号日停牌，不具备本次候选资格',
                      'entry': '建仓日停牌，买入未成交，预定权重资金保留现金',
                      'holding': '持有期间停牌，禁止成交；使用最近可靠估值并标注陈旧天数',
                      'exit': '结束日停牌，无法退出；保留未实现持仓并分别展示估值与已实现收益'}[phase] if known_event else ''
            event_label = '来源确认停牌' if known_event else label
            out.append(make_issue(code, f'{symbol} {event_label}（{len(group)} 个交易日）', action, symbol=symbol,
                                  dates=group.date, phase=phase, resolution='none' if known_event else resolution,
                                  severity='info' if known_event else 'blocking', evidence=evidence))
    return out


def finalize_readiness(result):
    actions = []
    dates = result.get('dates', {})
    coverage = {r['symbol']: r for r in result.get('coverage', [])}
    for issue in result['issues']:
        if issue.get('severity', 'blocking') != 'blocking':
            continue
        resolution = issue.get('resolution')
        if resolution not in {'download', 'verify'}:
            continue
        code, symbol = issue['code'], issue.get('symbol')
        kind = ('verify_status' if resolution == 'verify' else 'calendar' if code == 'CALENDAR_UNAVAILABLE'
                else 'universe' if code in {'IDENTITY_UNAVAILABLE','PIT_UNAVAILABLE'} else 'entry' if code == 'EXECUTION_PRICE_UNAVAILABLE' else 'bars')
        ranges = issue.get('date_ranges', [])
        start = dates.get('warmup_start_date')
        end = dates.get('exit_date')
        item = coverage.get(symbol, {})
        if symbol and not item.get('selected', True):
            end = dates.get('signal_date')
        if kind == 'verify_status' and ranges:
            start, end = ranges[0]['start'], ranges[-1]['end']
        if kind == 'entry':
            start = end = dates.get('entry_date')
        if kind == 'bars' and code == 'MISSING_BARS' and item.get('adjustment', 'hfq') == 'hfq' and ranges:
            start, end = ranges[0]['start'], ranges[-1]['end']
        # Never query dates beyond a verified listing boundary.
        if item.get('listed_date') and start:
            start = max(start, item['listed_date'])
        if item.get('last_required_date') and end:
            end = min(end, item['last_required_date'])
        action = {'symbol': symbol if kind not in {'calendar','universe'} else None,
                  'kind': kind, 'start': start, 'end': end}
        if action not in actions:
            actions.append(action)
    # Coalesce overlapping stock requests so field diagnostics do not redownload a shard.
    merged = {}
    for action in actions:
        key = (action['symbol'], action['kind'])
        if key not in merged:
            merged[key] = action.copy()
        elif action['start'] and merged[key]['start']:
            merged[key]['start'] = min(merged[key]['start'], action['start'])
            merged[key]['end'] = max(merged[key]['end'], action['end'])
    actions = list(merged.values())
    plan = {'actions': actions, 'executable_count': len(actions)}
    plan['plan_id'] = json_hash({'actions': actions, 'requirement': result['requirement_id'],
                                 'data': result.get('data_identity'), 'issues': result['issues']})
    result.update(schema_version=2, repair_plan=plan)
    return result
